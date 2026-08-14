## Task 6: Stale Propagation (M2.F) — Report

**Status:** COMPLETE

**Files created:**
- `src/film_director/enrichment/stale_propagator.py` — StalePropagator class with 5 public cascade methods
- `tests/unit/test_stale_propagation.py` — 24 tests across 8 categories

**Test summary:** 362 passed, 3 deselected (338 prior + 24 new). All green.

**Implementation notes:**
- Transaction contract: external conn used as-is; absent conn opens `db.connection()` with auto-commit/rollback
- Counting: counts current (non-outdated) items before marking, so already-outdated rows are excluded
- Scene/project cascade walks ALL beats (including outdated) to find orphaned current shots underneath
- Character matching uses `ShotSubject.character_id` (ID-based, not name)
- Zero DELETE SQL, zero LLM calls, zero imports of BeatEnricher/CoveragePlanner/StrategySelector

**Concerns:** None. `get_current_plan_by_shot` joins on shot_version which works correctly for our use case since plans and shots share version=1 in the test fixtures.
