"""Tests for H3PromptV1 Pydantic model."""
import pytest
from pydantic import ValidationError

from film_director.generation.h3_prompt import H3PromptV1


def _valid_prompt(**overrides) -> H3PromptV1:
    defaults = dict(
        id="prompt-1",
        shot_id="shot-1",
        generation_plan_id="plan-1",
        source_shot_version=1,
        source_generation_plan_version=1,
        subject_definitions="Subject 1: Alice",
        summary="A quiet moment",
        retention_analysis="No changes from prior shot",
        detailed_description="Alice sits at a table in afternoon light",
        rendered_prompt_text="Alice sits at table, afternoon light, cinematic",
    )
    defaults.update(overrides)
    return H3PromptV1(**defaults)


def test_h3_prompt_valid_construction():
    p = _valid_prompt()
    assert p.id == "prompt-1"
    assert p.status == "current"
    assert p.version == 1
    assert p.overall_soundscape == ""
    assert p.non_diegetic_music == ""


def test_h3_prompt_valid_status_current():
    p = _valid_prompt(status="current")
    assert p.status == "current"


def test_h3_prompt_valid_status_stale():
    p = _valid_prompt(status="stale")
    assert p.status == "stale"


def test_h3_prompt_invalid_status_rejected():
    with pytest.raises(ValidationError):
        _valid_prompt(status="done")


def test_h3_prompt_version_below_one_rejected():
    with pytest.raises(ValidationError):
        _valid_prompt(version=0)


def test_h3_prompt_empty_summary_rejected():
    with pytest.raises(ValidationError):
        _valid_prompt(summary="")


def test_h3_prompt_empty_rendered_prompt_text_rejected():
    with pytest.raises(ValidationError):
        _valid_prompt(rendered_prompt_text="   ")


def test_h3_prompt_source_shot_version_required_positive():
    with pytest.raises(ValidationError):
        _valid_prompt(source_shot_version=0)


def test_h3_prompt_source_generation_plan_version_required_positive():
    with pytest.raises(ValidationError):
        _valid_prompt(source_generation_plan_version=0)


def test_h3_prompt_optional_soundscape_fields():
    p = _valid_prompt(overall_soundscape="ambient rain", non_diegetic_music="soft piano")
    assert p.overall_soundscape == "ambient rain"
    assert p.non_diegetic_music == "soft piano"
