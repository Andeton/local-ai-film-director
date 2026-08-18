"""Regression tests for seed overflow safety (SQLite int64 constraint)."""
import sqlite3

import pytest

from film_director.generation.parameter_resolver import ParameterResolver, _SAFE_SEED_MAX
from film_director.models.canonical import GenerationPlan, ReferenceRequirements


_INT64_MAX = (1 << 63) - 1


def _plan(seed_policy="random", seed=None) -> GenerationPlan:
    return GenerationPlan(
        id="plan1", shot_id="s1", shot_version=1, strategy="REFERENCE_TO_VIDEO",
        reference_requirements=ReferenceRequirements(),
        duration_sec=5.0, seed_policy=seed_policy, seed=seed,
        selection_reason="test", status="ready", version=1,
        created_at="t", updated_at="t",
    )


class TestRandomSeedRange:
    def test_random_seed_non_negative(self):
        resolver = ParameterResolver()
        for _ in range(100):
            seed = resolver.resolve_seed(_plan(), take_number=1)
            assert seed >= 0, f"Negative seed: {seed}"

    def test_random_seed_within_sqlite_int64(self):
        resolver = ParameterResolver()
        for _ in range(100):
            seed = resolver.resolve_seed(_plan(), take_number=1)
            assert seed <= _INT64_MAX, f"Seed {seed} exceeds SQLite INT64 max {_INT64_MAX}"

    def test_safe_seed_max_constant(self):
        assert _SAFE_SEED_MAX == _INT64_MAX


class TestExplicitSeedBoundary:
    def test_max_legal_seed_accepted(self):
        """INT64_MAX should be a valid fixed seed."""
        resolver = ParameterResolver()
        seed = resolver.resolve_seed(
            _plan(seed_policy="fixed", seed=_INT64_MAX), take_number=1,
        )
        assert seed == _INT64_MAX

    def test_max_legal_seed_persists_in_sqlite(self, tmp_path):
        """INT64_MAX must round-trip through SQLite INTEGER."""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (seed INTEGER)")
        conn.execute("INSERT INTO t VALUES (?)", (_INT64_MAX,))
        row = conn.execute("SELECT seed FROM t").fetchone()
        conn.close()
        assert row[0] == _INT64_MAX

    def test_overflow_seed_rejected_by_sqlite(self):
        """Values > INT64_MAX cause OverflowError in sqlite3."""
        overflow_seed = _INT64_MAX + 1
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (seed INTEGER)")
        with pytest.raises(OverflowError):
            conn.execute("INSERT INTO t VALUES (?)", (overflow_seed,))
        conn.close()


class TestSeedConsistency:
    def test_resolved_seed_is_single_value(self):
        """Same resolve call must return the same seed for fixed policy."""
        resolver = ParameterResolver()
        plan = _plan(seed_policy="fixed", seed=42)
        s1 = resolver.resolve_seed(plan, take_number=1)
        s2 = resolver.resolve_seed(plan, take_number=1)
        assert s1 == s2 == 42
