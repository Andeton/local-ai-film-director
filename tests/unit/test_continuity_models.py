"""Unit tests for ContinuityState model, fingerprinting, and validation."""
import pytest

from film_director.continuity.continuity_models import (
    ContinuityState,
    compute_continuity_fingerprint,
)
from film_director.errors import ContinuityError


class TestContinuityStateModel:
    """Valid construction and field validation."""

    def test_minimal_unresolved(self):
        cs = ContinuityState(
            id="cs_1", shot_id="shot_1", scene_id="scene_1",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        assert cs.state == "unresolved"
        assert cs.predecessor_shot_id is None
        assert cs.upstream_take_id is None
        assert cs.continuity_revision == 0

    def test_full_current_state(self):
        cs = ContinuityState(
            id="cs_2", shot_id="shot_2", scene_id="scene_1",
            predecessor_shot_id="shot_1",
            upstream_take_id="take_1", upstream_take_number=1,
            upstream_last_frame_path="storage/takes/proj/shot/take_1/last_frame.png",
            upstream_last_frame_sha256="a" * 64,
            state="current", continuity_revision=3,
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        )
        assert cs.state == "current"
        assert cs.upstream_last_frame_sha256 == "a" * 64
        assert cs.continuity_revision == 3

    def test_outdated_with_invalidation(self):
        cs = ContinuityState(
            id="cs_3", shot_id="shot_3", scene_id="scene_1",
            predecessor_shot_id="shot_2",
            state="outdated",
            invalidation_reason="upstream_take_changed",
            invalidation_source_shot_id="shot_2",
            invalidated_at="2026-01-02T00:00:00Z",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-02T00:00:00Z",
        )
        assert cs.state == "outdated"
        assert cs.invalidation_reason == "upstream_take_changed"

    def test_all_three_states(self):
        for state in ("unresolved", "current", "outdated"):
            cs = ContinuityState(
                id=f"cs_{state}", shot_id=f"shot_{state}", scene_id="s",
                state=state, created_at="t", updated_at="t",
            )
            assert cs.state == state

    def test_invalid_state_rejected(self):
        with pytest.raises(Exception):
            ContinuityState(
                id="cs", shot_id="s", scene_id="sc",
                state="invalid", created_at="t", updated_at="t",
            )

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError):
            ContinuityState(
                id="", shot_id="s", scene_id="sc",
                created_at="t", updated_at="t",
            )

    def test_empty_shot_id_rejected(self):
        with pytest.raises(ValueError):
            ContinuityState(
                id="cs", shot_id="  ", scene_id="sc",
                created_at="t", updated_at="t",
            )

    def test_empty_scene_id_rejected(self):
        with pytest.raises(ValueError):
            ContinuityState(
                id="cs", shot_id="s", scene_id="",
                created_at="t", updated_at="t",
            )

    def test_invalid_sha256_rejected(self):
        with pytest.raises(ValueError):
            ContinuityState(
                id="cs", shot_id="s", scene_id="sc",
                upstream_last_frame_sha256="not_a_sha",
                created_at="t", updated_at="t",
            )

    def test_uppercase_sha256_rejected(self):
        with pytest.raises(ValueError):
            ContinuityState(
                id="cs", shot_id="s", scene_id="sc",
                upstream_last_frame_sha256="A" * 64,
                created_at="t", updated_at="t",
            )

    def test_none_sha256_accepted(self):
        cs = ContinuityState(
            id="cs", shot_id="s", scene_id="sc",
            upstream_last_frame_sha256=None,
            created_at="t", updated_at="t",
        )
        assert cs.upstream_last_frame_sha256 is None

    def test_negative_revision_rejected(self):
        with pytest.raises(ValueError):
            ContinuityState(
                id="cs", shot_id="s", scene_id="sc",
                continuity_revision=-1,
                created_at="t", updated_at="t",
            )

    def test_zero_revision_accepted(self):
        cs = ContinuityState(
            id="cs", shot_id="s", scene_id="sc",
            continuity_revision=0, created_at="t", updated_at="t",
        )
        assert cs.continuity_revision == 0


class TestContinuityFingerprint:
    """Determinism, sensitivity, and canonical encoding."""

    _BASE = dict(
        upstream_shot_id="shot_1",
        upstream_take_id="take_1",
        upstream_take_number=2,
        upstream_last_frame_sha256="b" * 64,
        continuity_revision=3,
    )

    def test_deterministic(self):
        fp1 = compute_continuity_fingerprint(**self._BASE)
        fp2 = compute_continuity_fingerprint(**self._BASE)
        assert fp1 == fp2

    def test_lowercase_hex_64_chars(self):
        fp = compute_continuity_fingerprint(**self._BASE)
        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in "0123456789abcdef" for c in fp)

    def test_change_upstream_shot_id(self):
        fp1 = compute_continuity_fingerprint(**self._BASE)
        fp2 = compute_continuity_fingerprint(**{**self._BASE, "upstream_shot_id": "shot_2"})
        assert fp1 != fp2

    def test_change_upstream_take_id(self):
        fp1 = compute_continuity_fingerprint(**self._BASE)
        fp2 = compute_continuity_fingerprint(**{**self._BASE, "upstream_take_id": "take_2"})
        assert fp1 != fp2

    def test_change_take_number(self):
        fp1 = compute_continuity_fingerprint(**self._BASE)
        fp2 = compute_continuity_fingerprint(**{**self._BASE, "upstream_take_number": 5})
        assert fp1 != fp2

    def test_change_sha256(self):
        fp1 = compute_continuity_fingerprint(**self._BASE)
        fp2 = compute_continuity_fingerprint(**{**self._BASE, "upstream_last_frame_sha256": "c" * 64})
        assert fp1 != fp2

    def test_change_revision(self):
        fp1 = compute_continuity_fingerprint(**self._BASE)
        fp2 = compute_continuity_fingerprint(**{**self._BASE, "continuity_revision": 4})
        assert fp1 != fp2

    def test_unicode_input(self):
        fp = compute_continuity_fingerprint(
            upstream_shot_id="shot_陆砚",
            upstream_take_id="take_ü",
            upstream_take_number=1,
            upstream_last_frame_sha256="d" * 64,
            continuity_revision=0,
        )
        assert len(fp) == 64
        # Deterministic with unicode
        fp2 = compute_continuity_fingerprint(
            upstream_shot_id="shot_陆砚",
            upstream_take_id="take_ü",
            upstream_take_number=1,
            upstream_last_frame_sha256="d" * 64,
            continuity_revision=0,
        )
        assert fp == fp2


class TestContinuityErrorExists:
    def test_continuity_error_is_film_director_error(self):
        from film_director.errors import FilmDirectorError
        err = ContinuityError("test error", detail="detail")
        assert isinstance(err, FilmDirectorError)
        assert err.message == "test error"
        assert err.detail == "detail"
