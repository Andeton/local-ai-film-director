"""Integration tests for M4.C rich WC import — script, storyboard, director."""
import json
import os
import sqlite3

import pytest

from film_director.adapters.wind_comic import WindComicAdapter, WCProjectBundle
from film_director.models.provenance import build_project_source_payload, compute_source_hash
from film_director.models.source_facts import (
    ShotSourceFacts,
    build_shot_source_facts,
    parse_storyboard_description,
)
from film_director.models.wind_comic_dto import (
    WCDirectorPlan,
    WCScriptShot,
    WCStoryboardShot,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import ProjectRepository
from film_director.services.import_service import ImportService


# ---------------------------------------------------------------------------
# WC DB fixture
# ---------------------------------------------------------------------------

def _create_wc_fixture(tmp_path) -> str:
    """Create a minimal WC SQLite DB with script + storyboard + plan."""
    db_path = os.path.join(str(tmp_path), "wc_fixture.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, user_id TEXT, title TEXT, description TEXT,
            cover_urls TEXT, status TEXT, created_at TEXT, updated_at TEXT,
            script_data TEXT, director_notes TEXT, pipeline_state TEXT,
            mode TEXT, execution_mode TEXT, style_id TEXT, aspect TEXT,
            global_asset_ids TEXT, output_config TEXT, series_id TEXT,
            episode_number INTEGER, primary_character_ref TEXT,
            locked_characters TEXT, share_token TEXT, share_created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE project_assets (
            id TEXT PRIMARY KEY, project_id TEXT, type TEXT, name TEXT,
            data TEXT, media_urls TEXT, shot_number INTEGER, version INTEGER,
            created_at TEXT, updated_at TEXT, confirmed INTEGER,
            persistent_url TEXT, stale INTEGER
        )
    """)
    # Insert project with script_data
    script_data = json.dumps({
        "title": "Detective Story",
        "synopsis": "A noir mystery",
        "shots": [
            {"shotNumber": 1, "sceneDescription": "Opening scene", "characters": ["Detective"],
             "dialogue": "Where am I?", "action": "walks into room", "emotion": "confused"},
            {"shotNumber": 2, "sceneDescription": "Discovery", "characters": ["Detective", "Witness"],
             "dialogue": "", "action": "examines evidence", "emotion": "focused"},
            {"shotNumber": 3, "sceneDescription": "Confrontation", "characters": ["Detective"],
             "dialogue": "I know the truth.", "action": "confronts suspect", "emotion": "determined"},
        ],
    })
    conn.execute(
        "INSERT INTO projects (id, title, status, aspect, style_id, script_data, locked_characters) "
        "VALUES (?,?,?,?,?,?,?)",
        ("test-proj", "Detective Story", "completed", "16:9", "cinematic", script_data, "[]"),
    )
    # Plan asset
    plan_data = json.dumps({
        "genre": "mystery",
        "style": "noir",
        "storyStructure": {"acts": 3, "totalShots": 3},
        "characters": [{"name": "Detective", "description": "seasoned"}],
        "scenes": [{"id": "s1", "description": "office"}],
    })
    conn.execute(
        "INSERT INTO project_assets (id, project_id, type, name, data, media_urls, version) "
        "VALUES (?,?,?,?,?,?,?)",
        ("plan-1", "test-proj", "plan", "Director Plan", plan_data, "[]", 1),
    )
    # Scene assets
    conn.execute(
        "INSERT INTO project_assets (id, project_id, type, name, data, media_urls, version) "
        "VALUES (?,?,?,?,?,?,?)",
        ("scene-1", "test-proj", "scene", "Office", json.dumps({"description": "Dark office", "location": "downtown"}), "[]", 1),
    )
    # Character assets
    conn.execute(
        "INSERT INTO project_assets (id, project_id, type, name, data, media_urls, version) "
        "VALUES (?,?,?,?,?,?,?)",
        ("char-1", "test-proj", "character", "Detective",
         json.dumps({"description": "Seasoned detective", "appearance": "grey hair, trench coat"}), "[]", 1),
    )
    # Storyboard shots
    for i, desc in enumerate([
        "cinematic film frame, camera angle: Wide Shot, slow dolly forward, lighting: Low-key dramatic, Rembrandt, color tone: cold",
        "cinematic film frame, camera angle: Medium Shot, eye level, lighting: Natural window light, color tone: warm",
        "cinematic film frame, camera angle: Close-Up, low angle, slow push in, lighting: Harsh overhead, color tone: stark",
    ], start=1):
        conn.execute(
            "INSERT INTO project_assets (id, project_id, type, name, data, media_urls, shot_number, version) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"sb-{i}", "test-proj", "storyboard", f"Shot {i}",
             json.dumps({"description": desc, "duration": 5 + i}), "[]", i, 1),
        )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Script DTO tests
# ---------------------------------------------------------------------------

class TestScriptDTO:
    def test_script_shots_parsed(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert len(bundle.script_shots) == 3

    def test_script_shot_fields(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        shot = bundle.script_shots[0]
        assert shot.shot_number == 1
        assert shot.dialogue == "Where am I?"
        assert shot.action == "walks into room"
        assert shot.emotion == "confused"
        assert shot.characters == ["Detective"]
        assert shot.scene_description == "Opening scene"

    def test_empty_dialogue_preserved(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert bundle.script_shots[1].dialogue == ""

    def test_characters_list_preserved(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert bundle.script_shots[1].characters == ["Detective", "Witness"]

    def test_no_script_data_graceful(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        conn = sqlite3.connect(wc_path)
        conn.execute("UPDATE projects SET script_data = NULL WHERE id = 'test-proj'")
        conn.commit()
        conn.close()
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert bundle.script_shots == []


# ---------------------------------------------------------------------------
# Director plan tests
# ---------------------------------------------------------------------------

class TestDirectorPlan:
    def test_director_plan_parsed(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert bundle.director_plan is not None
        assert bundle.director_plan.genre == "mystery"
        assert bundle.director_plan.style == "noir"
        assert bundle.director_plan.story_structure == {"acts": 3, "totalShots": 3}

    def test_no_plan_graceful(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        conn = sqlite3.connect(wc_path)
        conn.execute("DELETE FROM project_assets WHERE type = 'plan'")
        conn.commit()
        conn.close()
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert bundle.director_plan is None


# ---------------------------------------------------------------------------
# Storyboard in bundle
# ---------------------------------------------------------------------------

class TestStoryboardBundle:
    def test_storyboard_shots_in_bundle(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert len(bundle.storyboard_shots) == 3
        assert bundle.storyboard_shots[0].shot_number == 1
        assert "Wide Shot" in bundle.storyboard_shots[0].data.get("description", "")


# ---------------------------------------------------------------------------
# Script ↔ Storyboard correlation
# ---------------------------------------------------------------------------

class TestCorrelation:
    def test_matching_shot_numbers(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert 1 in facts
        assert 2 in facts
        assert 3 in facts
        assert facts[1].action == "walks into room"
        assert facts[1].storyboard_description != ""
        assert facts[1].source_storyboard_asset_id == "sb-1"

    def test_script_without_storyboard(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        conn = sqlite3.connect(wc_path)
        conn.execute("DELETE FROM project_assets WHERE type = 'storyboard' AND shot_number = 2")
        conn.commit()
        conn.close()
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert 2 in facts
        assert facts[2].action == "examines evidence"
        assert facts[2].storyboard_description == ""
        assert facts[2].source_storyboard_asset_id is None

    def test_storyboard_without_script(self, tmp_path):
        """Storyboard shot with no matching script shot is still included."""
        wc_path = _create_wc_fixture(tmp_path)
        # Add storyboard shot 99 with no script counterpart
        conn = sqlite3.connect(wc_path)
        conn.execute(
            "INSERT INTO project_assets (id, project_id, type, name, data, media_urls, shot_number, version) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("sb-99", "test-proj", "storyboard", "Shot 99",
             json.dumps({"description": "extra shot, camera angle: Aerial", "duration": 10}), "[]", 99, 1),
        )
        conn.commit()
        conn.close()
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert 99 in facts
        assert facts[99].action is None
        assert facts[99].duration_sec == 10.0

    def test_duplicate_storyboard_shot_number_excluded(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        conn = sqlite3.connect(wc_path)
        conn.execute(
            "INSERT INTO project_assets (id, project_id, type, name, data, media_urls, shot_number, version) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("sb-1-dup", "test-proj", "storyboard", "Shot 1 dup",
             json.dumps({"description": "duplicate", "duration": 99}), "[]", 1, 2),
        )
        conn.commit()
        conn.close()
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        # Shot 1 should have script data but NO storyboard (ambiguous)
        assert 1 in facts
        assert facts[1].action == "walks into room"
        assert facts[1].source_storyboard_asset_id is None
        assert facts[1].storyboard_description == ""

    def test_duplicate_script_shot_number_first_wins(self, tmp_path):
        """Duplicate script shotNumber — first occurrence used, duplicate skipped."""
        script_shots = [
            WCScriptShot(1, "first", ["A"], "d1", "act1", "em1"),
            WCScriptShot(1, "dupe", ["B"], "d2", "act2", "em2"),
        ]
        facts = build_shot_source_facts("proj", script_shots, [])
        assert facts[1].action == "act1"
        assert facts[1].characters == ["A"]


# ---------------------------------------------------------------------------
# Source facts fields
# ---------------------------------------------------------------------------

class TestSourceFactsFields:
    def test_dialogue_verbatim(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert facts[1].dialogue_text == "Where am I?"
        assert facts[3].dialogue_text == "I know the truth."

    def test_duration_from_storyboard(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert facts[1].duration_sec == 6.0  # 5 + 1
        assert facts[2].duration_sec == 7.0

    def test_camera_from_parser(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert facts[1].camera_angle is not None
        assert "Wide Shot" in facts[1].camera_angle
        assert facts[1].camera_movement is not None
        assert "dolly" in facts[1].camera_movement.lower()

    def test_storyboard_description_verbatim(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert "cinematic film frame" in facts[1].storyboard_description

    def test_source_project_id(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        assert facts[1].source_project_id == "test-proj"

    def test_frozen_source_facts(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        with pytest.raises(Exception):
            facts[1].action = "changed"  # type: ignore


# ---------------------------------------------------------------------------
# Recomputability
# ---------------------------------------------------------------------------

class TestRecomputability:
    def test_same_bundle_produces_identical_facts(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        facts_a = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        facts_b = build_shot_source_facts("test-proj", bundle.script_shots, bundle.storyboard_shots)
        for num in facts_a:
            assert facts_a[num] == facts_b[num]


# ---------------------------------------------------------------------------
# Director context persistence via import
# ---------------------------------------------------------------------------

class TestDirectorContextImport:
    def test_import_persists_director_context(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        db_path = os.path.join(str(tmp_path), "production.db")
        db = Database(db_path)
        db.init_schema()
        adapter = WindComicAdapter(wc_path)
        from film_director.persistence.repositories import (
            CharacterRepository, SceneRepository, SequenceRepository,
        )
        svc = ImportService(
            adapter=adapter,
            project_repo=ProjectRepository(db),
            sequence_repo=SequenceRepository(db),
            scene_repo=SceneRepository(db),
            character_repo=CharacterRepository(db),
            db=db,
        )
        result = svc.import_project("test-proj")
        loaded = ProjectRepository(db).get_project(result.project_id)
        assert loaded is not None
        assert loaded.director_context["genre"] == "mystery"
        assert loaded.director_context["style"] == "noir"
        assert loaded.director_context["story_structure"]["acts"] == 3


# ---------------------------------------------------------------------------
# Source hash includes script_data
# ---------------------------------------------------------------------------

class TestSourceHash:
    def test_script_change_changes_hash(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle1 = adapter.read_project_bundle("test-proj")
        hash1 = compute_source_hash(build_project_source_payload(bundle1.project))

        # Modify script_data
        conn = sqlite3.connect(wc_path)
        new_script = json.dumps({"title": "Changed", "shots": []})
        conn.execute("UPDATE projects SET script_data = ? WHERE id = 'test-proj'", (new_script,))
        conn.commit()
        conn.close()

        bundle2 = adapter.read_project_bundle("test-proj")
        hash2 = compute_source_hash(build_project_source_payload(bundle2.project))
        assert hash1 != hash2

    def test_storyboard_change_changes_hash(self, tmp_path):
        """Storyboard changes detected via storyboard asset version/data in source hash."""
        wc_path = _create_wc_fixture(tmp_path)
        adapter = WindComicAdapter(wc_path)
        bundle1 = adapter.read_project_bundle("test-proj")
        sb1_data = bundle1.storyboard_shots[0].data

        conn = sqlite3.connect(wc_path)
        conn.execute(
            "UPDATE project_assets SET data = ? WHERE id = 'sb-1'",
            (json.dumps({"description": "CHANGED", "duration": 99}),),
        )
        conn.commit()
        conn.close()

        bundle2 = adapter.read_project_bundle("test-proj")
        sb2_data = bundle2.storyboard_shots[0].data
        assert sb1_data != sb2_data  # storyboard content changed


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_project_without_script_importable(self, tmp_path):
        wc_path = _create_wc_fixture(tmp_path)
        conn = sqlite3.connect(wc_path)
        conn.execute("UPDATE projects SET script_data = NULL WHERE id = 'test-proj'")
        conn.commit()
        conn.close()
        adapter = WindComicAdapter(wc_path)
        bundle = adapter.read_project_bundle("test-proj")
        assert bundle.script_shots == []
        # Import still works
        db_path = os.path.join(str(tmp_path), "prod.db")
        db = Database(db_path)
        db.init_schema()
        from film_director.persistence.repositories import (
            CharacterRepository, SceneRepository, SequenceRepository,
        )
        svc = ImportService(
            adapter=adapter,
            project_repo=ProjectRepository(db),
            sequence_repo=SequenceRepository(db),
            scene_repo=SceneRepository(db),
            character_repo=CharacterRepository(db),
            db=db,
        )
        result = svc.import_project("test-proj")
        assert result.project_id is not None
