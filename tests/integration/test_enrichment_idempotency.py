"""Tests for enrichment idempotency, replanning, and strategy selection.

Regression tests for:
- Second enrichment doubling shot count (6→13)
- TEXT_TO_VIDEO assigned to shots with character subjects
- Replanning replacing rather than appending
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.enrichment.shot_planner import ShotPlanner
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
from film_director.enrichment.stale_propagator import StalePropagator
from film_director.enrichment.strategy_selector import StrategySelector, build_selection_context
from film_director.errors import HumanEditConflictError
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
    TakeRepository,
)
from film_director.services.enrichment_service import EnrichmentService


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


def _fake_shot_plan():
    return {"shots": [
        {"action": f"Shot {i+1} action", "dramatic_purpose": f"Purpose {i+1}",
         "shot_size": ["wide", "medium", "close_up", "medium_wide", "extreme_wide"][i % 5],
         "characters": ["Hero"], "duration_sec": 5.0}
        for i in range(6)
    ]}


def _fake_char_enrichment():
    return {"characters": []}


def _make_fake_llm():
    llm = MagicMock()
    call_count = [0]
    def _chat(messages, expect_json=False):
        call_count[0] += 1
        # Detect which prompt by system message content
        sys_msg = messages[0]["content"] if messages else ""
        if "shot list" in sys_msg:
            data = _fake_shot_plan()
        elif "character visual" in sys_msg:
            data = _fake_char_enrichment()
        else:
            data = _fake_shot_plan()
        return LLMResponse(content=json.dumps(data), parsed=data, model="test")
    llm.chat.side_effect = _chat
    return llm


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    char_repo = CharacterRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)

    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            director_context={"description": "A test story about a hero."},
            provenance=_prov(),
        ), conn=conn)
        seq_repo.save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="Main", order_index=0,
        ), conn=conn)
        # Two scenes (like the real project)
        scene_repo.save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="Scene 1", location="Office", description="Opening",
            order_index=0, provenance=_prov(),
        ), conn=conn)
        scene_repo.save_scene(Scene(
            id="scene-2", sequence_id="seq-1", wc_scene_id="wc-s2",
            name="Scene 2", location="Street", description="Chase",
            order_index=1, provenance=_prov(),
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Hero", description="", appearance="Strong build, dark hair",
            provenance=_prov(),
        ), conn=conn)

    fake_llm = _make_fake_llm()
    planner = ShotPlanner(fake_llm)

    stale_prop = StalePropagator(
        db=db, beat_repo=beat_repo, shot_repo=shot_repo,
        plan_repo=plan_repo, sequence_repo=seq_repo, scene_repo=scene_repo,
    )

    svc = EnrichmentService(
        db=db, project_repo=project_repo, sequence_repo=seq_repo,
        scene_repo=scene_repo, character_repo=char_repo,
        beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
        adapter=MagicMock(), import_service=MagicMock(),
        beat_enricher=BeatEnricher(MagicMock()),
        coverage_planner=CoveragePlanner(MagicMock()),
        shot_spec_builder=ShotSpecBuilder(),
        strategy_selector=StrategySelector(),
        stale_propagator=stale_prop,
        shot_planner=planner,
    )

    return {
        "svc": svc, "db": db,
        "shot_repo": shot_repo, "beat_repo": beat_repo,
        "plan_repo": plan_repo, "project_repo": project_repo,
        "fake_llm": fake_llm,
    }


class TestFirstEnrichmentCreatesShots:
    def test_creates_6_shots(self, env):
        result = env["svc"].enrich_project("proj-1")
        assert result.shots_created == 6
        shots = env["shot_repo"].get_current_shots_by_project("proj-1")
        assert len(shots) == 6


class TestSecondEnrichmentDoesNotDuplicate:
    def test_no_new_shots(self, env):
        r1 = env["svc"].enrich_project("proj-1")
        assert r1.shots_created == 6

        r2 = env["svc"].enrich_project("proj-1")
        assert r2.shots_created == 0
        assert r2.beats_created == 0

        shots = env["shot_repo"].get_current_shots_by_project("proj-1")
        assert len(shots) == 6  # still 6, not 12

    def test_preserves_shot_ids(self, env):
        env["svc"].enrich_project("proj-1")
        shots_before = env["shot_repo"].get_current_shots_by_project("proj-1")
        ids_before = {s.id for s in shots_before}

        env["svc"].enrich_project("proj-1")
        shots_after = env["shot_repo"].get_current_shots_by_project("proj-1")
        ids_after = {s.id for s in shots_after}

        assert ids_before == ids_after

    def test_preserves_shot_text(self, env):
        env["svc"].enrich_project("proj-1")
        shots_before = env["shot_repo"].get_current_shots_by_project("proj-1")
        actions_before = [s.action for s in sorted(shots_before, key=lambda s: s.order_index)]

        env["svc"].enrich_project("proj-1")
        shots_after = env["shot_repo"].get_current_shots_by_project("proj-1")
        actions_after = [s.action for s in sorted(shots_after, key=lambda s: s.order_index)]

        assert actions_before == actions_after


class TestReplanReplacesShots:
    def test_replan_replaces(self, env):
        env["svc"].enrich_project("proj-1")
        shots_before = env["shot_repo"].get_current_shots_by_project("proj-1")
        ids_before = {s.id for s in shots_before}

        r = env["svc"].replan_project("proj-1")
        assert r.shots_created == 6

        shots_after = env["shot_repo"].get_current_shots_by_project("proj-1")
        ids_after = {s.id for s in shots_after}

        assert len(shots_after) == 6  # replaced, not appended
        assert ids_before != ids_after  # new IDs

    def test_replan_refuses_with_takes(self, env):
        env["svc"].enrich_project("proj-1")
        shots = env["shot_repo"].get_current_shots_by_project("proj-1")
        shot = shots[0]
        plan = env["plan_repo"].get_current_plan_by_shot(shot.id)

        # Create prompt + generation request + Take
        from film_director.generation.generation_request import GenerationRequest, Take
        from film_director.generation.h3_prompt import H3PromptV1
        from film_director.persistence.repositories import GenerationRequestRepository, H3PromptRepository
        prompt_repo = H3PromptRepository(env["db"])
        prompt = H3PromptV1(
            id="p1", shot_id=shot.id, generation_plan_id=plan.id,
            source_shot_version=shot.version, source_generation_plan_version=plan.version,
            subject_definitions="subj", summary="sum", retention_analysis="ret",
            detailed_description="desc", overall_soundscape="", non_diegetic_music="",
            rendered_prompt_text="test", status="current", version=1,
            created_at="2026-01-01",
        )
        prompt_repo.save_prompt(prompt)
        req_repo = GenerationRequestRepository(env["db"])
        req = GenerationRequest(
            id="req-1", shot_id=shot.id, shot_version=shot.version,
            generation_plan_id=plan.id, generation_plan_version=plan.version,
            prompt_artifact_id="p1", prompt_artifact_version=1,
            workflow_definition_id="h3_r2v_v2", workflow_definition_version="2.0.0",
            workflow_template_fingerprint="a" * 64,
            take_number=1, parameters_snapshot=[], reference_snapshot=[],
            seed=42, status="succeeded",
        )
        req_repo.create_request(req)

        take_repo = TakeRepository(env["db"])
        take = Take(
            id="take-1", shot_id=shot.id,
            generation_request_id="req-1", seed=42,
            video_path="/fake.mp4", last_frame_path="/fake.png",
            status="succeeded", created_at="2026-01-01",
        )
        take_repo.save_take(take)

        with pytest.raises(HumanEditConflictError, match="Takes exist"):
            env["svc"].replan_project("proj-1")


class TestStrategyForCharacterShots:
    def test_subjects_without_ref_images_get_r2v(self):
        """Shots with character subjects must get REFERENCE_TO_VIDEO, not TEXT_TO_VIDEO."""
        shot = ShotSpecificationV1(
            id="s1", beat_id="b1", dramatic_purpose="test",
            subjects=[ShotSubject(character_id="c1", name="Hero", ref_images=[])],
            action="walks", camera=CameraIntent(shot_size="medium"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        ctx = build_selection_context(shot)
        selector = StrategySelector()
        plan = selector.select_strategy(ctx, shot, "16:9")
        assert plan.strategy == "REFERENCE_TO_VIDEO"

    def test_no_subjects_get_t2v(self):
        """Shots with NO subjects should get TEXT_TO_VIDEO."""
        shot = ShotSpecificationV1(
            id="s1", beat_id="b1", dramatic_purpose="test",
            subjects=[],
            action="landscape", camera=CameraIntent(shot_size="wide"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        ctx = build_selection_context(shot)
        selector = StrategySelector()
        plan = selector.select_strategy(ctx, shot, "16:9")
        assert plan.strategy == "TEXT_TO_VIDEO"

    def test_enrichment_creates_r2v_plans(self, env):
        """Enrichment-created shots with subjects get REFERENCE_TO_VIDEO plans."""
        env["svc"].enrich_project("proj-1")
        shots = env["shot_repo"].get_current_shots_by_project("proj-1")
        for shot in shots:
            plan = env["plan_repo"].get_current_plan_by_shot(shot.id)
            assert plan is not None
            if shot.subjects:
                assert plan.strategy == "REFERENCE_TO_VIDEO", \
                    f"Shot {shot.id} with subjects got {plan.strategy}"


class TestRepeatedEnrichmentIsNoOp:
    def test_nothing_missing_returns_zeros(self, env):
        r1 = env["svc"].enrich_project("proj-1")
        assert r1.shots_created == 6

        r2 = env["svc"].enrich_project("proj-1")
        assert r2.shots_created == 0
        assert r2.beats_created == 0
        assert r2.plans_created == 0

        r3 = env["svc"].enrich_project("proj-1")
        assert r3.shots_created == 0
