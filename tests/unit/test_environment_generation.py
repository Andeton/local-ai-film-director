"""Tests for environment reference generation.

Verifies that environment references are generated from project context,
exclude characters/action, use correct ownership, and follow the lifecycle.
"""
from __future__ import annotations

import pytest

from film_director.generation.reference_generator import (
    _build_environment_prompt,
    _ENV_NEGATIVE,
)
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)


APARTMENT_DESC = (
    "A tense nighttime scene in a small New York apartment. "
    "An exhausted man sits alone at a kitchen table. "
    "Kitchen area, hallway, and front door visible."
)


class TestEnvironmentPrompt:
    def test_includes_location_description(self):
        prompt = _build_environment_prompt(APARTMENT_DESC)
        assert "New York apartment" in prompt
        assert "kitchen" in prompt.lower()

    def test_excludes_characters(self):
        prompt = _build_environment_prompt(APARTMENT_DESC)
        assert "no people" in prompt.lower()
        assert "no characters" in prompt.lower()
        assert "no action" in prompt.lower()

    def test_requests_empty_interior(self):
        prompt = _build_environment_prompt(APARTMENT_DESC)
        assert "empty interior" in prompt.lower() or "establishing shot" in prompt.lower()

    def test_cinematic_quality(self):
        prompt = _build_environment_prompt(APARTMENT_DESC)
        assert "cinematic" in prompt.lower()

    def test_negative_excludes_people(self):
        assert "people" in _ENV_NEGATIVE
        assert "person" in _ENV_NEGATIVE
        assert "human" in _ENV_NEGATIVE
        assert "character" in _ENV_NEGATIVE


class TestEnvironmentAssetModel:
    def test_environment_kind_valid(self):
        asset = ReferenceAsset(
            id="ref-env-1", project_id="proj-1",
            kind=ReferenceKind.ENVIRONMENT,
            source=ReferenceSource.GENERATED,
            managed_path="references/proj-1/ref-env-1/original.png",
            content_sha256="a" * 64,
            source_provenance="env_gen_test",
            width=1024, height=1024,
        )
        assert asset.kind == ReferenceKind.ENVIRONMENT
        assert asset.character_id is None
        assert asset.shot_id is None
        assert asset.status == ReferenceStatus.CANDIDATE

    def test_environment_rejects_character_id(self):
        with pytest.raises(ValueError, match="environment must not have character_id"):
            ReferenceAsset(
                id="ref-env-bad", project_id="proj-1",
                character_id="char-1",
                kind=ReferenceKind.ENVIRONMENT,
                source=ReferenceSource.GENERATED,
                managed_path="test.png", content_sha256="a" * 64,
                source_provenance="test", width=1, height=1,
            )

    def test_generated_starts_candidate(self):
        asset = ReferenceAsset(
            id="ref-env-2", project_id="proj-1",
            kind=ReferenceKind.ENVIRONMENT,
            source=ReferenceSource.GENERATED,
            managed_path="references/proj-1/ref-env-2/original.png",
            content_sha256="b" * 64,
            source_provenance="test",
            width=1024, height=1024,
        )
        assert asset.status == ReferenceStatus.CANDIDATE

    def test_upload_env_also_valid(self):
        asset = ReferenceAsset(
            id="ref-env-up", project_id="proj-1",
            kind=ReferenceKind.ENVIRONMENT,
            source=ReferenceSource.USER_UPLOAD,
            managed_path="references/proj-1/ref-env-up/original.png",
            content_sha256="c" * 64,
            source_provenance="upload-test",
            width=800, height=600,
        )
        assert asset.source == ReferenceSource.USER_UPLOAD


class TestEnvironmentReadiness:
    """Environment readiness requires APPROVED + CURRENT environment ref."""

    def test_candidate_env_not_ready(self):
        asset = ReferenceAsset(
            id="ref-1", project_id="proj-1",
            kind=ReferenceKind.ENVIRONMENT,
            source=ReferenceSource.GENERATED,
            managed_path="test.png", content_sha256="a" * 64,
            source_provenance="test", width=1, height=1,
            status=ReferenceStatus.CANDIDATE,
            source_state=ReferenceSourceState.CURRENT,
        )
        assert asset.status != ReferenceStatus.APPROVED

    def test_approved_env_ready(self):
        asset = ReferenceAsset(
            id="ref-1", project_id="proj-1",
            kind=ReferenceKind.ENVIRONMENT,
            source=ReferenceSource.GENERATED,
            managed_path="test.png", content_sha256="a" * 64,
            source_provenance="test", width=1, height=1,
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
        )
        assert asset.status == ReferenceStatus.APPROVED
        assert asset.source_state == ReferenceSourceState.CURRENT
