"""Unit tests for P4.2/P4.3 — Reference prompt preview and provenance.

Tests prompt preview endpoints, override passthrough, and generation request lookup.
"""
from __future__ import annotations

import pytest

from film_director.generation.reference_generator import (
    build_character_prompt,
    build_environment_prompt,
    DEFAULT_CHARACTER_NEGATIVE,
    DEFAULT_ENVIRONMENT_NEGATIVE,
)
from film_director.models.reference import (
    ReferenceKind,
    ReferenceGenerationRequest,
)


# ---------------------------------------------------------------------------
# Character prompt preview
# ---------------------------------------------------------------------------

class TestCharacterPromptPreview:
    def test_body_prompt_includes_appearance(self):
        prompt = build_character_prompt("Alice", "dark hair, tall", ReferenceKind.CHARACTER_BODY)
        assert "Alice" in prompt
        assert "dark hair, tall" in prompt
        assert "full body standing pose" in prompt

    def test_face_prompt_uses_headshot(self):
        prompt = build_character_prompt("Bob", "blonde", ReferenceKind.CHARACTER_FACE)
        assert "portrait headshot" in prompt
        assert "full body standing pose" not in prompt

    def test_default_negative_is_available(self):
        assert "watermark" in DEFAULT_CHARACTER_NEGATIVE
        assert "character sheet" in DEFAULT_CHARACTER_NEGATIVE


# ---------------------------------------------------------------------------
# Environment prompt preview
# ---------------------------------------------------------------------------

class TestEnvironmentPromptPreview:
    def test_env_prompt_includes_description(self):
        prompt = build_environment_prompt("dimly lit kitchen in a small apartment")
        assert "dimly lit kitchen" in prompt
        assert "production design reference" in prompt
        assert "no people" in prompt

    def test_env_negative_is_available(self):
        assert "people" in DEFAULT_ENVIRONMENT_NEGATIVE
        assert "person" in DEFAULT_ENVIRONMENT_NEGATIVE

    def test_env_negative_contains_generic_exclusions(self):
        """Generic empty-set exclusions must be present."""
        for term in ["people", "person", "human", "character", "figure",
                      "text", "watermark", "blurry", "action", "motion blur"]:
            assert term in DEFAULT_ENVIRONMENT_NEGATIVE, f"Missing generic exclusion: {term}"

    def test_env_negative_no_p3_story_content(self):
        """P3 story-specific terms must NOT appear in generic defaults (P4.2a)."""
        neg_lower = DEFAULT_ENVIRONMENT_NEGATIVE.lower()
        for term in ["police car", "blood", "envelope", "weapon",
                      "kitchen", "apartment", "new york"]:
            assert term not in neg_lower, f"Story-specific term in generic default: {term}"


# ---------------------------------------------------------------------------
# ReferenceGenerationRequest model supports ENVIRONMENT kind
# ---------------------------------------------------------------------------

class TestRefGenRequestEnvironment:
    def test_environment_kind_accepted(self):
        req = ReferenceGenerationRequest(
            id="rgreq_test",
            project_id="proj-1",
            character_id="__environment__",
            requested_kind=ReferenceKind.ENVIRONMENT,
            source_appearance_hash="a" * 64,
            prompt="test prompt",
            negative_prompt="test negative",
            workflow_definition_id="z_image_turbo_v1",
            workflow_definition_version="1.0.0",
            workflow_template_fingerprint="b" * 64,
            seed=42,
        )
        assert req.requested_kind == ReferenceKind.ENVIRONMENT
        assert req.prompt == "test prompt"
        assert req.negative_prompt == "test negative"

    def test_prompt_fields_persisted(self):
        req = ReferenceGenerationRequest(
            id="rgreq_test2",
            project_id="proj-1",
            character_id="__environment__",
            requested_kind=ReferenceKind.ENVIRONMENT,
            source_appearance_hash="a" * 64,
            prompt="custom prompt text",
            negative_prompt="custom negative text",
            workflow_definition_id="z_image_turbo_v1",
            workflow_definition_version="1.0.0",
            workflow_template_fingerprint="b" * 64,
            seed=123,
        )
        d = req.model_dump()
        assert d["prompt"] == "custom prompt text"
        assert d["negative_prompt"] == "custom negative text"
        assert d["seed"] == 123


# ---------------------------------------------------------------------------
# User upload has no generation prompt
# ---------------------------------------------------------------------------

class TestUserUploadNoPrompt:
    def test_user_upload_source_has_no_request(self):
        """USER_UPLOAD ReferenceAssets do not have a ReferenceGenerationRequest."""
        from film_director.models.reference import (
            ReferenceAsset,
            ReferenceSource,
            ReferenceSourceState,
            ReferenceStatus,
        )
        asset = ReferenceAsset(
            id="ref_upload_test",
            project_id="proj-1",
            character_id="char-1",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.USER_UPLOAD,
            managed_path="references/test/original.png",
            content_sha256="c" * 64,
            source_provenance="user_upload",
            status=ReferenceStatus.CANDIDATE,
            source_state=ReferenceSourceState.CURRENT,
            width=512,
            height=512,
            created_at="2026-01-01",
            updated_at="2026-01-01",
        )
        assert asset.source == ReferenceSource.USER_UPLOAD
        # source_provenance for uploads is NOT a rgreq_ ID
        assert not asset.source_provenance.startswith("rgreq_")
