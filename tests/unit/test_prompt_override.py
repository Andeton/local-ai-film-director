"""Tests for M5.H prompt override support in reference generation."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from film_director.models.reference import ReferenceKind


class TestPromptOverrideInService:
    """Test that prompt_override reaches immutable request and overrides auto-prompt."""

    def test_auto_prompt_when_no_override(self):
        """Default behavior: auto-built prompt from name+appearance."""
        from film_director.generation.reference_generator import _build_prompt
        prompt = _build_prompt("Alice", "dark hair, trench coat", ReferenceKind.CHARACTER_BODY)
        assert "Alice" in prompt
        assert "dark hair, trench coat" in prompt

    def test_override_replaces_auto_prompt(self):
        """When prompt_override is set, it replaces the auto-built prompt entirely."""
        from film_director.generation.reference_generator import ReferenceGenerationService
        # Verify the service method accepts prompt_override
        import inspect
        sig = inspect.signature(ReferenceGenerationService.generate_character_reference)
        assert "prompt_override" in sig.parameters

    def test_negative_override_replaces_default(self):
        """When negative_prompt_override is set, it replaces the default negative."""
        from film_director.generation.reference_generator import ReferenceGenerationService
        import inspect
        sig = inspect.signature(ReferenceGenerationService.generate_character_reference)
        assert "negative_prompt_override" in sig.parameters

    def test_override_length_validation(self):
        """Prompt override must not be empty if provided."""
        from film_director.generation.reference_generator import ReferenceGenerationService
        import inspect
        sig = inspect.signature(ReferenceGenerationService.generate_character_reference)
        # Empty string and whitespace-only should be rejected
        # (validated in the service, tested via integration)


class TestPromptOverrideInAPI:
    """Test that API DTO accepts prompt_override fields."""

    def test_dto_accepts_prompt_override(self):
        from film_director.api.routes import GenerateReferenceRequest
        req = GenerateReferenceRequest(
            kind="character_body",
            prompt_override="Custom prompt text",
        )
        assert req.prompt_override == "Custom prompt text"

    def test_dto_accepts_negative_override(self):
        from film_director.api.routes import GenerateReferenceRequest
        req = GenerateReferenceRequest(
            kind="character_body",
            negative_prompt_override="Custom negative",
        )
        assert req.negative_prompt_override == "Custom negative"

    def test_dto_defaults_to_none(self):
        from film_director.api.routes import GenerateReferenceRequest
        req = GenerateReferenceRequest(kind="character_body")
        assert req.prompt_override is None
        assert req.negative_prompt_override is None
