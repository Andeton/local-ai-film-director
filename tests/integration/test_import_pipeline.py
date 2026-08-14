"""Integration tests for the Import / Normalization Service (M1.F).

Covers: basic import, provenance, character turnaround dedup, idempotent
reimport, atomic rollback, change detection (added/modified/deleted),
side-effect-free check, apply_detected_changes marks OUTDATED, reimport
clears outdated, reimport marks deleted-upstream OUTDATED.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    CharacterRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
)
from film_director.errors import NormalizationError
from film_director.services.import_service import ImportService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, wc_db_path):
    db = Database(str(tmp_path / "our.db"))
    db.init_schema()
    adapter = WindComicAdapter(wc_db_path)
    return dict(
        adapter=adapter,
        db=db,
        wc_db_path=wc_db_path,
        project=ProjectRepository(db),
        sequence=SequenceRepository(db),
        scene=SceneRepository(db),
        character=CharacterRepository(db),
    )


@pytest.fixture
def svc(env):
    return ImportService(
        adapter=env["adapter"],
        project_repo=env["project"],
        sequence_repo=env["sequence"],
        scene_repo=env["scene"],
        character_repo=env["character"],
    )


# ---------------------------------------------------------------------------
# 1. Basic Import
# ---------------------------------------------------------------------------

class TestBasicImport:
    def test_basic_import_counts(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        assert r.scenes_imported == 2
        assert r.characters_imported == 2

    def test_project_persisted(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        p = env["project"].get_project(r.project_id)
        assert p is not None
        assert p.title == "The Abandoned Hospital"
        assert p.status == "active"

    def test_sequence_created(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        assert len(seqs) == 1
        assert seqs[0].name == "Main"

    def test_scenes_persisted(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert len(scenes) == 2
        names = {s.name for s in scenes}
        assert "Hospital Exterior" in names
        assert "Hospital Lobby" in names

    def test_characters_persisted(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        assert len(chars) == 2
        names = {c.name for c in chars}
        assert "Detective" in names
        assert "Mysterious Woman" in names


# ---------------------------------------------------------------------------
# 2. Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_project_provenance(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        p = env["project"].get_project(r.project_id)
        assert p.provenance.source_system == "wind_comic"
        assert p.provenance.source_project_id == wc_project_id
        assert len(p.provenance.source_hash) == 64  # SHA-256

    def test_scene_provenance(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        for scene in scenes:
            assert scene.provenance.source_system == "wind_comic"
            assert len(scene.provenance.source_hash) == 64

    def test_character_provenance(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        for ch in chars:
            assert ch.provenance.source_system == "wind_comic"
            assert len(ch.provenance.source_hash) == 64


# ---------------------------------------------------------------------------
# 3. Character Turnaround Paths Deduplication
# ---------------------------------------------------------------------------

class TestCharacterTurnaroundPaths:
    def test_persistent_url_only(self, svc, env, wc_project_id):
        """Character with no media_urls but persistent_url -> [persistent_url]."""
        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        mw = next(c for c in chars if c.name == "Mysterious Woman")
        # media_urls=[], persistent_url=None -> []
        assert mw.turnaround_paths == []

    def test_media_urls_and_persistent_url(self, svc, env, wc_project_id):
        """Detective has media_urls + persistent_url -> combined, deduped."""
        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        det = next(c for c in chars if c.name == "Detective")
        assert det.turnaround_paths == ["ref/det_front.png", "/persist/det.png"]

    def test_dedup_when_persistent_url_in_media_urls(self, svc, env, wc_project_id):
        """If persistent_url is already in media_urls, don't duplicate."""
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET media_urls=?, persistent_url=? WHERE id='asset_char_001'",
            (json.dumps(["/persist/det.png", "other.png"]), "/persist/det.png"),
        )
        conn.commit()
        conn.close()

        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        det = next(c for c in chars if c.name == "Detective")
        assert det.turnaround_paths == ["/persist/det.png", "other.png"]

    def test_stable_order(self, svc, env, wc_project_id):
        """Order is: media_urls first, then persistent_url appended."""
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET media_urls=?, persistent_url=? WHERE id='asset_char_001'",
            (json.dumps(["a.png", "b.png"]), "c.png"),
        )
        conn.commit()
        conn.close()

        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        det = next(c for c in chars if c.name == "Detective")
        assert det.turnaround_paths == ["a.png", "b.png", "c.png"]

    def test_face_ref_path_is_none(self, svc, env, wc_project_id):
        """face_ref_path deferred to M5."""
        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        assert all(c.face_ref_path is None for c in chars)

    def test_visual_anchors_empty(self, svc, env, wc_project_id):
        """visual_anchors has no WC source in M1."""
        r = svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        assert all(c.visual_anchors == [] for c in chars)


