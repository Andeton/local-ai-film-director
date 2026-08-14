"""Tests for M2 repositories — Beat, Shot, GenerationPlan (Task 2 / M2.B).

TDD: written before implementation. Tests cover:
1. UPSERT semantics (save, modify, save again → 1 row, updated fields)
2. JSON round-trip for all serialized fields
3. Current vs Historical queries
4. Shot version invariant (plan targeting old shot_version excluded)
5. mark_outdated (individual + scope-based, version NOT incremented)
6. FK violation
7. Shared transaction rollback
8. Restart persistence
9. get_current_shots_by_project (excludes outdated shots AND shots on outdated beats)
10. get_character by ID
"""
import sqlite3

import pytest

from film_director.models.canonical import (
    Beat,
    CameraIntent,
    CharacterReference,
    GenerationPlan,
    ProductionProject,
    ReferenceRequirements,
    Scene,
    Sequence,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prov(**kw) -> Provenance:
    d = dict(
        source_system="wind_comic",
        source_project_id="p",
        source_asset_id="a",
        source_asset_version=1,
        imported_at="t",
        source_hash="a" * 64,
    )
    d.update(kw)
    return Provenance(**d)


def _project(id="p1", wc="wc1", title="Film", **kw) -> ProductionProject:
    return ProductionProject(
        id=id, wc_project_id=wc, title=title,
        created_at="t", updated_at="t",
        provenance=_prov(source_asset_id=wc),
        **kw,
    )


def _sequence(id="sq1", project_id="p1", name="Seq", order_index=0) -> Sequence:
    return Sequence(id=id, project_id=project_id, name=name, order_index=order_index)


def _scene(id="s1", sequence_id="sq1", wc_scene_id="ws1", name="Scene", **kw) -> Scene:
    return Scene(
        id=id, sequence_id=sequence_id, wc_scene_id=wc_scene_id,
        name=name, location="L", description="D", order_index=0,
        provenance=_prov(),
        **kw,
    )


def _beat(id="b1", scene_id="s1", **kw) -> Beat:
    defaults = dict(
        dramatic_action="Hero enters",
        character_intention="Seek truth",
        change="Discovers clue",
        characters=["char-1", "char-2"],
        order_index=0,
        status="draft",
        source="llm",
        version=1,
        created_at="t",
        updated_at="t",
    )
    defaults.update(kw)
    return Beat(id=id, scene_id=scene_id, **defaults)


def _shot(id="sh1", beat_id="b1", **kw) -> ShotSpecificationV1:
    defaults = dict(
        dramatic_purpose="Reveal hero",
        subjects=[
            ShotSubject(character_id="c1", name="Hero", ref_images=["ref1.png"]),
        ],
        action="Hero walks in",
        environment={"setting": "forest", "weather": "rain"},
        camera=CameraIntent(shot_size="medium", angle="eye_level", movement="dolly_in"),
        lighting={"key": "low", "mood": "dramatic"},
        audio_intent={"music": "tense", "sfx": ["footsteps"]},
        duration_sec=4.5,
        continuity_inputs={"prev_shot": "sh0", "match_on": "costume"},
        order_index=0,
        status="draft",
        source="generated",
        version=1,
        created_at="t",
        updated_at="t",
    )
    defaults.update(kw)
    return ShotSpecificationV1(id=id, beat_id=beat_id, **defaults)


def _plan(id="gp1", shot_id="sh1", **kw) -> GenerationPlan:
    defaults = dict(
        shot_version=1,
        strategy="TEXT_TO_VIDEO",
        reference_requirements=ReferenceRequirements(
            character_refs=True, scene_ref=False, prev_frame=True, style_ref=False,
        ),
        duration_sec=4.5,
        resolution_intent={"width": 1280, "height": 720},
        seed_policy="random",
        seed=None,
        continuity_mode="none",
        selection_reason="No storyboard image",
        status="draft",
        version=1,
        created_at="t",
        updated_at="t",
    )
    defaults.update(kw)
    return GenerationPlan(id=id, shot_id=shot_id, **defaults)


# ---------------------------------------------------------------------------
# Fixtures — full FK hierarchy: project → sequence → scene → beat → shot → plan
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def hierarchy(db):
    """Create full FK parent hierarchy and return all repos."""
    proj_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)
    char_repo = CharacterRepository(db)

    proj_repo.save_project(_project())
    seq_repo.save_sequence(_sequence())
    scene_repo.save_scene(_scene())

    return {
        "db": db,
        "proj_repo": proj_repo,
        "seq_repo": seq_repo,
        "scene_repo": scene_repo,
        "beat_repo": beat_repo,
        "shot_repo": shot_repo,
        "plan_repo": plan_repo,
        "char_repo": char_repo,
    }


