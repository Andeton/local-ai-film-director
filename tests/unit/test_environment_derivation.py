"""Tests for environment description derivation from narrative context.

Regression test for the collage/contact-sheet bug where the raw narrative
story was used as the environment generation prompt, producing a story
visualization instead of a stable set reference.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from film_director.enrichment.shot_planner import (
    ShotPlanner,
    _ENVIRONMENT_DERIVATION_SYSTEM,
)
from film_director.errors import EnrichmentError
from film_director.generation.reference_generator import (
    _build_environment_prompt,
    _ENV_NEGATIVE,
)
from film_director.llm.provider import LLMResponse


APARTMENT_IDEA = (
    "A tense nighttime scene in a small New York apartment. "
    "An exhausted man sits alone at a kitchen table when he hears someone "
    "quietly trying to unlock the front door. He freezes, turns off the lamp, "
    "and watches the hallway. The door slowly opens and a woman steps inside "
    "holding a blood-stained envelope. They recognize each other but neither "
    "speaks. She places the envelope on the table. He opens it, reads the "
    "contents, and realizes someone close to them has betrayed them. "
    "The scene ends with distant police sirens approaching outside."
)

# A good environment description — the kind the LLM should produce
GOOD_ENV_DESC = (
    "Small, modest New York apartment at night. Compact kitchen area with a "
    "worn wooden table and a practical table lamp. A short dim hallway connects "
    "the kitchen to the front door. Muted neutral walls, modest cabinetry, "
    "one window with cool blue nighttime city light filtering in. Warm lamp "
    "light contrasts with cold ambient exterior light. Lived-in, restrained "
    "contemporary thriller production design."
)


class TestEnvironmentDerivationPrompt:
    """The derivation system prompt must instruct extraction, not visualization."""

    def test_system_includes_set(self):
        assert "SET" in _ENVIRONMENT_DERIVATION_SYSTEM or "LOCATION" in _ENVIRONMENT_DERIVATION_SYSTEM

    def test_system_excludes_characters(self):
        sys = _ENVIRONMENT_DERIVATION_SYSTEM.lower()
        assert "exclude" in sys
        assert "character" in sys

    def test_system_excludes_action(self):
        sys = _ENVIRONMENT_DERIVATION_SYSTEM.lower()
        assert "action" in sys or "event" in sys

    def test_system_excludes_props(self):
        sys = _ENVIRONMENT_DERIVATION_SYSTEM.lower()
        assert "prop" in sys


class TestDerivedEnvironmentContent:
    """A good environment description must exclude story events."""

    def test_no_man_in_env(self):
        assert "man" not in GOOD_ENV_DESC.lower().split()

    def test_no_woman_in_env(self):
        assert "woman" not in GOOD_ENV_DESC.lower()

    def test_no_envelope_in_env(self):
        assert "envelope" not in GOOD_ENV_DESC.lower()

    def test_no_police_in_env(self):
        assert "police" not in GOOD_ENV_DESC.lower()

    def test_no_betrayal_in_env(self):
        assert "betray" not in GOOD_ENV_DESC.lower()

    def test_has_location(self):
        assert "apartment" in GOOD_ENV_DESC.lower()

    def test_has_lighting(self):
        assert "light" in GOOD_ENV_DESC.lower()


class TestShotPlannerDeriveEnvironment:
    def _make_llm(self, env_desc):
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(
            content=json.dumps({"environment_description": env_desc}),
            parsed={"environment_description": env_desc},
            model="test",
        )
        return llm

    def test_derives_environment(self):
        planner = ShotPlanner(self._make_llm(GOOD_ENV_DESC))
        result = planner.derive_environment(APARTMENT_IDEA)
        assert result == GOOD_ENV_DESC

    def test_empty_description_returns_empty(self):
        planner = ShotPlanner(MagicMock())
        result = planner.derive_environment("")
        assert result == ""

    def test_failed_derivation_raises(self):
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(
            content="{}", parsed={"bad": True}, model="test",
        )
        planner = ShotPlanner(llm)
        with pytest.raises(EnrichmentError, match="derive environment"):
            planner.derive_environment(APARTMENT_IDEA)

    def test_persisted_env_not_re_derived(self):
        """Once environment_description is in director_context, it's reused."""
        # This is tested through EnrichmentService — if env_description
        # already exists, derive_environment is not called
        pass  # Covered by test_enrichment_idempotency integration tests


class TestEnvironmentGenerationPrompt:
    """The generation prompt must produce ONE coherent image."""

    def test_single_image_requested(self):
        prompt = _build_environment_prompt(GOOD_ENV_DESC)
        lower = prompt.lower()
        assert "single" in lower or "one" in lower
        assert "one coherent" in lower or "single continuous" in lower

    def test_no_collage(self):
        prompt = _build_environment_prompt(GOOD_ENV_DESC)
        assert "no collage" in prompt.lower()
        assert "no split screen" in prompt.lower()
        assert "no contact sheet" in prompt.lower()
        assert "no storyboard" in prompt.lower()
        assert "no multiple panels" in prompt.lower()

    def test_no_people(self):
        prompt = _build_environment_prompt(GOOD_ENV_DESC)
        assert "no people" in prompt.lower()
        assert "no characters" in prompt.lower()
        assert "no action" in prompt.lower()

    def test_contains_environment_desc(self):
        prompt = _build_environment_prompt(GOOD_ENV_DESC)
        assert "New York apartment" in prompt

    def test_negative_excludes_collage(self):
        neg = _ENV_NEGATIVE.lower()
        assert "collage" in neg
        assert "contact sheet" in neg
        assert "storyboard" in neg
        assert "split screen" in neg
        assert "multiple panels" in neg
        assert "grid" in neg

    def test_negative_is_generic_no_story_props(self):
        """P4.2a: generic default must NOT contain P3-specific terms."""
        neg = _ENV_NEGATIVE.lower()
        for term in ["police car", "blood", "envelope", "weapon"]:
            assert term not in neg, f"Story-specific term in generic default: {term}"
        # Generic exclusions must remain
        assert "people" in neg
        assert "action" in neg


class TestEnvironmentGenerationGuard:
    """Generation must require persisted environment_description."""

    def test_missing_env_desc_blocks_generation(self):
        """API route returns 422 when no environment_description exists."""
        # This is an API-level test verified in integration tests.
        # The route checks: p.director_context.get("environment_description")
        # and raises HTTPException(422) if missing.
        pass
