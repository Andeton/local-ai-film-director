"""Tests for CoveragePlanner (M2.D) — TDD-first, all mocked LLM."""
import copy

import pytest

from film_director.errors import EnrichmentError, LLMStructuredOutputError, LLMUnavailableError
from film_director.llm.provider import LLMResponse
from film_director.models.canonical import Beat, Scene
from film_director.models.provenance import Provenance
from film_director.enrichment.coverage_planner import CoveragePlanner, CoverageDecision


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------

class FakeLLMProvider:
    def __init__(self):
        self._responses: list = []
        self.call_count = 0
        self.last_messages = None
        self.last_expect_json = None
        self.all_expect_json: list[bool] = []

    def queue(self, response_or_exception):
        self._responses.append(response_or_exception)

    def chat(self, messages, expect_json=False):
        self.call_count += 1
        self.last_messages = messages
        self.last_expect_json = expect_json
        self.all_expect_json.append(expect_json)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def health(self):
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provenance() -> Provenance:
    return Provenance(
        source_system="wind_comic",
        source_project_id="proj-001",
        source_asset_id="asset-001",
        source_asset_version=1,
        imported_at="2024-01-01T00:00:00+00:00",
        source_hash="a" * 64,
    )


def _make_scene(**overrides) -> Scene:
    base = dict(
        id="scene-abc123",
        sequence_id="seq-001",
        wc_scene_id="wc-scene-001",
        name="The Confrontation",
        location="Rooftop at night",
        description="Hero faces villain in final showdown",
        order_index=0,
        status="draft",
        provenance=_make_provenance(),
    )
    base.update(overrides)
    return Scene(**base)


def _make_beat(**overrides) -> Beat:
    base = dict(
        id="beat-abc123",
        scene_id="scene-abc123",
        dramatic_action="Hero confronts villain",
        character_intention="Protect the team",
        change="Fear turns to resolve",
        characters=["Hero", "Villain"],
        order_index=0,
        status="draft",
        source="llm",
        version=1,
    )
    base.update(overrides)
    return Beat(**base)


def _make_response(payload, raw: str = "") -> LLMResponse:
    return LLMResponse(
        content=raw or str(payload),
        parsed=payload,
        model="test-model",
    )


def _valid_coverage_item(**overrides) -> dict:
    base = {
        "shot_type": "establishing",
        "shot_size": "wide",
        "angle": "high",
        "movement": "pan_left",
        "purpose": "Set the scene",
        "duration_sec": 4.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Valid coverage list
# ---------------------------------------------------------------------------

def test_valid_coverage_returns_decisions():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 1
    assert isinstance(result[0], CoverageDecision)


# ---------------------------------------------------------------------------
# 2. Multiple items preserve order
# ---------------------------------------------------------------------------

def test_multiple_items_preserve_order():
    llm = FakeLLMProvider()
    items = [
        _valid_coverage_item(purpose="First"),
        _valid_coverage_item(purpose="Second"),
        _valid_coverage_item(purpose="Third"),
    ]
    llm.queue(_make_response({"coverage": items}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 3
    assert [r.purpose for r in result] == ["First", "Second", "Third"]


# ---------------------------------------------------------------------------
# 3. All 7 shot_size values accepted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [
    "extreme_wide", "wide", "medium_wide", "medium",
    "medium_close", "close_up", "extreme_close",
])
def test_all_shot_sizes_accepted(size):
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item(shot_size=size)]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert result[0].shot_size == size


# ---------------------------------------------------------------------------
# 4. Invalid shot_size triggers domain repair
# ---------------------------------------------------------------------------

def test_invalid_shot_size_triggers_repair():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item(shot_size="bogus")]}))
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 1
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 5. Missing "coverage" key triggers repair
# ---------------------------------------------------------------------------

def test_missing_coverage_key_triggers_repair():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"wrong_key": [_valid_coverage_item()]}))
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 1
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 6. Empty coverage list triggers repair
# ---------------------------------------------------------------------------