@pytest.fixture
def beat_repo(hierarchy):
    return hierarchy["beat_repo"]


@pytest.fixture
def shot_repo(hierarchy):
    return hierarchy["shot_repo"]


@pytest.fixture
def plan_repo(hierarchy):
    return hierarchy["plan_repo"]


# ---------------------------------------------------------------------------
# 1. UPSERT
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_beat_upsert(self, beat_repo):
        beat_repo.save_beat(_beat(dramatic_action="V1"))
        beat_repo.save_beat(_beat(dramatic_action="V2"))
        b = beat_repo.get_beat("b1")
        assert b is not None
        assert b.dramatic_action == "V2"
        all_beats = beat_repo.get_beats_by_scene("s1")
        assert len(all_beats) == 1

    def test_shot_upsert(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot(dramatic_purpose="V1"))
        shot_repo.save_shot(_shot(dramatic_purpose="V2"))
        s = shot_repo.get_shot("sh1")
        assert s is not None
        assert s.dramatic_purpose == "V2"
        all_shots = shot_repo.get_shots_by_beat("b1")
        assert len(all_shots) == 1

    def test_plan_upsert(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot())
        plan_repo.save_plan(_plan(selection_reason="V1"))
        plan_repo.save_plan(_plan(selection_reason="V2"))
        plans = plan_repo.get_plans_by_shot("sh1")
        assert len(plans) == 1
        assert plans[0].selection_reason == "V2"


# ---------------------------------------------------------------------------
# 2. JSON round-trip
# ---------------------------------------------------------------------------

