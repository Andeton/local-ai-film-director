"""Tests for StrategySelector (M2.E) — TDD, deterministic, zero LLM."""
from __future__ import annotations

import uuid

import pytest

from film_director.enrichment.strategy_selector import (
    StrategySelector,
    build_selection_context,
)
from film_director.models.canonical import (
    CameraIntent,
    GenerationPlan,
    ShotSpecificationV1,
    ShotSubject,
    StrategySelectionContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shot(
    subjects: list[ShotSubject] | None = None,
    storyboard_image_path: str | None = None,
    continuity_inputs: dict | None = None,
    dramatic_purpose: str = "test purpose",
    duration_sec: float = 5.0,
    version: int = 1,
) -> ShotSpecificationV1:
    return ShotSpecificationV1(
        id=f"shot-{uuid.uuid4().hex[:8]}",
        beat_id="beat-001",
        dramatic_purpose=dramatic_purpose,
        subjects=subjects or [],
        action="walks forward",
        camera=CameraIntent(shot_size="medium"),
        order_index=0,
        storyboard_image_path=storyboard_image_path,
        continuity_inputs=continuity_inputs or {},
        duration_sec=duration_sec,
        version=version,
    )


def _make_ctx(
    has_character_refs: bool = False,
    has_recurring_cast: bool = False,
    has_storyboard_image: bool = False,
    has_prev_shot: bool = False,
    shot_purpose: str = "test",
    subject_count: int = 0,
) -> StrategySelectionContext:
    return StrategySelectionContext(
        has_character_refs=has_character_refs,
        has_recurring_cast=has_recurring_cast,
        has_storyboard_image=has_storyboard_image,
        has_prev_shot=has_prev_shot,
        shot_purpose=shot_purpose,
        subject_count=subject_count,
    )


# ---------------------------------------------------------------------------
# Context building tests (5)
# ---------------------------------------------------------------------------

class TestBuildSelectionContext:
    def test_context_has_character_refs_true(self):
        """Subject with ref_images → has_character_refs=True."""
        shot = _make_shot(subjects=[
            ShotSubject(character_id="c1", name="Alice", ref_images=["a.png"]),
        ])
        ctx = build_selection_context(shot)
        assert ctx.has_character_refs is True

    def test_context_has_character_refs_false(self):
        """Subject with empty ref_images → has_character_refs=False."""
        shot = _make_shot(subjects=[
            ShotSubject(character_id="c1", name="Alice", ref_images=[]),
        ])
        ctx = build_selection_context(shot)
        assert ctx.has_character_refs is False

    def test_context_has_recurring_cast_true(self):
        """Subjects present → has_recurring_cast=True."""
        shot = _make_shot(subjects=[
            ShotSubject(character_id="c1", name="Alice"),
        ])
        ctx = build_selection_context(shot)
        assert ctx.has_recurring_cast is True

    def test_context_has_recurring_cast_false(self):
        """No subjects → has_recurring_cast=False."""
        shot = _make_shot(subjects=[])
        ctx = build_selection_context(shot)
        assert ctx.has_recurring_cast is False

    def test_context_has_storyboard_image_true(self):
        """Non-empty storyboard_image_path → has_storyboard_image=True."""
        shot = _make_shot(storyboard_image_path="/some/image.png")
        ctx = build_selection_context(shot)
        assert ctx.has_storyboard_image is True

    def test_context_has_storyboard_image_false_none(self):
        """None path → has_storyboard_image=False."""
        shot = _make_shot(storyboard_image_path=None)
        ctx = build_selection_context(shot)
        assert ctx.has_storyboard_image is False

    def test_context_has_storyboard_image_false_empty(self):
        """Empty string path → has_storyboard_image=False."""
        shot = _make_shot(storyboard_image_path="")
        ctx = build_selection_context(shot)
        assert ctx.has_storyboard_image is False

    def test_context_has_prev_shot_true(self):
        """continuity_inputs with prev_shot_id → has_prev_shot=True."""
        shot = _make_shot(continuity_inputs={"prev_shot_id": "shot-prev"})
        ctx = build_selection_context(shot)
        assert ctx.has_prev_shot is True

    def test_context_has_prev_shot_false_missing(self):
        """Empty continuity_inputs → has_prev_shot=False."""
        shot = _make_shot(continuity_inputs={})
        ctx = build_selection_context(shot)
        assert ctx.has_prev_shot is False

    def test_context_subject_count(self):
        """subject_count equals number of subjects."""
        shot = _make_shot(subjects=[
            ShotSubject(character_id="c1", name="Alice"),
            ShotSubject(character_id="c2", name="Bob"),
            ShotSubject(character_id="c3", name="Carol"),
        ])
        ctx = build_selection_context(shot)
        assert ctx.subject_count == 3


# ---------------------------------------------------------------------------
# Strategy selection tests (5 — one per strategy)
# ---------------------------------------------------------------------------

class TestStrategySelection:
    def setup_method(self):
        self.selector = StrategySelector()
        self.shot = _make_shot()

    def test_first_last_frame_strategy(self):
        """has_prev_shot=True → FIRST_LAST_FRAME."""
        ctx = _make_ctx(has_prev_shot=True)
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "FIRST_LAST_FRAME"

    def test_multi_panel_strategy(self):
        """subject_count>=3 and has_storyboard_image → MULTI_PANEL."""
        ctx = _make_ctx(subject_count=3, has_storyboard_image=True)
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "MULTI_PANEL"

    def test_image_to_video_strategy(self):
        """has_storyboard_image=True, has_recurring_cast=False → IMAGE_TO_VIDEO."""
        ctx = _make_ctx(has_storyboard_image=True, has_recurring_cast=False)
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "IMAGE_TO_VIDEO"

    def test_reference_to_video_strategy(self):
        """has_character_refs=True, has_recurring_cast=True → REFERENCE_TO_VIDEO."""
        ctx = _make_ctx(has_character_refs=True, has_recurring_cast=True, subject_count=1)
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "REFERENCE_TO_VIDEO"

    def test_text_to_video_strategy(self):
        """Nothing applicable → TEXT_TO_VIDEO."""
        ctx = _make_ctx()
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "TEXT_TO_VIDEO"


# ---------------------------------------------------------------------------
# Precedence collision tests (3)
# ---------------------------------------------------------------------------

class TestPrecedenceCollisions:
    def setup_method(self):
        self.selector = StrategySelector()
        self.shot = _make_shot()

    def test_priority_1_beats_all(self):
        """Priority 1 (has_prev_shot) wins over all other matches."""
        ctx = _make_ctx(
            has_prev_shot=True,
            has_character_refs=True,
            has_recurring_cast=True,
            has_storyboard_image=True,
            subject_count=4,
        )
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "FIRST_LAST_FRAME"

    def test_priority_2_beats_4(self):
        """Priority 2 (multi-panel) wins over reference-to-video."""
        ctx = _make_ctx(
            has_prev_shot=False,
            subject_count=3,
            has_storyboard_image=True,
            has_character_refs=True,
            has_recurring_cast=True,
        )
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "MULTI_PANEL"

    def test_priority_3_beats_4(self):
        """Priority 3 (image-to-video) wins when recurring_cast=False even with char refs."""
        ctx = _make_ctx(
            has_prev_shot=False,
            has_storyboard_image=True,
            has_recurring_cast=False,
            has_character_refs=True,
            subject_count=1,
        )
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "IMAGE_TO_VIDEO"


# ---------------------------------------------------------------------------
# GenerationPlan fields tests (5)
# ---------------------------------------------------------------------------

class TestGenerationPlanFields:
    def setup_method(self):
        self.selector = StrategySelector()
        self.shot = _make_shot(duration_sec=7.5, version=3)

    def test_plan_shot_id_shot_version_duration(self):
        """Plan has correct shot_id, shot_version, duration_sec."""
        ctx = _make_ctx()
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.shot_id == self.shot.id
        assert plan.shot_version == self.shot.version
        assert plan.duration_sec == pytest.approx(7.5)

    def test_plan_resolution_intent_has_aspect(self):
        """resolution_intent contains the project_aspect."""
        ctx = _make_ctx()
        plan = self.selector.select_strategy(ctx, self.shot, "9:16")
        assert plan.resolution_intent["aspect"] == "9:16"

    def test_plan_seed_policy_and_seed(self):
        """seed_policy='random', seed=None by default."""
        ctx = _make_ctx()
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.seed_policy == "random"
        assert plan.seed is None

    def test_continuity_mode_first_last_for_flf(self):
        """FIRST_LAST_FRAME → continuity_mode='first_last'; others → 'none'."""
        ctx_flf = _make_ctx(has_prev_shot=True)
        plan_flf = self.selector.select_strategy(ctx_flf, self.shot, "16:9")
        assert plan_flf.continuity_mode == "first_last"

        ctx_ttv = _make_ctx()
        plan_ttv = self.selector.select_strategy(ctx_ttv, self.shot, "16:9")
        assert plan_ttv.continuity_mode == "none"

    def test_selection_reason_is_nonempty(self):
        """selection_reason is a non-empty string."""
        ctx = _make_ctx()
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert isinstance(plan.selection_reason, str)
        assert len(plan.selection_reason) > 0


# ---------------------------------------------------------------------------
# ReferenceRequirements tests (5)
# ---------------------------------------------------------------------------

class TestReferenceRequirements:
    def setup_method(self):
        self.selector = StrategySelector()
        self.shot = _make_shot()

    def test_text_to_video_all_false(self):
        """TEXT_TO_VIDEO: all reference requirements False."""
        ctx = _make_ctx()
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "TEXT_TO_VIDEO"
        rr = plan.reference_requirements
        assert rr.character_refs is False
        assert rr.scene_ref is False
        assert rr.prev_frame is False
        assert rr.style_ref is False

    def test_image_to_video_scene_ref_only(self):
        """IMAGE_TO_VIDEO: scene_ref=True, rest False."""
        ctx = _make_ctx(has_storyboard_image=True, has_recurring_cast=False)
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "IMAGE_TO_VIDEO"
        rr = plan.reference_requirements
        assert rr.scene_ref is True
        assert rr.character_refs is False
        assert rr.prev_frame is False
        assert rr.style_ref is False

    def test_reference_to_video_character_refs_only(self):
        """REFERENCE_TO_VIDEO: character_refs=True, rest False."""
        ctx = _make_ctx(has_character_refs=True, has_recurring_cast=True, subject_count=1)
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        assert plan.strategy == "REFERENCE_TO_VIDEO"
        rr = plan.reference_requirements
        assert rr.character_refs is True
        assert rr.scene_ref is False
        assert rr.prev_frame is False
        assert rr.style_ref is False

    def test_first_last_frame_refs(self):
        """FIRST_LAST_FRAME: prev_frame=True, character_refs=ctx.has_character_refs."""
        ctx_with = _make_ctx(has_prev_shot=True, has_character_refs=True)
        plan_with = self.selector.select_strategy(ctx_with, self.shot, "16:9")
        assert plan_with.reference_requirements.prev_frame is True
        assert plan_with.reference_requirements.character_refs is True

        ctx_without = _make_ctx(has_prev_shot=True, has_character_refs=False)
        plan_without = self.selector.select_strategy(ctx_without, self.shot, "16:9")
        assert plan_without.reference_requirements.prev_frame is True
        assert plan_without.reference_requirements.character_refs is False

    def test_multi_panel_refs(self):
        """MULTI_PANEL: scene_ref=True, character_refs=ctx.has_character_refs."""
        ctx_with = _make_ctx(subject_count=3, has_storyboard_image=True, has_character_refs=True)
        plan_with = self.selector.select_strategy(ctx_with, self.shot, "16:9")
        assert plan_with.reference_requirements.scene_ref is True
        assert plan_with.reference_requirements.character_refs is True

        ctx_without = _make_ctx(subject_count=3, has_storyboard_image=True, has_character_refs=False)
        plan_without = self.selector.select_strategy(ctx_without, self.shot, "16:9")
        assert plan_without.reference_requirements.scene_ref is True
        assert plan_without.reference_requirements.character_refs is False


# ---------------------------------------------------------------------------
# Safety tests (3)
# ---------------------------------------------------------------------------

class TestSafety:
    def setup_method(self):
        self.selector = StrategySelector()
        self.shot = _make_shot()

    def test_unique_plan_ids_across_calls(self):
        """Each call produces a unique plan id."""
        ctx = _make_ctx()
        ids = {self.selector.select_strategy(ctx, self.shot, "16:9").id for _ in range(10)}
        assert len(ids) == 10

    def test_no_input_mutation(self):
        """ctx and shot are not mutated by select_strategy."""
        ctx = _make_ctx(has_character_refs=True, subject_count=1)
        original_ctx_refs = ctx.has_character_refs
        original_shot_id = self.shot.id
        self.selector.select_strategy(ctx, self.shot, "16:9")
        assert ctx.has_character_refs == original_ctx_refs
        assert self.shot.id == original_shot_id

    def test_no_provider_specific_values(self):
        """Output GenerationPlan has no engine_family or workflow_profile fields."""
        ctx = _make_ctx()
        plan = self.selector.select_strategy(ctx, self.shot, "16:9")
        plan_dict = plan.model_dump()
        assert "engine_family" not in plan_dict
        assert "workflow_profile" not in plan_dict
