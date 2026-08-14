"""Tests for GenerationPlan, ReferenceRequirements canonical models (M2)."""
import pytest
from pydantic import ValidationError

from film_director.models.canonical import GenerationPlan, ReferenceRequirements


def _minimal_plan(**overrides) -> dict:
    base = {
        "id": "plan-1",
        "shot_id": "shot-1",
        "shot_version": 1,
        "strategy": "TEXT_TO_VIDEO",
        "reference_requirements": ReferenceRequirements(),
        "duration_sec": 5.0,
    }
    base.update(overrides)
    return base


def test_generation_plan_valid():
    plan = GenerationPlan(**_minimal_plan())
    assert plan.id == "plan-1"
    assert plan.shot_id == "shot-1"
    assert plan.shot_version == 1
    assert plan.strategy == "TEXT_TO_VIDEO"
    assert plan.duration_sec == 5.0
    assert plan.seed_policy == "random"
    assert plan.seed is None
    assert plan.continuity_mode == "none"
    assert plan.status == "draft"
    assert plan.version == 1
    assert plan.selection_reason == ""


def test_generation_plan_all_strategies():
    strategies = [
        "TEXT_TO_VIDEO",
        "IMAGE_TO_VIDEO",
        "REFERENCE_TO_VIDEO",
        "FIRST_LAST_FRAME",
        "MULTI_PANEL",
    ]
    for strategy in strategies:
        plan = GenerationPlan(**_minimal_plan(strategy=strategy))
        assert plan.strategy == strategy


def test_generation_plan_invalid_strategy():
    with pytest.raises(ValidationError):
        GenerationPlan(**_minimal_plan(strategy="H3_VIDEO"))


def test_generation_plan_seed_policies():
    for policy in ("random", "fixed", "vary_per_take"):
        plan = GenerationPlan(**_minimal_plan(seed_policy=policy))
        assert plan.seed_policy == policy


def test_generation_plan_invalid_seed_policy():
    with pytest.raises(ValidationError):
        GenerationPlan(**_minimal_plan(seed_policy="sequential"))


def test_generation_plan_reference_requirements():
    refs = ReferenceRequirements(
        character_refs=True, scene_ref=True, prev_frame=False, style_ref=True
    )
    plan = GenerationPlan(**_minimal_plan(reference_requirements=refs))
    assert plan.reference_requirements.character_refs is True
    assert plan.reference_requirements.scene_ref is True
    assert plan.reference_requirements.prev_frame is False
    assert plan.reference_requirements.style_ref is True


def test_generation_plan_selection_reason():
    plan = GenerationPlan(**_minimal_plan(selection_reason="has character refs"))
    assert plan.selection_reason == "has character refs"


def test_generation_plan_no_engine_family():
    plan = GenerationPlan(**_minimal_plan())
    assert not hasattr(plan, "engine_family"), "GenerationPlan must NOT have engine_family"


def test_generation_plan_no_workflow_profile():
    plan = GenerationPlan(**_minimal_plan())
    assert not hasattr(plan, "workflow_profile"), "GenerationPlan must NOT have workflow_profile"


def test_generation_plan_continuity_modes():
    for mode in ("none", "last_frame", "first_last"):
        plan = GenerationPlan(**_minimal_plan(continuity_mode=mode))
        assert plan.continuity_mode == mode
