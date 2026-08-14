"""Tests for BeatEnricher (M2.C) — TDD-first, all mocked LLM."""
import pytest

from film_director.errors import EnrichmentError, LLMStructuredOutputError, LLMUnavailableError
from film_director.llm.provider import LLMResponse
from film_director.models.canonical import Beat, Scene
from film_director.models.provenance import Provenance
from film_director.enrichment.beat_enricher import BeatEnricher


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------

class FakeLLMProvider:
    def __init__(self):
        self._responses: list = []
        self.call_count = 0
        self.last_messages = None
        self.last_expect_json = None

    def queue(self, response_or_exception):
        self._responses.append(response_or_exception)

    def chat(self, messages, expect_json=False):
        self.call_count += 1
        self.last_messages = messages
        self.last_expect_json = expect_json
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


def _make_response(beats_payload, raw: str = "") -> LLMResponse:
    return LLMResponse(
        content=raw or str(beats_payload),
        parsed=beats_payload,
        model="test-model",
    )


def _valid_beat_dict(**overrides) -> dict:
    base = {
        "dramatic_action": "Hero confronts villain",
        "character_intention": "Protect the team",
        "change": "Fear turns to resolve",
        "characters": ["hero", "villain"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Success tests
# ---------------------------------------------------------------------------

def test_valid_single_beat():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    scene = _make_scene()
    beats = enricher.enrich_scene(scene)
    assert len(beats) == 1
    assert isinstance(beats[0], Beat)


def test_valid_multiple_beats():
    llm = FakeLLMProvider()
    beat_dicts = [
        _valid_beat_dict(dramatic_action="Action one"),
        _valid_beat_dict(dramatic_action="Action two"),
        _valid_beat_dict(dramatic_action="Action three"),
    ]
    llm.queue(_make_response({"beats": beat_dicts}))
    enricher = BeatEnricher(llm)
    beats = enricher.enrich_scene(_make_scene())
    assert len(beats) == 3
    assert [b.order_index for b in beats] == [0, 1, 2]
    ids = [b.id for b in beats]
    assert len(set(ids)) == 3, "All beat IDs must be distinct"


def test_scene_fields_in_prompt():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    scene = _make_scene(
        name="Night Chase",
        location="Dark alley",
        description="Pursuit through winding streets",
    )
    enricher.enrich_scene(scene)
    all_text = " ".join(
        m.get("content", "") for m in llm.last_messages
    )
    assert "Night Chase" in all_text
    assert "Dark alley" in all_text
    assert "Pursuit through winding streets" in all_text


def test_script_context_included():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    context = {"theme": "redemption", "genre": "thriller"}
    enricher.enrich_scene(_make_scene(), script_context=context)
    all_text = " ".join(
        m.get("content", "") for m in llm.last_messages
    )
    assert "redemption" in all_text or "thriller" in all_text


def test_script_context_absent():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    # Should work fine with no context
    beats = enricher.enrich_scene(_make_scene(), script_context=None)
    assert len(beats) >= 1


def test_beat_attributes():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    scene = _make_scene(id="scene-xyz999")
    beats = enricher.enrich_scene(scene)
    b = beats[0]
    assert b.status == "draft"
    assert b.source == "llm"
    assert b.version == 1
    assert b.scene_id == "scene-xyz999"
    assert b.id.startswith("beat")
    assert len(b.id) > 4  # prefix + at least some hex


# ---------------------------------------------------------------------------
# Domain repair tests
# ---------------------------------------------------------------------------

def test_missing_beats_key_triggers_repair():
    llm = FakeLLMProvider()
    # First response: wrong key
    llm.queue(_make_response({"wrong_key": [_valid_beat_dict()]}))
    # Second response: valid
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    beats = enricher.enrich_scene(_make_scene())
    assert len(beats) == 1
    assert llm.call_count == 2


def test_beats_wrong_type_triggers_repair():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": "not a list"}))
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    beats = enricher.enrich_scene(_make_scene())
    assert len(beats) >= 1
    assert llm.call_count == 2


def test_empty_beats_triggers_repair():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": []}))
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    beats = enricher.enrich_scene(_make_scene())
    assert len(beats) >= 1
    assert llm.call_count == 2


def test_invalid_beat_item_triggers_repair():
    llm = FakeLLMProvider()
    # dramatic_action is empty string — fails domain validation
    llm.queue(_make_response({"beats": [{"dramatic_action": ""}]}))
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    beats = enricher.enrich_scene(_make_scene())
    assert len(beats) >= 1
    assert llm.call_count == 2


def test_two_invalid_raises_enrichment_error():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": []}))
    llm.queue(_make_response({"beats": []}))
    enricher = BeatEnricher(llm)
    with pytest.raises(EnrichmentError):
        enricher.enrich_scene(_make_scene())
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# Provider error propagation
# ---------------------------------------------------------------------------

def test_structured_output_error_propagates():
    llm = FakeLLMProvider()
    llm.queue(LLMStructuredOutputError("Cannot parse"))
    enricher = BeatEnricher(llm)
    with pytest.raises(LLMStructuredOutputError):
        enricher.enrich_scene(_make_scene())
    assert llm.call_count == 1


def test_unavailable_error_propagates():
    llm = FakeLLMProvider()
    llm.queue(LLMUnavailableError("LLM offline"))
    enricher = BeatEnricher(llm)
    with pytest.raises(LLMUnavailableError):
        enricher.enrich_scene(_make_scene())
    assert llm.call_count == 1


# ---------------------------------------------------------------------------
# Call count tests
# ---------------------------------------------------------------------------

def test_valid_first_response_one_call():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    enricher.enrich_scene(_make_scene())
    assert llm.call_count == 1


def test_repair_exactly_two_calls():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": "wrong"}))
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    enricher.enrich_scene(_make_scene())
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# Input safety
# ---------------------------------------------------------------------------

def test_scene_not_mutated():
    llm = FakeLLMProvider()
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    scene = _make_scene(name="Original Name", location="Original Location")
    original_name = scene.name
    original_location = scene.location
    original_id = scene.id
    enricher.enrich_scene(scene)
    assert scene.name == original_name
    assert scene.location == original_location
    assert scene.id == original_id


def test_expect_json_always_true():
    llm = FakeLLMProvider()
    # Two calls: initial + repair
    llm.queue(_make_response({"beats": "bad"}))
    llm.queue(_make_response({"beats": [_valid_beat_dict()]}))
    enricher = BeatEnricher(llm)
    enricher.enrich_scene(_make_scene())
    # last_expect_json is from the last call (repair call)
    assert llm.last_expect_json is True