# ---------------------------------------------------------------------------
# 4. Idempotent Reimport
# ---------------------------------------------------------------------------

class TestIdempotentReimport:
    def test_same_project_id(self, svc, env, wc_project_id):
        r1 = svc.import_project(wc_project_id)
        r2 = svc.import_project(wc_project_id)
        assert r1.project_id == r2.project_id

    def test_no_duplicate_projects(self, svc, env, wc_project_id):
        svc.import_project(wc_project_id)
        svc.import_project(wc_project_id)
        assert len(env["project"].list_projects()) == 1

    def test_no_duplicate_scenes(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert len(scenes) == 2

    def test_no_duplicate_characters(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        assert len(chars) == 2

    def test_scene_ids_stable(self, svc, env, wc_project_id):
        r1 = svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r1.project_id)
        scenes1 = {s.wc_scene_id: s.id for s in env["scene"].get_scenes_by_sequence(seqs[0].id)}
        svc.import_project(wc_project_id)
        scenes2 = {s.wc_scene_id: s.id for s in env["scene"].get_scenes_by_sequence(seqs[0].id)}
        assert scenes1 == scenes2

    def test_character_ids_stable(self, svc, env, wc_project_id):
        r1 = svc.import_project(wc_project_id)
        chars1 = {c.wc_character_id: c.id for c in env["character"].get_characters_by_project(r1.project_id)}
        svc.import_project(wc_project_id)
        chars2 = {c.wc_character_id: c.id for c in env["character"].get_characters_by_project(r1.project_id)}
        assert chars1 == chars2


# ---------------------------------------------------------------------------
# 5. Atomic Rollback on Failure
# ---------------------------------------------------------------------------

class TestAtomicRollback:
    def test_no_partial_import_on_scene_save_failure(self, svc, env, wc_project_id):
        """If scene save fails mid-import, nothing should be persisted."""
        original_save = env["scene"].save_scene

        call_count = 0

        def failing_save(scene, conn=None):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise RuntimeError("Simulated failure")
            return original_save(scene, conn=conn)

        with patch.object(env["scene"], "save_scene", side_effect=failing_save):
            with pytest.raises((RuntimeError, NormalizationError)):
                svc.import_project(wc_project_id)

        # Nothing should be persisted
        assert len(env["project"].list_projects()) == 0


# ---------------------------------------------------------------------------
# 6. Change Detection
# ---------------------------------------------------------------------------

class TestChangeDetectionModified:
    def test_no_changes_after_import(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        assert svc.check_for_changes(r.project_id) == []

    def test_scene_description_modified(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "CHANGED", "location": "X"}),),
        )
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        assert any(c.entity_type == "scene" and c.change_type == "modified" for c in changes)

    def test_character_media_url_modified(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET media_urls=? WHERE id='asset_char_001'",
            (json.dumps(["new.png"]),),
        )
        conn.commit()
        conn.close()
        assert any(
            c.entity_type == "character" and c.change_type == "modified"
            for c in svc.check_for_changes(r.project_id)
        )

    def test_project_title_modified(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("UPDATE projects SET title='New' WHERE id=?", (wc_project_id,))
        conn.commit()
        conn.close()
        assert any(
            c.entity_type == "project" and c.change_type == "modified"
            for c in svc.check_for_changes(r.project_id)
        )


class TestChangeDetectionAdded:
    def test_new_scene_detected(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "INSERT INTO project_assets (id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale)"
            " VALUES ('new_scene',?,'scene','New Room',?,'[]',NULL,NULL,1,0,0)",
            (wc_project_id, json.dumps({"description": "new", "location": "new"})),
        )
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        added = [c for c in changes if c.change_type == "added" and c.entity_type == "scene"]
        assert len(added) == 1
        assert added[0].entity_id is None

    def test_new_character_detected(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "INSERT INTO project_assets (id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale)"
            " VALUES ('new_char',?,'character','Nurse',?,'[]',NULL,NULL,1,0,0)",
            (wc_project_id, json.dumps({"description": "nurse", "appearance": "scrubs"})),
        )
        conn.commit()
        conn.close()
        assert any(
            c.change_type == "added" and c.entity_type == "character"
            for c in svc.check_for_changes(r.project_id)
        )


class TestChangeDetectionDeleted:
    def test_deleted_scene_detected(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_scene_001'")
        conn.commit()
        conn.close()
        assert any(
            c.change_type == "deleted" and c.entity_type == "scene"
            for c in svc.check_for_changes(r.project_id)
        )

    def test_deleted_character_detected(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_char_001'")
        conn.commit()
        conn.close()
        assert any(
            c.change_type == "deleted" and c.entity_type == "character"
            for c in svc.check_for_changes(r.project_id)
        )


# ---------------------------------------------------------------------------
# 7. check_for_changes is Side-Effect-Free
# ---------------------------------------------------------------------------

class TestSideEffectFree:
    def test_check_does_not_mutate_status(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "X", "location": "Y"}),),
        )
        conn.commit()
        conn.close()

        # Call check twice — should not change state
        svc.check_for_changes(r.project_id)
        svc.check_for_changes(r.project_id)

        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert all(s.status != "outdated" for s in scenes)

        p = env["project"].get_project(r.project_id)
        assert p.status == "active"