def test_empty_coverage_list_triggers_repair():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": []}))
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 1
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 7. Wrong coverage type triggers repair
# ---------------------------------------------------------------------------

def test_wrong_coverage_type_triggers_repair():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": "not a list"}))
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 1
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 8. Invalid item (empty purpose) triggers repair
# ---------------------------------------------------------------------------

def test_invalid_item_empty_purpose_triggers_repair():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item(purpose="")]}))
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 1
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 9. First invalid + second valid = success, 2 calls
# ---------------------------------------------------------------------------

def test_first_invalid_second_valid():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item(duration_sec=-1)]}))
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    result = planner.plan_coverage(_make_beat(), _make_scene())
    assert len(result) == 1
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 10. First invalid + second invalid = EnrichmentError, 2 calls
# ---------------------------------------------------------------------------

def test_two_invalid_raises_enrichment_error():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": []}))
    llm.queue(_make_response({"coverage": []}))
    planner = CoveragePlanner(llm)
    with pytest.raises(EnrichmentError):
        planner.plan_coverage(_make_beat(), _make_scene())
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 11. Max high-level calls = 2
# ---------------------------------------------------------------------------

def test_max_two_calls():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": "bad"}))
    llm.queue(_make_response({"coverage": "bad"}))
    planner = CoveragePlanner(llm)
    with pytest.raises(EnrichmentError):
        planner.plan_coverage(_make_beat(), _make_scene())
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# 12. Valid first response = 1 call
# ---------------------------------------------------------------------------

def test_valid_first_response_one_call():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    planner.plan_coverage(_make_beat(), _make_scene())
    assert llm.call_count == 1


# ---------------------------------------------------------------------------
# 13. LLMStructuredOutputError propagates, 1 call
# ---------------------------------------------------------------------------

def test_structured_output_error_propagates():
    llm = FakeLLMProvider()
    llm.queue(LLMStructuredOutputError("Cannot parse"))
    planner = CoveragePlanner(llm)
    with pytest.raises(LLMStructuredOutputError):
        planner.plan_coverage(_make_beat(), _make_scene())
    assert llm.call_count == 1


# ---------------------------------------------------------------------------
# 14. LLMUnavailableError propagates, 1 call
# ---------------------------------------------------------------------------

def test_unavailable_error_propagates():
    llm = FakeLLMProvider()
    llm.queue(LLMUnavailableError("LLM offline"))
    planner = CoveragePlanner(llm)
    with pytest.raises(LLMUnavailableError):
        planner.plan_coverage(_make_beat(), _make_scene())
    assert llm.call_count == 1


# ---------------------------------------------------------------------------
# 15. Every call uses expect_json=True
# ---------------------------------------------------------------------------

def test_expect_json_always_true():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": "bad"}))
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    planner.plan_coverage(_make_beat(), _make_scene())
    assert all(llm.all_expect_json)


# ---------------------------------------------------------------------------
# 16. Beat + Scene fields in request
# ---------------------------------------------------------------------------

def test_beat_scene_fields_in_request():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    beat = _make_beat(dramatic_action="Hero leaps across rooftop")
    scene = _make_scene(name="Night Chase", location="Dark alley")
    planner.plan_coverage(beat, scene)
    all_text = " ".join(m.get("content", "") for m in llm.last_messages)
    assert "Hero leaps across rooftop" in all_text
    assert "Night Chase" in all_text
    assert "Dark alley" in all_text


# ---------------------------------------------------------------------------
# 17. Beat/Scene inputs unchanged
# ---------------------------------------------------------------------------

def test_inputs_not_mutated():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"coverage": [_valid_coverage_item()]}))
    planner = CoveragePlanner(llm)
    beat = _make_beat()
    scene = _make_scene()
    beat_copy = copy.deepcopy(beat)
    scene_copy = copy.deepcopy(scene)
    planner.plan_coverage(beat, scene)
    assert beat.id == beat_copy.id
    assert beat.dramatic_action == beat_copy.dramatic_action
    assert scene.name == scene_copy.name
    assert scene.location == scene_copy.location
