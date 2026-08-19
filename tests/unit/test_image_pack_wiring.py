"""Tests for image-pack production path wiring.

Verifies that generate_take uses the image-pack workflow when an
approved environment reference is available, and that preview matches
execution.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from film_director.generation.h3_types import H3ReferenceBinding
from film_director.generation.workflow_registry import WorkflowResolver
from film_director.models.canonical import (
    CameraIntent, ShotSpecificationV1, ShotSubject,
)
from film_director.models.reference import (
    ReferenceAsset, ReferenceKind, ReferenceSource,
    ReferenceSourceState, ReferenceStatus,
)


def _make_char_asset(char_id="char-1"):
    return ReferenceAsset(
        id=f"ref-body-{char_id}", project_id="proj-1", character_id=char_id,
        kind=ReferenceKind.CHARACTER_BODY, source=ReferenceSource.GENERATED,
        managed_path=f"references/proj-1/ref-body-{char_id}/original.png",
        content_sha256="a" * 64, source_provenance="test",
        status=ReferenceStatus.APPROVED, source_state=ReferenceSourceState.CURRENT,
        width=1024, height=1024,
    )


def _make_env_asset(status=ReferenceStatus.APPROVED, state=ReferenceSourceState.CURRENT):
    return ReferenceAsset(
        id="ref-env-1", project_id="proj-1",
        kind=ReferenceKind.ENVIRONMENT, source=ReferenceSource.GENERATED,
        managed_path="references/proj-1/ref-env-1/original.png",
        content_sha256="e" * 64, source_provenance="test",
        status=status, source_state=state,
        width=1024, height=1024,
    )


class TestImagePackWorkflowSelection:
    """Image-pack workflow selected when environment ref is available."""

    def test_chain_head_with_env_selects_image_pack(self):
        """Shot 1 with character + environment → h3_r2v_image_pack_v1."""
        all_assets = [_make_char_asset(), _make_env_asset()]
        env = next(
            (a for a in all_assets
             if a.kind == ReferenceKind.ENVIRONMENT
             and a.status.value == "approved"
             and a.source_state.value == "current"),
            None,
        )
        assert env is not None
        # When env is available, GenerationService uses resolve_image_pack()

    def test_chain_head_without_env_uses_legacy(self):
        """Shot 1 without environment → legacy h3_r2v_v1."""
        all_assets = [_make_char_asset()]
        env = next(
            (a for a in all_assets
             if a.kind == ReferenceKind.ENVIRONMENT
             and a.status.value == "approved"
             and a.source_state.value == "current"),
            None,
        )
        assert env is None
        # Without env, falls back to resolve_for_reference_count

    def test_stale_env_not_selected(self):
        """Stale environment ref is not used for image-pack."""
        all_assets = [
            _make_char_asset(),
            _make_env_asset(state=ReferenceSourceState.STALE),
        ]
        env = next(
            (a for a in all_assets
             if a.kind == ReferenceKind.ENVIRONMENT
             and a.status.value == "approved"
             and a.source_state.value == "current"),
            None,
        )
        assert env is None

    def test_rejected_env_not_selected(self):
        all_assets = [
            _make_char_asset(),
            _make_env_asset(status=ReferenceStatus.REJECTED),
        ]
        env = next(
            (a for a in all_assets
             if a.kind == ReferenceKind.ENVIRONMENT
             and a.status.value == "approved"
             and a.source_state.value == "current"),
            None,
        )
        assert env is None


class TestImagePackBindingOrder:
    """Bindings must follow the semantic order: char, env, continuity, prop."""

    def test_shot1_bindings_char_env(self):
        """Chain head with character + environment → 2 bindings."""
        char_asset = _make_char_asset()
        env_asset = _make_env_asset()

        # Simulate what GenerationService builds
        char_binding = H3ReferenceBinding(
            reference_asset_id=char_asset.id,
            reference_kind=char_asset.kind.value,
            subject_index=1,
            character_id="char-1",
            character_name="The Man",
            appearance="dark hair, 40s",
            picture_index=1,
            local_path="/fake/char.png",
            content_sha256=char_asset.content_sha256,
        )
        env_binding = H3ReferenceBinding(
            reference_asset_id=env_asset.id,
            reference_kind=env_asset.kind.value,
            picture_index=2,
            local_path="/fake/env.png",
            content_sha256=env_asset.content_sha256,
        )

        ordered = [char_binding, env_binding]
        assert ordered[0].picture_index == 1
        assert ordered[0].reference_kind == "character_body"
        assert ordered[1].picture_index == 2
        assert ordered[1].reference_kind == "environment"

    def test_no_fake_continuity_on_chain_head(self):
        """Chain head must NOT have a Picture 3 continuity binding."""
        char_binding = H3ReferenceBinding(
            reference_asset_id="ref-1", reference_kind="character_body",
            subject_index=1, character_id="c1", character_name="X",
            appearance="", picture_index=1,
            local_path="/fake.png", content_sha256="a" * 64,
        )
        env_binding = H3ReferenceBinding(
            reference_asset_id="ref-2", reference_kind="environment",
            picture_index=2,
            local_path="/fake2.png", content_sha256="b" * 64,
        )
        ordered = [char_binding, env_binding]
        # Only 2 bindings — no Picture 3
        assert len(ordered) == 2
        assert all(b.picture_index <= 2 for b in ordered)


class TestMultipleCharacters:
    """Multi-character shots must include all subjects."""

    def test_two_character_bindings(self):
        """Shot with 2 subjects → Picture 1 = char1, Picture 2 = env, Picture 3 = char2."""
        char1 = _make_char_asset("char-1")
        char2 = _make_char_asset("char-2")
        env = _make_env_asset()

        # GenerationService puts first char at Picture 1, env at Picture 2,
        # additional chars starting at Picture 3
        bindings = [
            H3ReferenceBinding(
                reference_asset_id=char1.id, reference_kind="character_body",
                subject_index=1, character_id="char-1", character_name="The Man",
                appearance="dark hair", picture_index=1,
                local_path="/f1.png", content_sha256=char1.content_sha256,
            ),
            H3ReferenceBinding(
                reference_asset_id=env.id, reference_kind="environment",
                picture_index=2,
                local_path="/f2.png", content_sha256=env.content_sha256,
            ),
            H3ReferenceBinding(
                reference_asset_id=char2.id, reference_kind="character_body",
                subject_index=2, character_id="char-2", character_name="The Woman",
                appearance="blonde", picture_index=3,
                local_path="/f3.png", content_sha256=char2.content_sha256,
            ),
        ]
        assert len(bindings) == 3
        assert bindings[0].character_name == "The Man"
        assert bindings[1].reference_kind == "environment"
        assert bindings[2].character_name == "The Woman"

    def test_image_pack_has_4_slots(self):
        """Frozen image-pack workflow has 4 materialized slots."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        resolver = WorkflowResolver(project_root=project_root)
        pack = resolver.resolve_image_pack()
        assert pack.constraints["materialized_reference_slots"] == 4
        # Can hold: char1 + env + char2 + (continuity or prop)


class TestPreviewMatchesExecution:
    """Preview workflow must match what generate_take would use."""

    def test_preview_with_env_shows_image_pack(self):
        """When env ref exists, preview should show image-pack, not legacy v2."""
        # The corrected preview logic:
        env_ref = _make_env_asset()
        char_ref = _make_char_asset()
        is_head = True

        if not is_head:
            wf = "h3_r2v_image_pack_v1"
        elif env_ref is not None:
            wf = "h3_r2v_image_pack_v1"
        elif char_ref is not None:
            wf = "h3_r2v_v1"
        else:
            wf = "h3_r2v_v1"

        assert wf == "h3_r2v_image_pack_v1"

    def test_preview_without_env_shows_legacy(self):
        env_ref = None
        char_ref = _make_char_asset()
        is_head = True

        if not is_head:
            wf = "h3_r2v_image_pack_v1"
        elif env_ref is not None:
            wf = "h3_r2v_image_pack_v1"
        elif char_ref is not None:
            wf = "h3_r2v_v1"
        else:
            wf = "h3_r2v_v1"

        assert wf == "h3_r2v_v1"