# ---------------------------------------------------------------------------
# 8. apply_detected_changes marks OUTDATED
# ---------------------------------------------------------------------------

class TestApplyChanges:
    def test_modified_scene_marked_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "X", "location": "Y"}),),
        )
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert any(s.status == "outdated" for s in scenes)

    def test_modified_character_marked_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET media_urls=? WHERE id='asset_char_001'",
            (json.dumps(["new.png"]),),
        )
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        chars = env["character"].get_characters_by_project(r.project_id)
        assert any(c.status == "outdated" for c in chars)

    def test_deleted_scene_marked_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_scene_001'")
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert any(s.status == "outdated" for s in scenes)


# ---------------------------------------------------------------------------
# 9. apply added -> project OUTDATED
# ---------------------------------------------------------------------------

class TestApplyAdded:
    def test_added_marks_project_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "INSERT INTO project_assets (id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale)"
            " VALUES ('new',?,'scene','X',?,'[]',NULL,NULL,1,0,0)",
            (wc_project_id, json.dumps({})),
        )
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        assert env["project"].get_project(r.project_id).status == "outdated"


# ---------------------------------------------------------------------------
# 10. Reimport after apply clears outdated
# ---------------------------------------------------------------------------

class TestReimportClearsOutdated:
    def test_reimport_clears_outdated_scene(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "NEW", "location": "NEW"}),),
        )
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        # Now reimport
        svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert all(s.status == "draft" for s in scenes)

    def test_reimport_clears_outdated_project(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        # Trigger add
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute(
            "INSERT INTO project_assets (id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale)"
            " VALUES ('new2',?,'scene','Z',?,'[]',NULL,NULL,1,0,0)",
            (wc_project_id, json.dumps({})),
        )
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        assert env["project"].get_project(r.project_id).status == "outdated"
        # Reimport should restore active
        svc.import_project(wc_project_id)
        assert env["project"].get_project(r.project_id).status == "active"


# ---------------------------------------------------------------------------
# 11. Reimport marks deleted-upstream entities OUTDATED
# ---------------------------------------------------------------------------

class TestReimportDeletedUpstream:
    def test_deleted_upstream_stays_outdated_after_reimport(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_scene_001'")
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        outdated = [s for s in scenes if s.status == "outdated"]
        assert len(outdated) == 1  # deleted scene remains outdated

    def test_deleted_character_stays_outdated_after_reimport(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_char_001'")
        conn.commit()
        conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        outdated = [c for c in chars if c.status == "outdated"]
        assert len(outdated) == 1


# ---------------------------------------------------------------------------
# 12. Direct reimport after upstream delete (without prior apply)
# ---------------------------------------------------------------------------

class TestDirectReimportAfterDelete:
    def test_direct_reimport_marks_deleted_scene_outdated(self, svc, env, wc_project_id):
        """Reimport directly after upstream deletion (no apply step) should mark OUTDATED."""
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_scene_001'")
        conn.commit()
        conn.close()
        # Reimport directly — no check/apply step
        svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        outdated = [s for s in scenes if s.status == "outdated"]
        assert len(outdated) == 1

    def test_direct_reimport_marks_deleted_character_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_char_001'")
        conn.commit()
        conn.close()
        svc.import_project(wc_project_id)
        chars = env["character"].get_characters_by_project(r.project_id)
        outdated = [c for c in chars if c.status == "outdated"]
        assert len(outdated) == 1
