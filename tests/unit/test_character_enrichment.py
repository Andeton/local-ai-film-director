"""Tests for character enrichment via ShotPlanner.

Regression test for the bug where WC produced generic placeholder characters
(主角/伙伴 with empty appearances) and reference generation received no
usable visual description.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from film_director.enrichment.shot_planner import (
    ShotPlanner,
    _is_character_deficient,
    _validate_character_enrichment,
)
from film_director.errors import EnrichmentError
from film_director.llm.provider import LLMResponse
from film_director.models.canonical import CharacterReference
from film_director.models.provenance import Provenance


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


APARTMENT_IDEA = (
    "A tense nighttime scene in a small New York apartment. "
    "An exhausted man sits alone at a kitchen table when he hears someone "
    "quietly trying to unlock the front door. He freezes, turns off the lamp, "
    "and watches the hallway. The door slowly opens and a woman steps inside "
    "holding a blood-stained envelope."
)


def _make_char(char_id, name, appearance=""):
    return CharacterReference(
        id=char_id, project_id="proj-1", wc_character_id=f"wc-{char_id}",
        name=name, description="", appearance=appearance, provenance=_prov(),
    )


# -----------------------------------------------------------------------
# _is_character_deficient
# -----------------------------------------------------------------------

class TestDeficiencyDetection:
    def test_empty_appearance_is_deficient(self):
        assert _is_character_deficient(_make_char("c1", "Alice", "")) is True

    def test_whitespace_appearance_is_deficient(self):
        assert _is_character_deficient(_make_char("c1", "Alice", "  ")) is True

    def test_generic_chinese_name_empty_appearance(self):
        assert _is_character_deficient(_make_char("c1", "主角", "")) is True

    def test_generic_chinese_name_short_appearance(self):
        assert _is_character_deficient(_make_char("c1", "伙伴", "short")) is True

    def test_real_name_with_appearance_not_deficient(self):
        assert _is_character_deficient(
            _make_char("c1", "Alice", "Tall woman with dark hair, late 30s, wearing a trench coat")
        ) is False

    def test_generic_name_with_full_appearance_not_deficient(self):
        """A generic name with a detailed appearance is usable."""
        assert _is_character_deficient(
            _make_char("c1", "主角", "Exhausted man in his 40s, disheveled brown hair, wrinkled shirt, dark circles under eyes")
        ) is False


# -----------------------------------------------------------------------
# _validate_character_enrichment
# -----------------------------------------------------------------------

class TestEnrichmentValidation:
    def test_valid_response(self):
        chars = [_make_char("c1", "主角"), _make_char("c2", "伙伴")]
        parsed = {"characters": [
            {"id": "c1", "display_name": "The Man",
             "appearance": "Exhausted man in his 40s, disheveled brown hair, wrinkled dress shirt"},
            {"id": "c2", "display_name": "The Woman",
             "appearance": "Woman in her 30s, dark coat, sharp features, holding a manila envelope"},
        ]}
        result, err = _validate_character_enrichment(parsed, chars)
        assert err is None
        assert "c1" in result
        assert result["c1"]["display_name"] == "The Man"
        assert "exhausted" in result["c1"]["appearance"].lower() or "40s" in result["c1"]["appearance"]

    def test_missing_key(self):
        _, err = _validate_character_enrichment({"data": []}, [])
        assert "missing" in err.lower()

    def test_empty_display_name_rejected(self):
        chars = [_make_char("c1", "主角")]
        parsed = {"characters": [
            {"id": "c1", "display_name": "", "appearance": "Some appearance description here"},
        ]}
        _, err = _validate_character_enrichment(parsed, chars)
        assert "empty display_name" in err

    def test_empty_appearance_rejected(self):
        chars = [_make_char("c1", "主角")]
        parsed = {"characters": [
            {"id": "c1", "display_name": "The Man", "appearance": ""},
        ]}
        _, err = _validate_character_enrichment(parsed, chars)
        assert "empty appearance" in err

    def test_too_short_appearance_rejected(self):
        chars = [_make_char("c1", "主角")]
        parsed = {"characters": [
            {"id": "c1", "display_name": "The Man", "appearance": "A man"},
        ]}
        _, err = _validate_character_enrichment(parsed, chars)
        assert "too short" in err


# -----------------------------------------------------------------------
# ShotPlanner.enrich_characters integration
# -----------------------------------------------------------------------

class TestEnrichCharactersIntegration:
    def _make_llm(self, response_data):
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(
            content=json.dumps(response_data),
            parsed=response_data,
            model="test",
        )
        return llm

    def test_enriches_deficient_characters(self):
        chars = [_make_char("c1", "主角"), _make_char("c2", "伙伴")]
        response = {"characters": [
            {"id": "c1", "display_name": "The Man",
             "appearance": "Exhausted man in his early 40s with disheveled brown hair and dark circles"},
            {"id": "c2", "display_name": "The Woman",
             "appearance": "Woman in her early 30s with sharp features and a dark trench coat"},
        ]}
        planner = ShotPlanner(self._make_llm(response))
        updated = planner.enrich_characters(chars, APARTMENT_IDEA)

        assert len(updated) == 2
        assert updated[0].id == "c1"  # ID preserved
        assert updated[0].name == "The Man"
        assert "40s" in updated[0].appearance
        assert updated[1].id == "c2"  # ID preserved
        assert updated[1].name == "The Woman"

    def test_preserves_ids(self):
        chars = [_make_char("c1", "主角")]
        response = {"characters": [
            {"id": "c1", "display_name": "The Man",
             "appearance": "Exhausted man with brown hair and tired eyes, wrinkled shirt"},
        ]}
        planner = ShotPlanner(self._make_llm(response))
        updated = planner.enrich_characters(chars, APARTMENT_IDEA)
        assert updated[0].id == "c1"
        assert updated[0].project_id == "proj-1"
        assert updated[0].wc_character_id == "wc-c1"

    def test_skips_good_characters(self):
        chars = [_make_char("c1", "Alice", "Tall woman with dark hair, late 30s")]
        planner = ShotPlanner(MagicMock())  # LLM should NOT be called
        updated = planner.enrich_characters(chars, APARTMENT_IDEA)
        assert updated == []

    def test_no_enrichment_without_description(self):
        chars = [_make_char("c1", "主角")]
        planner = ShotPlanner(MagicMock())
        updated = planner.enrich_characters(chars, "")
        assert updated == []

    def test_failed_enrichment_raises(self):
        chars = [_make_char("c1", "主角")]
        bad_response = {"bad": True}
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(
            content="{}", parsed=bad_response, model="test",
        )
        planner = ShotPlanner(llm)
        with pytest.raises(EnrichmentError, match="Character enrichment failed"):
            planner.enrich_characters(chars, APARTMENT_IDEA)

    def test_no_arbitrary_personal_names(self):
        """Display names should be role labels, not invented names."""
        chars = [_make_char("c1", "主角")]
        response = {"characters": [
            {"id": "c1", "display_name": "The Man",
             "appearance": "Exhausted man in his 40s with disheveled hair and dark circles"},
        ]}
        planner = ShotPlanner(self._make_llm(response))
        updated = planner.enrich_characters(chars, APARTMENT_IDEA)
        # The prompt instructs not to invent personal names
        # The display_name should be a role label like "The Man"
        assert updated[0].name == "The Man"

    def test_generic_chinese_names_replaced(self):
        chars = [_make_char("c1", "主角"), _make_char("c2", "伙伴")]
        response = {"characters": [
            {"id": "c1", "display_name": "The Man",
             "appearance": "Man in his 40s, tired eyes, rumpled shirt"},
            {"id": "c2", "display_name": "The Woman",
             "appearance": "Woman in her 30s, dark coat, intense gaze"},
        ]}
        planner = ShotPlanner(self._make_llm(response))
        updated = planner.enrich_characters(chars, APARTMENT_IDEA)
        # Generic Chinese names should be replaced
        assert updated[0].name != "主角"
        assert updated[1].name != "伙伴"


# -----------------------------------------------------------------------
# Guard: empty appearance blocks reference generation
# -----------------------------------------------------------------------

class TestEmptyAppearanceGuard:
    def test_generate_body_prompt_with_appearance(self):
        from film_director.generation.reference_generator import _build_prompt
        from film_director.models.reference import ReferenceKind
        prompt = _build_prompt("The Man", "Exhausted man in his 40s, disheveled brown hair", ReferenceKind.CHARACTER_BODY)
        assert "Exhausted man" in prompt
        assert "disheveled brown hair" in prompt

    def test_generate_body_prompt_empty_appearance(self):
        from film_director.generation.reference_generator import _build_prompt
        from film_director.models.reference import ReferenceKind
        prompt = _build_prompt("主角", "", ReferenceKind.CHARACTER_BODY)
        # The prompt would be useless — just "A character reference photo of 主角. ."
        assert "主角" in prompt
        # This is what the API guard protects against
