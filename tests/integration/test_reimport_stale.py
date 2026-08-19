"""Integration tests for M4.F — reimport + stale propagation on WC source changes.

Tests change detection for script, storyboard, and director data via extended
project source hash, plus stale propagation to downstream derived artifacts.

Uses a real SQLite DB with ImportService + EnrichmentService wiring.
No live WC/Ollama dependency.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from film_director.adapters.wind_comic import WindComicAdapter, WCProjectBundle
from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
from film_director.enrichment.stale_propagator import StalePropagator
from film_director.enrichment.strategy_selector import StrategySelector
from film_director.llm.provider import LLMResponse
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
from film_director.models.provenance import (
    Provenance,
    build_project_source_payload,
    compute_source_hash,
)
from film_director.models.wind_comic_dto import (
    WCCharacter,
    WCDirectorPlan,
    WCProject,
    WCScene,
    WCScriptShot,
    WCStoryboardShot,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
    TakeRepository,
)
from film_director.services.enrichment_service import EnrichmentService
from film_director.services.import_service import ImportService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_wc_fixture(tmp_path, *, script_data=None, storyboard_desc="A wide shot",
                        storyboard_duration=5.0, director_genre="drama",
                        director_style="noir"):
    """Create a WC SQLite fixture with script/storyboard/director data."""
    db_path = str(tmp_path / "wc.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE projects (
        id TEXT PRIMARY KEY, title TEXT, description TEXT DEFAULT '',
        status TEXT, aspect TEXT,
        style_id TEXT, script_data TEXT, locked_characters TEXT
    )""")
    conn.execute("""CREATE TABLE project_assets (
        id TEXT PRIMARY KEY, project_id TEXT, type TEXT, name TEXT,
        shot_number INTEGER, data TEXT, media_urls TEXT,
        persistent_url TEXT, version INTEGER
    )""")

    if script_data is None:
        script_data = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "Hello", "action": "walks", "emotion": "happy"},
        ]}

    conn.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)", (
        "wc-proj", "Test Film", "A test film.", "complete", "16:9", None,
        json.dumps(script_data), "[]",
    ))
    # Scene
    conn.execute("INSERT INTO project_assets VALUES (?,?,?,?,?,?,?,?,?)", (
        "scene-1", "wc-proj", "scene", "Scene 1", None,
        json.dumps({"location": "Office", "description": "Quiet"}),
        "[]", None, 1,
    ))
    # Character
    conn.execute("INSERT INTO project_assets VALUES (?,?,?,?,?,?,?,?,?)", (
        "char-1", "wc-proj", "character", "Hero", None,
        json.dumps({"description": "Protagonist", "appearance": "Tall"}),
        "[]", None, 1,
    ))
    # Storyboard
    conn.execute("INSERT INTO project_assets VALUES (?,?,?,?,?,?,?,?,?)", (
        "sb-1", "wc-proj", "storyboard", None, 1,
        json.dumps({"description": storyboard_desc, "duration": storyboard_duration}),
        json.dumps(["http://img/sb1.png"]), None, 1,
    ))
    # Director plan
    conn.execute("INSERT INTO project_assets VALUES (?,?,?,?,?,?,?,?,?)", (
        "plan-1", "wc-proj", "plan", None, None,
        json.dumps({"genre": director_genre, "style": director_style, "storyStructure": {"acts": 3}}),
        "[]", None, 1,
    ))
    conn.commit()
    conn.close()
    return db_path


class FakeLLM:
    def __init__(self):
        self._responses = []
        self.call_count = 0

    def queue(self, resp):
        self._responses.append(resp)

    def chat(self, messages, expect_json=False):
        self.call_count += 1
        return self._responses.pop(0)

    def health(self):
        return True


def _beat_response():
    return LLMResponse(content="", parsed={"beats": [
        {"dramatic_action": "Hero acts", "character_intention": "Find truth",
         "change": "Confusion to clarity", "characters": ["Hero"]},
    ]}, model="test")


def _coverage_response():
    return LLMResponse(content="", parsed={"coverage": [
        {"shot_type": "establishing", "shot_size": "wide", "angle": "eye_level",
         "movement": "static", "purpose": "Establish mood", "duration_sec": 5.0},
    ]}, model="test")


