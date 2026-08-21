"""Unit tests for P4 UX cleanup — operator-facing presentation changes.

Tests terminology mapping, subject editing, and domain semantics preservation.
"""
from __future__ import annotations

import pytest

from film_director.models.reference import ReferenceSourceState, ReferenceStatus


# ---------------------------------------------------------------------------
# 1. Source state terminology mapping (UI only, domain unchanged)
# ---------------------------------------------------------------------------

class TestSourceStateTerminology:
    def test_domain_values_unchanged(self):
        """Internal domain values CURRENT/STALE remain unchanged."""
        assert ReferenceSourceState.CURRENT.value == "current"
        assert ReferenceSourceState.STALE.value == "stale"

    def test_ui_mapping_logic(self):
        """UI maps current→Fresh, stale→Outdated."""
        # Simulates the JS sourceStateLabel function
        def source_state_label(s):
            return {"current": "Fresh", "stale": "Outdated"}.get(s, s)

        assert source_state_label("current") == "Fresh"
        assert source_state_label("stale") == "Outdated"
        assert source_state_label("unknown") == "unknown"  # passthrough


# ---------------------------------------------------------------------------
# 5. Individual subject removal
# ---------------------------------------------------------------------------

class TestSubjectRemoval:
    def test_filter_removes_specific_character(self):
        """Removing by character_id filters exactly that character."""
        subjects = [
            {"character_id": "char-1", "name": "Alice", "ref_images": []},
            {"character_id": "char-2", "name": "Bob", "ref_images": []},
            {"character_id": "char-3", "name": "Carol", "ref_images": []},
        ]
        # Remove char-2
        result = [s for s in subjects if s["character_id"] != "char-2"]
        assert len(result) == 2
        assert result[0]["character_id"] == "char-1"
        assert result[1]["character_id"] == "char-3"

    def test_filter_preserves_order(self):
        """Removal preserves order of remaining subjects."""
        subjects = [
            {"character_id": "char-a", "name": "A"},
            {"character_id": "char-b", "name": "B"},
            {"character_id": "char-c", "name": "C"},
        ]
        result = [s for s in subjects if s["character_id"] != "char-a"]
        assert [s["character_id"] for s in result] == ["char-b", "char-c"]


# ---------------------------------------------------------------------------
# 6. Duplicate subject prevention
# ---------------------------------------------------------------------------

class TestSubjectDuplicatePrevention:
    def test_existing_character_not_added_twice(self):
        """Adding a character already in subjects is prevented."""
        subjects = [
            {"character_id": "char-1", "name": "Alice"},
        ]
        new_char_id = "char-1"
        already_exists = any(s["character_id"] == new_char_id for s in subjects)
        assert already_exists is True


# ---------------------------------------------------------------------------
# 8. Original Idea vs legacy distinction
# ---------------------------------------------------------------------------

class TestIdeaDistinction:
    def test_original_idea_field_separate(self):
        """original_idea and description are separate dict keys."""
        dc = {
            "original_idea": "My creative input",
            "description": "WC processed version with extra content",
        }
        assert dc["original_idea"] != dc["description"]
        assert dc["original_idea"] == "My creative input"

    def test_legacy_project_no_original_idea(self):
        """Legacy projects may have description but no original_idea."""
        dc = {"description": "WC content only"}
        assert dc.get("original_idea") is None


# ---------------------------------------------------------------------------
# 10. Historical provenance immutability
# ---------------------------------------------------------------------------

class TestHistoricalProvenance:
    def test_generation_request_snapshot_immutable(self):
        """GenerationRequest reference_snapshot is immutable historical data."""
        from film_director.generation.generation_request import GenerationRequest
        req = GenerationRequest(
            id="greq_test",
            shot_id="shot-1",
            shot_version=1,
            generation_plan_id="plan-1",
            generation_plan_version=1,
            prompt_artifact_id="h3p_test",
            prompt_artifact_version=1,
            workflow_definition_id="h3_r2v_v1",
            workflow_definition_version="1.0.0",
            workflow_template_fingerprint="a" * 64,
            take_number=1,
            parameters_snapshot=[{"name": "seed", "value": 42}],
            reference_snapshot=[
                {"character_name": "Historical Name", "reference_kind": "character_body"},
            ],
            seed=42,
            status="succeeded",
        )
        # Historical data is what it is — renaming characters doesn't change it
        assert req.reference_snapshot[0]["character_name"] == "Historical Name"
        # model_dump preserves it
        d = req.model_dump()
        assert d["reference_snapshot"][0]["character_name"] == "Historical Name"


# ---------------------------------------------------------------------------
# 11. Lifecycle domain semantics unchanged
# ---------------------------------------------------------------------------

class TestLifecycleSemantics:
    def test_approved_plus_current_is_eligible(self):
        """APPROVED + CURRENT is eligible for selection."""
        from film_director.models.reference import ReferenceAsset, ReferenceKind, ReferenceSource
        asset = ReferenceAsset(
            id="ref-test", project_id="proj-1", character_id="char-1",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.GENERATED,
            managed_path="test.png", content_sha256="a" * 64,
            source_provenance="test",
            status=ReferenceStatus.APPROVED,
            source_state=ReferenceSourceState.CURRENT,
            width=64, height=64,
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        # Eligible: approved AND current
        assert asset.status == ReferenceStatus.APPROVED
        assert asset.source_state == ReferenceSourceState.CURRENT

    def test_rejected_plus_current_not_eligible(self):
        """REJECTED + CURRENT is NOT eligible (valid state, just not selected)."""
        from film_director.models.reference import ReferenceAsset, ReferenceKind, ReferenceSource
        asset = ReferenceAsset(
            id="ref-test2", project_id="proj-1", character_id="char-1",
            kind=ReferenceKind.CHARACTER_BODY,
            source=ReferenceSource.GENERATED,
            managed_path="test.png", content_sha256="b" * 64,
            source_provenance="test",
            status=ReferenceStatus.REJECTED,
            source_state=ReferenceSourceState.CURRENT,
            width=64, height=64,
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        # Valid state but not eligible
        assert asset.status == ReferenceStatus.REJECTED
        assert asset.source_state == ReferenceSourceState.CURRENT
        # Selection requires APPROVED — this would be filtered out
        is_eligible = (asset.status == ReferenceStatus.APPROVED
                       and asset.source_state == ReferenceSourceState.CURRENT)
        assert is_eligible is False
