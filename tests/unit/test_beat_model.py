"""Tests for Beat canonical model (M2)."""
import pytest
from pydantic import ValidationError

from film_director.models.canonical import Beat


def _minimal_beat(**overrides) -> dict:
    base = {
        "id": "beat-1",
        "scene_id": "scene-1",
        "dramatic_action": "Hero confronts villain",
        "character_intention": "Protect the team",
        "change": "Fear turns to resolve",
        "order_index": 0,
    }
    base.update(overrides)
    return base


def test_beat_valid():
    beat = Beat(**_minimal_beat())
    assert beat.status == "draft"
    assert beat.source == "llm"
    assert beat.version == 1
    assert beat.characters == []
    assert beat.created_at == ""
    assert beat.updated_at == ""


def test_beat_all_valid_statuses():
    for status in ("draft", "approved", "outdated"):
        beat = Beat(**_minimal_beat(status=status))
        assert beat.status == status


def test_beat_invalid_status():
    with pytest.raises(ValidationError):
        Beat(**_minimal_beat(status="invalid"))


def test_beat_valid_sources():
    for source in ("llm", "human"):
        beat = Beat(**_minimal_beat(source=source))
        assert beat.source == source


def test_beat_invalid_source():
    with pytest.raises(ValidationError):
        Beat(**_minimal_beat(source="gpt"))


def test_beat_characters_list():
    chars = ["char-1", "char-2", "char-3"]
    beat = Beat(**_minimal_beat(characters=chars))
    assert beat.characters == chars


def test_beat_mutable_default_safety():
    beat_a = Beat(**_minimal_beat())
    beat_b = Beat(**_minimal_beat())
    beat_a.characters.append("char-99")
    assert beat_b.characters == [], "characters list must be independent per instance"