@pytest.fixture
def env(tmp_path):
    """Full wired environment: WC fixture + our DB + services."""
    wc_path = _create_wc_fixture(tmp_path)
    adapter = WindComicAdapter(wc_path)

    db = Database(str(tmp_path / "our.db"))
    db.init_schema()

    project_repo = ProjectRepository(db)
    sequence_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    character_repo = CharacterRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)

    llm = FakeLLM()

    stale_propagator = StalePropagator(
        db=db, beat_repo=beat_repo, shot_repo=shot_repo,
        plan_repo=plan_repo, sequence_repo=sequence_repo, scene_repo=scene_repo,
    )

    import_service = ImportService(
        adapter=adapter, project_repo=project_repo, sequence_repo=sequence_repo,
        scene_repo=scene_repo, character_repo=character_repo, db=db,
    )

    enrichment_service = EnrichmentService(
        db=db, project_repo=project_repo, sequence_repo=sequence_repo,
        scene_repo=scene_repo, character_repo=character_repo,
        beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
        adapter=adapter, import_service=import_service,
        beat_enricher=BeatEnricher(llm), coverage_planner=CoveragePlanner(llm),
        shot_spec_builder=ShotSpecBuilder(), strategy_selector=StrategySelector(),
        stale_propagator=stale_propagator,
    )

    return dict(
        tmp_path=tmp_path, wc_path=wc_path, adapter=adapter, db=db,
        project_repo=project_repo, sequence_repo=sequence_repo,
        scene_repo=scene_repo, character_repo=character_repo,
        beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
        llm=llm, import_service=import_service,
        enrichment_service=enrichment_service,
        stale_propagator=stale_propagator,
    )


def _do_initial_import_and_enrich(env):
    """Import + enrich, returning (project_id, beat_ids, shot_ids, plan_ids)."""
    env["llm"].queue(_beat_response())
    env["llm"].queue(_coverage_response())
    result = env["import_service"].import_project("wc-proj")
    enrich = env["enrichment_service"].enrich_project(result.project_id)
    beats = env["beat_repo"].get_current_beats_by_scene(
        env["scene_repo"].get_scenes_by_sequence(
            env["sequence_repo"].get_sequences_by_project(result.project_id)[0].id
        )[0].id
    )
    shots = []
    plans = []
    for b in beats:
        s = env["shot_repo"].get_current_shots_by_beat(b.id)
        shots.extend(s)
        for sh in s:
            p = env["plan_repo"].get_current_plan_by_shot(sh.id)
            if p:
                plans.append(p)
    return result.project_id, [b.id for b in beats], [s.id for s in shots], [p.id for p in plans]


def _modify_wc(wc_path, table, column, value, where_id):
    """Modify a field in the WC fixture DB."""
    conn = sqlite3.connect(wc_path)
    conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (value, where_id))
    conn.commit()
    conn.close()


# ===========================================================================
# IDENTICAL REIMPORT — no false stale
# ===========================================================================