class TestJsonRoundtrip:
    def test_beat_characters(self, beat_repo):
        chars = ["alice", "bob", "charlie"]
        beat_repo.save_beat(_beat(characters=chars))
        b = beat_repo.get_beat("b1")
        assert b.characters == chars

    def test_shot_subjects(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        subjects = [
            ShotSubject(character_id="c1", name="Hero", ref_images=["a.png", "b.png"]),
            ShotSubject(character_id="c2", name="Villain", ref_images=[]),
        ]
        shot_repo.save_shot(_shot(subjects=subjects))
        s = shot_repo.get_shot("sh1")
        assert len(s.subjects) == 2
        assert s.subjects[0].character_id == "c1"
        assert s.subjects[0].ref_images == ["a.png", "b.png"]
        assert s.subjects[1].name == "Villain"

    def test_shot_camera(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        cam = CameraIntent(shot_size="close_up", angle="low", movement="pan")
        shot_repo.save_shot(_shot(camera=cam))
        s = shot_repo.get_shot("sh1")
        assert s.camera.shot_size == "close_up"
        assert s.camera.angle == "low"
        assert s.camera.movement == "pan"

    def test_shot_environment(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        env = {"setting": "castle", "time": "night"}
        shot_repo.save_shot(_shot(environment=env))
        s = shot_repo.get_shot("sh1")
        assert s.environment == env

    def test_shot_lighting(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        lighting = {"key": "high", "fill": "soft"}
        shot_repo.save_shot(_shot(lighting=lighting))
        s = shot_repo.get_shot("sh1")
        assert s.lighting == lighting

    def test_shot_audio_intent(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        audio = {"music": "epic", "sfx": ["explosion"]}
        shot_repo.save_shot(_shot(audio_intent=audio))
        s = shot_repo.get_shot("sh1")
        assert s.audio_intent == audio

    def test_shot_continuity_inputs(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        cont = {"prev_shot": "sh0", "match": "wardrobe"}
        shot_repo.save_shot(_shot(continuity_inputs=cont))
        s = shot_repo.get_shot("sh1")
        assert s.continuity_inputs == cont

    def test_plan_reference_requirements(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot())
        rr = ReferenceRequirements(
            character_refs=True, scene_ref=True, prev_frame=False, style_ref=True,
        )
        plan_repo.save_plan(_plan(reference_requirements=rr))
        plans = plan_repo.get_plans_by_shot("sh1")
        assert plans[0].reference_requirements.character_refs is True
        assert plans[0].reference_requirements.scene_ref is True
        assert plans[0].reference_requirements.prev_frame is False
        assert plans[0].reference_requirements.style_ref is True

    def test_plan_resolution_intent(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot())
        res = {"width": 1920, "height": 1080}
        plan_repo.save_plan(_plan(resolution_intent=res))
        plans = plan_repo.get_plans_by_shot("sh1")
        assert plans[0].resolution_intent == res


# ---------------------------------------------------------------------------
# 3. Current vs Historical
# ---------------------------------------------------------------------------

class TestCurrentVsHistorical:
    def test_beat_current_vs_all(self, beat_repo):
        beat_repo.save_beat(_beat(id="b1", order_index=0))
        beat_repo.save_beat(_beat(id="b2", order_index=1, status="outdated"))
        beat_repo.save_beat(_beat(id="b3", order_index=2, status="approved"))
        all_beats = beat_repo.get_beats_by_scene("s1")
        current = beat_repo.get_current_beats_by_scene("s1")
        assert len(all_beats) == 3
        assert len(current) == 2  # b1 (draft) + b3 (approved)
        assert all(b.status != "outdated" for b in current)

    def test_shot_current_vs_all(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot(id="sh1", order_index=0))
        shot_repo.save_shot(_shot(id="sh2", order_index=1, status="outdated"))
        all_shots = shot_repo.get_shots_by_beat("b1")
        current = shot_repo.get_current_shots_by_beat("b1")
        assert len(all_shots) == 2
        assert len(current) == 1
        assert current[0].id == "sh1"

    def test_plan_current_vs_all(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot())
        plan_repo.save_plan(_plan(id="gp1", status="draft"))
        plan_repo.save_plan(_plan(id="gp2", status="outdated"))
        all_plans = plan_repo.get_plans_by_shot("sh1")
        current = plan_repo.get_current_plan_by_shot("sh1")
        assert len(all_plans) == 2
        assert current is not None
        assert current.id == "gp1"


# ---------------------------------------------------------------------------
# 4. Shot version invariant
# ---------------------------------------------------------------------------

class TestShotVersionInvariant:
    def test_plan_targeting_old_shot_version_excluded(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        # Save shot at version 1, then update to version 2
        shot_repo.save_shot(_shot(version=1))
        plan_repo.save_plan(_plan(shot_version=1))
        # Bump the shot version
        shot_repo.save_shot(_shot(version=2))
        # Plan targets version 1 but shot is now version 2
        current = plan_repo.get_current_plan_by_shot("sh1")
        assert current is None  # excluded because version mismatch

    def test_plan_matching_current_shot_version_returned(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot(version=3))
        plan_repo.save_plan(_plan(shot_version=3))
        current = plan_repo.get_current_plan_by_shot("sh1")
        assert current is not None
        assert current.shot_version == 3


# ---------------------------------------------------------------------------
# 5. mark_outdated
# ---------------------------------------------------------------------------

class TestMarkOutdated:
    def test_mark_beat_outdated(self, beat_repo):
        beat_repo.save_beat(_beat(version=5))
        beat_repo.mark_outdated("b1")
        b = beat_repo.get_beat("b1")
        assert b.status == "outdated"
        assert b.version == 5  # NOT incremented

    def test_mark_beats_outdated_by_scene(self, beat_repo):
        beat_repo.save_beat(_beat(id="b1", order_index=0))
        beat_repo.save_beat(_beat(id="b2", order_index=1))
        beat_repo.mark_beats_outdated_by_scene("s1")
        current = beat_repo.get_current_beats_by_scene("s1")
        assert len(current) == 0
        # Content preserved
        assert beat_repo.get_beat("b1").dramatic_action == "Hero enters"

    def test_mark_shot_outdated(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot(version=3))
        shot_repo.mark_outdated("sh1")
        s = shot_repo.get_shot("sh1")
        assert s.status == "outdated"
        assert s.version == 3

    def test_mark_shots_outdated_by_beat(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot(id="sh1", order_index=0))
        shot_repo.save_shot(_shot(id="sh2", order_index=1))
        shot_repo.mark_shots_outdated_by_beat("b1")
        current = shot_repo.get_current_shots_by_beat("b1")
        assert len(current) == 0

    def test_mark_plan_outdated(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot())
        plan_repo.save_plan(_plan(version=2))
        plan_repo.mark_outdated("gp1")
        plans = plan_repo.get_plans_by_shot("sh1")
        assert plans[0].status == "outdated"
        assert plans[0].version == 2

    def test_mark_plan_outdated_by_shot(self, hierarchy):
        beat_repo, shot_repo, plan_repo = (
            hierarchy["beat_repo"], hierarchy["shot_repo"], hierarchy["plan_repo"]
        )
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot())
        plan_repo.save_plan(_plan(id="gp1"))
        plan_repo.save_plan(_plan(id="gp2"))
        plan_repo.mark_plan_outdated_by_shot("sh1")
        current = plan_repo.get_current_plan_by_shot("sh1")
        assert current is None

    def test_repeated_marking_harmless(self, beat_repo):
        beat_repo.save_beat(_beat())
        beat_repo.mark_outdated("b1")
        beat_repo.mark_outdated("b1")  # idempotent
        b = beat_repo.get_beat("b1")
        assert b.status == "outdated"


# ---------------------------------------------------------------------------
# 6. FK violation
# ---------------------------------------------------------------------------

class TestFKViolation:
    def test_beat_with_nonexistent_scene(self, hierarchy):
        beat_repo = hierarchy["beat_repo"]
        with pytest.raises(sqlite3.IntegrityError):
            beat_repo.save_beat(_beat(scene_id="no_such_scene"))

    def test_shot_with_nonexistent_beat(self, hierarchy):
        shot_repo = hierarchy["shot_repo"]
        with pytest.raises(sqlite3.IntegrityError):
            shot_repo.save_shot(_shot(beat_id="no_such_beat"))

    def test_plan_with_nonexistent_shot(self, hierarchy):
        plan_repo = hierarchy["plan_repo"]
        with pytest.raises(sqlite3.IntegrityError):
            plan_repo.save_plan(_plan(shot_id="no_such_shot"))


# ---------------------------------------------------------------------------
# 7. Shared transaction rollback
# ---------------------------------------------------------------------------

class TestSharedTransactionRollback:
    def test_rollback_all_absent(self, hierarchy):
        db = hierarchy["db"]
        beat_repo = hierarchy["beat_repo"]
        shot_repo = hierarchy["shot_repo"]
        plan_repo = hierarchy["plan_repo"]

        try:
            with db.connection() as conn:
                beat_repo.save_beat(_beat(id="b_rb"), conn=conn)
                shot_repo.save_shot(_shot(id="sh_rb", beat_id="b_rb"), conn=conn)
                plan_repo.save_plan(_plan(id="gp_rb", shot_id="sh_rb"), conn=conn)
                raise RuntimeError("intentional failure")
        except RuntimeError:
            pass

        assert beat_repo.get_beat("b_rb") is None
        assert shot_repo.get_shot("sh_rb") is None
        assert plan_repo.get_plans_by_shot("sh_rb") == []


# ---------------------------------------------------------------------------
# 8. Restart persistence
# ---------------------------------------------------------------------------

class TestRestartPersistence:
    def test_hierarchy_survives_restart(self, tmp_path):
        path = str(tmp_path / "restart.db")
        db1 = Database(path)
        db1.init_schema()

        # Save full hierarchy via DB A
        ProjectRepository(db1).save_project(_project())
        SequenceRepository(db1).save_sequence(_sequence())
        SceneRepository(db1).save_scene(_scene())
        BeatRepository(db1).save_beat(_beat())
        ShotRepository(db1).save_shot(_shot())
        GenerationPlanRepository(db1).save_plan(_plan())

        # Read back via DB B on same file
        db2 = Database(path)
        db2.init_schema()

        b = BeatRepository(db2).get_beat("b1")
        assert b is not None
        assert b.dramatic_action == "Hero enters"
        assert b.characters == ["char-1", "char-2"]

        s = ShotRepository(db2).get_shot("sh1")
        assert s is not None
        assert s.camera.shot_size == "medium"
        assert len(s.subjects) == 1

        plans = GenerationPlanRepository(db2).get_plans_by_shot("sh1")
        assert len(plans) == 1
        assert plans[0].reference_requirements.character_refs is True


# ---------------------------------------------------------------------------
# 9. get_current_shots_by_project
# ---------------------------------------------------------------------------

class TestGetCurrentShotsByProject:
    def test_returns_current_shots(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot(id="sh1", order_index=0))
        shot_repo.save_shot(_shot(id="sh2", order_index=1))
        result = shot_repo.get_current_shots_by_project("p1")
        assert len(result) == 2

    def test_excludes_outdated_shots(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat())
        shot_repo.save_shot(_shot(id="sh1", order_index=0))
        shot_repo.save_shot(_shot(id="sh2", order_index=1, status="outdated"))
        result = shot_repo.get_current_shots_by_project("p1")
        assert len(result) == 1
        assert result[0].id == "sh1"

    def test_excludes_shots_on_outdated_beats(self, hierarchy):
        beat_repo, shot_repo = hierarchy["beat_repo"], hierarchy["shot_repo"]
        beat_repo.save_beat(_beat(id="b1", order_index=0))
        beat_repo.save_beat(_beat(id="b2", order_index=1, status="outdated"))
        shot_repo.save_shot(_shot(id="sh1", beat_id="b1", order_index=0))
        shot_repo.save_shot(_shot(id="sh2", beat_id="b2", order_index=1))
        result = shot_repo.get_current_shots_by_project("p1")
        assert len(result) == 1
        assert result[0].id == "sh1"


# ---------------------------------------------------------------------------
# 10. get_character by ID
# ---------------------------------------------------------------------------

class TestGetCharacterById:
    def test_returns_character(self, hierarchy):
        char_repo, db = hierarchy["char_repo"], hierarchy["db"]
        char = CharacterReference(
            id="c1", project_id="p1", wc_character_id="wc1",
            name="Hero", description="Brave", appearance="Tall",
            provenance=_prov(),
        )
        char_repo.save_character(char)
        result = char_repo.get_character("c1")
        assert result is not None
        assert result.name == "Hero"
        assert result.id == "c1"

    def test_returns_none_for_unknown(self, hierarchy):
        char_repo = hierarchy["char_repo"]
        assert char_repo.get_character("no_such_id") is None
