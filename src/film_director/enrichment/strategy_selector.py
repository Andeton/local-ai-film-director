"""Deterministic strategy selector — zero LLM calls, zero provider-specific fields."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from film_director.models.canonical import (
    GenerationPlan,
    ReferenceRequirements,
    ShotSpecificationV1,
    StrategySelectionContext,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_selection_context(shot: ShotSpecificationV1) -> StrategySelectionContext:
    """Derive a StrategySelectionContext from a ShotSpecificationV1 (pure, no LLM)."""
    has_character_refs = any(
        bool(s.ref_images) for s in shot.subjects
    )
    has_recurring_cast = len(shot.subjects) > 0
    has_storyboard_image = bool(shot.storyboard_image_path)
    has_prev_shot = bool(shot.continuity_inputs.get("prev_shot_id"))
    subject_count = len(shot.subjects)
    shot_purpose = shot.dramatic_purpose

    return StrategySelectionContext(
        has_character_refs=has_character_refs,
        has_recurring_cast=has_recurring_cast,
        has_storyboard_image=has_storyboard_image,
        has_prev_shot=has_prev_shot,
        shot_purpose=shot_purpose,
        subject_count=subject_count,
    )


class StrategySelector:
    """Pure deterministic strategy selector — maps context to GenerationPlan."""

    def select_strategy(
        self,
        ctx: StrategySelectionContext,
        shot: ShotSpecificationV1,
        project_aspect: str,
    ) -> GenerationPlan:
        """Select strategy and build GenerationPlan. No LLM, no DB, no side effects."""
        strategy, reason, continuity_mode = self._pick_strategy(ctx)
        reference_requirements = self._build_reference_requirements(strategy, ctx)

        now = _utc_now()
        plan_id = f"gplan{uuid.uuid4().hex[:12]}"

        return GenerationPlan(
            id=plan_id,
            shot_id=shot.id,
            shot_version=shot.version,
            strategy=strategy,
            reference_requirements=reference_requirements,
            duration_sec=shot.duration_sec,
            resolution_intent={"aspect": project_aspect},
            seed_policy="random",
            seed=None,
            continuity_mode=continuity_mode,
            selection_reason=reason,
            status="draft",
            version=1,
            created_at=now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pick_strategy(
        self,
        ctx: StrategySelectionContext,
    ) -> tuple[str, str, str]:
        """Return (strategy, reason, continuity_mode) using explicit precedence."""
        # Priority 1 — frame continuity
        if ctx.has_prev_shot:
            return (
                "FIRST_LAST_FRAME",
                "Continuation from previous shot requires frame-to-frame consistency",
                "first_last",
            )

        # Priority 2 — multi-subject with composition
        if ctx.subject_count >= 3 and ctx.has_storyboard_image:
            return (
                "MULTI_PANEL",
                "Multi-subject scene with storyboard composition",
                "none",
            )

        # Priority 3 — locked composition, no recurring cast
        if ctx.has_storyboard_image and not ctx.has_recurring_cast:
            return (
                "IMAGE_TO_VIDEO",
                "Locked storyboard composition without character references",
                "none",
            )

        # Priority 4 — identity-consistent generation
        # Subjects with character IDs → REFERENCE_TO_VIDEO (refs resolved at generation time)
        if ctx.has_recurring_cast:
            return (
                "REFERENCE_TO_VIDEO",
                "Character subjects present — references resolved at generation time",
                "none",
            )

        # Default — text only (no subjects at all)
        return (
            "TEXT_TO_VIDEO",
            "No character subjects or storyboard — text-only generation",
            "none",
        )

    def _build_reference_requirements(
        self,
        strategy: str,
        ctx: StrategySelectionContext,
    ) -> ReferenceRequirements:
        """Build ReferenceRequirements for the selected strategy."""
        if strategy == "TEXT_TO_VIDEO":
            return ReferenceRequirements()

        if strategy == "IMAGE_TO_VIDEO":
            return ReferenceRequirements(scene_ref=True)

        if strategy == "REFERENCE_TO_VIDEO":
            return ReferenceRequirements(character_refs=True)

        if strategy == "FIRST_LAST_FRAME":
            return ReferenceRequirements(
                character_refs=ctx.has_character_refs,
                prev_frame=True,
            )

        if strategy == "MULTI_PANEL":
            return ReferenceRequirements(
                character_refs=ctx.has_character_refs,
                scene_ref=True,
            )

        # Unreachable — exhaustive match on Literal strategies
        return ReferenceRequirements()  # pragma: no cover