class TestIdenticalReimport:
    def test_no_change_detection_on_identical_reimport(self, env):
        """Reimporting identical WC data → no changes detected."""
        env["import_service"].import_project("wc-proj")
        changes = env["import_service"].check_for_changes(
            env["project_repo"].get_project_by_wc_id("wc-proj").id
        )
        assert len(changes) == 0

    def test_no_stale_propagation_on_identical_reimport(self, env):
        """Reimporting identical WC data → no stale marks."""
        proj_id, beat_ids, shot_ids, plan_ids = _do_initial_import_and_enrich(env)
        # Reimport (no WC changes)
        env["import_service"].import_project("wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        assert len(changes) == 0
        # Verify beats/shots/plans still current
        for bid in beat_ids:
            b = env["beat_repo"].get_beat(bid)
            assert b.status != "outdated"
        for sid in shot_ids:
            s = env["shot_repo"].get_shot(sid)
            assert s.status != "outdated"


# ===========================================================================
# SCRIPT CHANGES
# ===========================================================================

class TestScriptChanges:
    def test_dialogue_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        # Change dialogue in script
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "Goodbye", "action": "walks", "emotion": "happy"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_action_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "Hello", "action": "runs", "emotion": "happy"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_emotion_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "Hello", "action": "walks", "emotion": "sad"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_characters_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero", "Villain"],
             "dialogue": "Hello", "action": "walks", "emotion": "happy"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_script_change_makes_derived_stale(self, env):
        proj_id, beat_ids, shot_ids, plan_ids = _do_initial_import_and_enrich(env)
        # Change script
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "Changed!", "action": "walks", "emotion": "happy"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        env["enrichment_service"].apply_source_changes(proj_id, changes)
        # Verify derived artifacts are stale
        for bid in beat_ids:
            assert env["beat_repo"].get_beat(bid).status == "outdated"
        for sid in shot_ids:
            assert env["shot_repo"].get_shot(sid).status == "outdated"


# ===========================================================================
# STORYBOARD CHANGES
# ===========================================================================

class TestStoryboardChanges:
    def test_description_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        _modify_wc(env["wc_path"], "project_assets", "data",
                    json.dumps({"description": "Changed storyboard", "duration": 5.0}), "sb-1")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_duration_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        _modify_wc(env["wc_path"], "project_assets", "data",
                    json.dumps({"description": "A wide shot", "duration": 12.0}), "sb-1")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_version_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        _modify_wc(env["wc_path"], "project_assets", "version", 2, "sb-1")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_storyboard_change_makes_derived_stale(self, env):
        proj_id, beat_ids, shot_ids, _ = _do_initial_import_and_enrich(env)
        _modify_wc(env["wc_path"], "project_assets", "data",
                    json.dumps({"description": "New desc", "duration": 9.0}), "sb-1")
        changes = env["import_service"].check_for_changes(proj_id)
        env["enrichment_service"].apply_source_changes(proj_id, changes)
        for bid in beat_ids:
            assert env["beat_repo"].get_beat(bid).status == "outdated"
        for sid in shot_ids:
            assert env["shot_repo"].get_shot(sid).status == "outdated"


# ===========================================================================
# DIRECTOR CHANGES
# ===========================================================================

class TestDirectorChanges:
    def test_genre_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        _modify_wc(env["wc_path"], "project_assets", "data",
                    json.dumps({"genre": "comedy", "style": "noir", "storyStructure": {"acts": 3}}),
                    "plan-1")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_style_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        _modify_wc(env["wc_path"], "project_assets", "data",
                    json.dumps({"genre": "drama", "style": "cyberpunk", "storyStructure": {"acts": 3}}),
                    "plan-1")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_story_structure_change_detected(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        _modify_wc(env["wc_path"], "project_assets", "data",
                    json.dumps({"genre": "drama", "style": "noir", "storyStructure": {"acts": 5}}),
                    "plan-1")
        changes = env["import_service"].check_for_changes(proj_id)
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in changes)

    def test_director_change_updates_context(self, env):
        env["import_service"].import_project("wc-proj")
        proj_id = env["project_repo"].get_project_by_wc_id("wc-proj").id
        _modify_wc(env["wc_path"], "project_assets", "data",
                    json.dumps({"genre": "comedy", "style": "slapstick", "storyStructure": {"acts": 2}}),
                    "plan-1")
        # Reimport
        env["import_service"].import_project("wc-proj")
        proj = env["project_repo"].get_project(proj_id)
        assert proj.director_context["genre"] == "comedy"
        assert proj.director_context["style"] == "slapstick"


# ===========================================================================
# HUMAN EDIT SAFETY
# ===========================================================================

class TestHumanEditSafety:
    def test_human_edited_shot_source_preserved_after_reimport(self, env):
        proj_id, _, shot_ids, _ = _do_initial_import_and_enrich(env)
        # Human-edit a shot
        shot = env["shot_repo"].get_shot(shot_ids[0])
        edited = shot.model_copy(update={"source": "human", "version": shot.version + 1})
        env["shot_repo"].save_shot(edited)
        # Change WC data and reimport
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "Changed!", "action": "walks", "emotion": "happy"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        env["import_service"].import_project("wc-proj")
        # Human-edited shot's source marker is preserved
        shot_after = env["shot_repo"].get_shot(shot_ids[0])
        assert shot_after.source == "human"


# ===========================================================================
# HISTORY PRESERVATION
# ===========================================================================

class TestHistoryPreservation:
    def test_generation_request_survives_reimport(self, env):
        from film_director.generation.generation_request import GenerationRequest
        from film_director.generation.h3_prompt import H3PromptV1
        from film_director.persistence.repositories import H3PromptRepository
        proj_id, _, shot_ids, plan_ids = _do_initial_import_and_enrich(env)
        # Create prerequisite prompt
        prompt_repo = H3PromptRepository(env["db"])
        prompt = H3PromptV1(
            id="prompt-1", shot_id=shot_ids[0], generation_plan_id=plan_ids[0],
            source_shot_version=1, source_generation_plan_version=1,
            subject_definitions="sub", summary="sum", retention_analysis="ret",
            detailed_description="desc", overall_soundscape="sound",
            non_diegetic_music="music", rendered_prompt_text="test prompt",
            status="current", version=1, created_at="2024-01-01T00:00:00",
        )
        prompt_repo.save_prompt(prompt)
        # Create a generation request
        greq_repo = GenerationRequestRepository(env["db"])
        greq = GenerationRequest(
            id="greq-test-1", shot_id=shot_ids[0], shot_version=1,
            generation_plan_id=plan_ids[0], generation_plan_version=1,
            prompt_artifact_id="prompt-1", prompt_artifact_version=1,
            workflow_definition_id="wf-1", workflow_definition_version="1",
            workflow_template_fingerprint="a" * 64,
            take_number=1, parameters_snapshot=[], reference_snapshot=[],
            seed=42, comfyui_prompt_id=None,
            status="succeeded", submitted_at="2024-01-01T00:00:00",
            completed_at="2024-01-01T00:01:00", error=None,
        )
        greq_repo.create_request(greq)
        # Change WC and reimport + stale cascade
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "New!", "action": "walks", "emotion": "happy"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        env["enrichment_service"].apply_source_changes(proj_id, changes)
        env["import_service"].import_project("wc-proj")
        # Request survives unchanged
        greq_after = greq_repo.get_request("greq-test-1")
        assert greq_after is not None
        assert greq_after.id == "greq-test-1"
        assert greq_after.shot_id == shot_ids[0]
        assert greq_after.seed == 42
        assert greq_after.status == "succeeded"

    def test_take_survives_reimport(self, env):
        from film_director.generation.generation_request import GenerationRequest, Take
        from film_director.generation.h3_prompt import H3PromptV1
        from film_director.persistence.repositories import H3PromptRepository
        proj_id, _, shot_ids, plan_ids = _do_initial_import_and_enrich(env)
        # Create prerequisite prompt
        prompt_repo = H3PromptRepository(env["db"])
        prompt = H3PromptV1(
            id="p1", shot_id=shot_ids[0], generation_plan_id=plan_ids[0],
            source_shot_version=1, source_generation_plan_version=1,
            subject_definitions="sub", summary="sum", retention_analysis="ret",
            detailed_description="desc", overall_soundscape="sound",
            non_diegetic_music="music", rendered_prompt_text="test",
            status="current", version=1, created_at="t0",
        )
        prompt_repo.save_prompt(prompt)
        greq_repo = GenerationRequestRepository(env["db"])
        take_repo = TakeRepository(env["db"])
        greq = GenerationRequest(
            id="greq-t-1", shot_id=shot_ids[0], shot_version=1,
            generation_plan_id=plan_ids[0], generation_plan_version=1,
            prompt_artifact_id="p1", prompt_artifact_version=1,
            workflow_definition_id="wf1", workflow_definition_version="1",
            workflow_template_fingerprint="b" * 64,
            take_number=1, parameters_snapshot=[], reference_snapshot=[],
            seed=99, comfyui_prompt_id=None,
            status="succeeded", submitted_at="t0", completed_at="t1", error=None,
        )
        greq_repo.create_request(greq)
        take = Take(
            id="take-1", shot_id=shot_ids[0], generation_request_id="greq-t-1",
            seed=99, video_path="/v/test.mp4", audio_path=None,
            last_frame_path="/v/frame.png", status="succeeded", created_at="t1",
        )
        take_repo.save_take(take)
        # Change WC and reimport
        new_script = {"shots": [
            {"shotNumber": 1, "sceneDescription": "Office", "characters": ["Hero"],
             "dialogue": "Changed!", "action": "runs", "emotion": "sad"},
        ]}
        _modify_wc(env["wc_path"], "projects", "script_data",
                    json.dumps(new_script), "wc-proj")
        changes = env["import_service"].check_for_changes(proj_id)
        env["enrichment_service"].apply_source_changes(proj_id, changes)
        env["import_service"].import_project("wc-proj")
        # Take survives
        take_after = take_repo.get_take("take-1")
        assert take_after is not None
        assert take_after.video_path == "/v/test.mp4"
        assert take_after.seed == 99
