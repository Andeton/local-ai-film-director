# M6 Take Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate multiple takes per shot with different seeds, persist a generation queue, and allow deterministic human selection (favorite/approve/reject).

**Architecture:** Extends M3 single-take pipeline with configurable takes_per_shot, deterministic seed derivation, a SQLite-backed persistent queue, and human review actions on Takes. The proven M3/M5.E GenerationService pipeline is reused for individual take execution.

**Tech Stack:** Python 3.14.3, FastAPI, Pydantic v2, SQLite, ComfyUI REST/WS

## Global Constraints

- Architecture FROZEN V1 — ADR-001 through ADR-005 remain unchanged
- Existing immutable GenerationRequests and historical Takes are never modified
- M3 single-shot `POST /shots/{shot_id}/generate` remains backward compatible
- M5 reference selection, lifecycle, and staleness remain unchanged
- No M7 continuity/previous-frame
- No M8 AI review/scoring (ReviewResult model deferred)
- No M9 UI
- No distributed workers — single local process
- ComfyUI concurrency default 1, configurable
- Queue state persists in SQLite alongside production data

## Design Decisions

### A. Lifecycle Separation

Take.status is extended with review states. Favorite is a **separate boolean field** (`is_favorite: bool`) independent of status, avoiding lifecycle ambiguity.

```
EXECUTION PHASE:     pending → generating → succeeded | failed
REVIEW PHASE:        succeeded → approved | rejected
PREFERENCE:          is_favorite = True/False  (orthogonal to status)
```

**Status enum** (extends existing M3 values):
- `pending`, `generating`, `succeeded`, `failed` — unchanged from M3
- `approved` — human accepts this take (terminal; at most one per shot)
- `rejected` — human rejects this take

**Favorite field** (`is_favorite: bool = False`):
- Independent of status — an approved take can also be favorite
- Only succeeded/approved takes can be favorited (not failed/rejected)
- Multiple favorites per shot allowed
- Unfavorite reverts `is_favorite` to False without changing status

**Transition rules:**
- Only `succeeded` takes can be approved/rejected
- `failed` takes cannot be reviewed
- `approved` is terminal (no re-rejection after approval)
- Approval does NOT change other takes' statuses
- `is_favorite` can be set on `succeeded` or `approved` takes

### B. Immutable Provenance

Each take gets its own immutable GenerationRequest with:
- `take_number`: 1-based sequential per shot (1, 2, 3...)
- `seed`: actual seed submitted to ComfyUI (derived deterministically)
- `parameters_snapshot`: exact injected parameters for this take
- `reference_snapshot`: exact references used (shared across takes for same shot)
- `workflow_template_fingerprint`: exact workflow version

GenerationRequest remains INSERT-ONLY. Take.generation_request_id links 1:1.

### C. Seed Derivation

Deterministic, collision-safe derivation:

```python
# Safe seed domain: 0 to 2^63-1 (intersection of SQLite signed int64 + non-negative)
_SAFE_SEED_MAX = (1 << 63) - 1

def derive_take_seed(base_seed: int, take_index: int) -> int:
    """Derive deterministic seed for take N.
    
    take_index is 0-based (take_number - 1).
    Algorithm: SHA-256 of f"{base_seed}:{take_index}" (UTF-8),
    first 8 bytes as big-endian uint, masked to [0, 2^63-1].
    
    Deterministic with negligible collision probability (~1/2^63).
    Raises ValueError if base_seed outside safe domain or take_index < 0.
    The actual submitted seed is always preserved in GenerationRequest.seed.
    """
```

**uint64 correction (M6.A):** The original plan specified unrestricted uint64 output. SQLite INTEGER is signed 64-bit (max 2^63-1). The seed derivation now masks to `& ((1 << 63) - 1)` to guarantee all derived seeds survive SQLite roundtrip. ComfyUI H3 accepts the full uint64 range, but SQLite is the binding constraint.

- `seed_policy=fixed`: all takes use the same base_seed (unusual but valid)
- `seed_policy=random`: each take gets a unique random seed
- `seed_policy=vary_per_take`: deterministic derivation from base_seed + take_index

### D. Queue Persistence

New SQLite table `generation_queue`:

**Seed persistence correction (M6.B):** base_seed and derived seed are persisted at enqueue time so that restart recovery and worker execution never recompute seeds from transient state.

```sql
CREATE TABLE IF NOT EXISTS generation_queue (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL,
    take_number INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    base_seed INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    -- pending → claimed → succeeded | failed | cancelled
    generation_request_id TEXT,
    take_id TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    UNIQUE(shot_id, take_number)
);
```

State machine:
```
pending → claimed → succeeded
                  → failed
       → cancelled (user cancels before claim)
```

### E. Restart Recovery

On startup, the queue worker scans for `status=claimed` jobs and resolves each deterministically using the following state matrix:

| Queue claimed? | gen_request_id? | Request status | Take exists? | Recovery action |
|---|---|---|---|---|
| claimed | NULL | — | — | Reset to `pending` (never submitted) |
| claimed | present | succeeded | yes | Mark queue `succeeded` (crash after finalization) |
| claimed | present | succeeded | no | Create Take from ComfyUI result, mark queue `succeeded` |
| claimed | present | failed | — | Mark queue `failed` |
| claimed | present | pending | — | Should not happen; mark queue `failed` + log warning |
| claimed | present | queued/running | — | Check ComfyUI history (see below) |

**ComfyUI history check for queued/running requests:**
1. Read `comfyui_prompt_id` from the request
2. Query ComfyUI `/history/{prompt_id}`
3. If completed successfully: download result, create Take, finalize request+queue as `succeeded`
4. If failed or not found: mark request `failed`, mark queue `failed`
5. If still running: mark request `failed` (orphaned), mark queue `failed`

**Idempotency guarantee:** Before creating a Take during recovery, always check `TakeRepository` for an existing Take with the same `generation_request_id`. If found, skip creation. The UNIQUE constraint on `generation_request_id` in the takes table prevents duplicates even under race conditions.

### F. Atomicity

| Operation | Transaction Owner | Scope |
|---|---|---|
| Enqueue batch | `QueueService.enqueue_shot` | Explicit `with db.connection() as conn:` — insert N queue jobs atomically |
| Claim one job | `QueueWorker._claim_next` | Explicit `with db.connection() as conn:` — see below |
| Pre-request resolution | No transaction | Read-only resolution (same as M3) |
| Create GenerationRequest | `QueueWorker._execute_job` | Explicit `conn`: INSERT request + UPDATE queue.generation_request_id |
| Finalize Take + Request | `QueueWorker._finalize` | Explicit `conn`: INSERT Take + UPDATE request status + UPDATE queue status |
| Post-submit failure | `QueueWorker._handle_failure` | Explicit `conn`: UPDATE request failed + UPDATE queue failed |

**Claim atomicity (critical):** The claim operation must be wrapped in an explicit `Database.connection()` context manager (which provides BEGIN/COMMIT/ROLLBACK). SQLite's write-ahead log (WAL) mode serializes write transactions, so only one writer can execute the UPDATE at a time. The claim query:

```sql
UPDATE generation_queue
SET status = 'claimed', claimed_at = datetime('now'), updated_at = datetime('now')
WHERE id = (
    SELECT id FROM generation_queue
    WHERE status = 'pending'
    ORDER BY priority DESC, created_at ASC
    LIMIT 1
)
RETURNING id, shot_id, take_number, project_id
```

The subquery + UPDATE pattern ensures atomicity: if two workers race, SQLite's write lock serializes them. The second writer finds no `pending` rows (or a different one) because the first already committed. The `RETURNING` clause retrieves the claimed job in the same statement.

The long ComfyUI execution happens OUTSIDE any SQLite transaction (same pattern as M3).

### G. Concurrency

- Default: 1 concurrent ComfyUI job
- Configured via `Settings.generation_concurrency` (int, default 1)
- Enforced by the QueueWorker: poll loop claims at most `concurrency` jobs
- No SQLite row locking during ComfyUI work — claim uses atomic UPDATE with LIMIT
- For concurrency > 1: multiple claims in sequence, each runs in its own thread/task

### H. Backward Compatibility

- `POST /shots/{shot_id}/generate` remains synchronous, single-take (M3 compat)
- New `POST /shots/{shot_id}/enqueue-takes` adds multi-take queue path
- GenerationService.generate_shot() is reused by the queue worker for individual take execution
- Existing M3/M5 tests are NOT modified (they use generate_shot directly)
- take_number uniqueness is enforced at the queue level, not by modifying the takes table constraint

### I. API Contract

**New M6 endpoints:**

```
POST /shots/{shot_id}/enqueue-takes
  Body: { takes_count: int = 3 }  — validated: 1 ≤ takes_count ≤ 10
  Response: { queue_job_ids: list[str], shot_id: str, takes_count: int }
  Enqueues N take generation jobs. queue_job_ids are generation_queue.id values.
  Error: 404 shot not found, 422 invalid takes_count, 409 already queued

POST /scenes/{scene_id}/enqueue-batch
  Body: { takes_per_shot: int = 3 }  — validated: 1 ≤ takes_per_shot ≤ 10
  Response: { total_jobs: int, shots_enqueued: int }
  Enqueues takes for every eligible R2V shot in the scene.
  Error: 404 scene not found

GET /queue/status
  Response: { pending: int, claimed: int, succeeded: int, failed: int, cancelled: int, total: int }

GET /queue/jobs
  Query: status (optional filter), shot_id (optional), limit (default 50, max 200)
  Response: list[QueueJobResponse]

POST /queue/jobs/{job_id}/cancel
  Response: QueueJobResponse with status="cancelled"
  Only pending jobs. Error: 404 not found, 409 not cancellable

GET /shots/{shot_id}/takes
  Response: list[TakeResponse]  — includes is_favorite field
  Ordered by take_number ASC (derived from generation_request.take_number).

POST /takes/{take_id}/approve
  Response: TakeResponse
  Only succeeded takes. At most one approved per shot.
  Error: 404 not found, 409 wrong status or another already approved

POST /takes/{take_id}/reject
  Response: TakeResponse
  Only succeeded takes. Error: 404, 409

POST /takes/{take_id}/favorite
  Response: TakeResponse with is_favorite=True
  Only succeeded or approved takes. Error: 404, 409

POST /takes/{take_id}/unfavorite
  Response: TakeResponse with is_favorite=False
  Only is_favorite=True takes. Error: 404, 409

GET /shots/{shot_id}/approved-take
  Response: TakeResponse | 404
  Returns the single approved take for the shot.
```

**Existing endpoints preserved:**
- `POST /shots/{shot_id}/generate` — synchronous single take (M3 compat)

### J. Selection Invariants

- A shot may have at most **one** approved take (status=approved)
- A shot may have **multiple** favorite takes (is_favorite=True)
- `approved` status and `is_favorite` are independent (an approved take can also be is_favorite=True)
- Approving a take does NOT auto-reject other succeeded takes
- `approved` is terminal — cannot be unapproved
- `is_favorite` can be toggled independently on succeeded or approved takes
- Deterministic approved-take resolution: `GET /shots/{shot_id}/approved-take` returns the single approved take or 404

### K. Scope Boundaries — Explicit Exclusions

- No previous-frame continuity (M7)
- No downstream invalidation chains (M7)
- No AI visual scoring (M8)
- No prompt regeneration suggestions (M8)
- No UI (M9)
- No distributed workers
- No automatic retry (attempt_count tracks but does not auto-retry)
- No WebSocket push notifications for queue progress

### L. Live-Data Strategy

M6 live acceptance will:
1. Copy `data/production.db` from the M5 worktree (read-only copy, M5 original untouched)
2. Run `db.init_schema()` to add any new M6 tables
3. Reuse the existing project/character/shot/reference data
4. Generate new takes in the M6 worktree's own `storage/` directory
5. M5 worktree storage is never read or modified

## Take Model Extension

```python
class Take(BaseModel):
    id: str
    shot_id: str
    generation_request_id: str
    seed: int
    video_path: str
    audio_path: str | None = None
    last_frame_path: str | None = None
    status: Literal["pending", "generating", "succeeded", "failed", "approved", "rejected"] = "pending"
    is_favorite: bool = False       # M6: orthogonal preference flag
    created_at: str = ""
```

Status transition rules:
- `pending → generating`: queue worker claims
- `generating → succeeded`: ComfyUI completes + media verified
- `generating → failed`: ComfyUI error or media failure
- `succeeded → approved`: human approves (at most one per shot; terminal)
- `succeeded → rejected`: human rejects

Favorite rules (independent of status):
- `is_favorite = True`: only on succeeded or approved takes
- `is_favorite = False`: unfavorite (no status change)
- Multiple favorites per shot allowed
- An approved take can also be favorite

## File Structure

```
src/film_director/
  generation/
    generation_request.py    # MODIFY: extend Take status enum
    generation_service.py    # MODIFY: accept take_number parameter
    parameter_resolver.py    # MODIFY: seed derivation for multi-take
    queue_models.py          # CREATE: QueueJob model
    queue_service.py         # CREATE: QueueService (enqueue, cancel)
    queue_worker.py          # CREATE: QueueWorker (claim, execute, recover)
  services/
    take_service.py          # CREATE: TakeService (approve/reject/favorite)
  persistence/
    database.py              # MODIFY: generation_queue table
    repositories.py          # MODIFY: QueueJobRepository, extend TakeRepository
  api/routes.py              # MODIFY: M6 queue + take endpoints
  main.py                    # MODIFY: wire M6 services
  config.py                  # MODIFY: generation_concurrency setting
tests/
  unit/
    test_seed_derivation.py  # CREATE
    test_queue_state.py      # CREATE
    test_take_lifecycle.py   # CREATE
  integration/
    test_queue_service.py    # CREATE
    test_queue_worker.py     # CREATE
    test_take_api.py         # CREATE
    test_m6_live.py          # CREATE: @pytest.mark.live
```

## Task Dependency Table

| Task | Requires | Produces |
|---|---|---|
| M6.A | None | TakeStatus enum, seed derivation, QueueJob model, queue table |
| M6.B | M6.A | QueueService (enqueue, cancel), QueueJobRepository |
| M6.C | M6.A, M6.B | QueueWorker (claim, execute, finalize, recover) |
| M6.D | M6.A | TakeService (approve/reject/favorite/unfavorite) |
| M6.E | M6.B, M6.C, M6.D | M6 API endpoints |
| M6.F | M6.A-E | Live acceptance: 3 takes + queue proof + human approval |

**Forward dependencies: ZERO.**

---

### Task M6.A: Take Lifecycle + Queue Data Model

**Goal:** Extend Take status, implement deterministic seed derivation, create QueueJob model and generation_queue table.

**Files:** Modify `generation_request.py`, `parameter_resolver.py`, `persistence/database.py`. Create `generation/queue_models.py`.

**Take status extension:**
- Add `approved`, `rejected`, `favorite` to Take.status Literal
- Preserve existing M3 values unchanged

**Seed derivation:**
- Add `derive_take_seed(base_seed, take_index)` to `parameter_resolver.py`
- SHA-256 based, deterministic, collision-safe
- Modify `resolve_seed` to accept take_index and apply policy

**QueueJob model:**
- Pydantic model with queue state machine fields
- Status: pending, claimed, succeeded, failed, cancelled

**Database:**
- Add `generation_queue` table with UNIQUE(shot_id, take_number)

**Tests:** Seed derivation determinism/collision, status enum round-trip, queue table creation, QueueJob validation.

- [ ] **Steps: TDD, models, schema, seed derivation, tests, commit**

```bash
git commit -m "M6.A: take lifecycle, seed derivation, and queue data model"
```

---

### Task M6.B: Queue Service (Enqueue + Cancel)

**Goal:** Enqueue take generation jobs for shots/scenes, cancel pending jobs.

**Files:** Create `generation/queue_service.py`, extend `persistence/repositories.py` with QueueJobRepository.

**QueueService:**
- `enqueue_shot(shot_id, takes_count, project_id)` → list[QueueJob]
  - Validates shot exists and has a current R2V plan
  - Creates N queue jobs with take_numbers 1..N
  - Idempotent: skips already-queued take_numbers
  - Returns created jobs
- `enqueue_scene(scene_id, takes_per_shot)` → int (total jobs)
  - Finds all eligible shots in scene
  - Calls enqueue_shot for each
- `cancel_job(job_id)` → QueueJob
  - Only pending jobs can be cancelled
  - Raises error for claimed/completed jobs

**QueueJobRepository:**
- `create(job, conn)` — INSERT
- `get(job_id)` — SELECT by ID
- `list_by_status(status, limit)` — filtered listing
- `list_by_shot(shot_id)` — all jobs for a shot
- `claim_next(conn)` → QueueJob | None — atomic UPDATE ... LIMIT 1
- `update_status(job_id, status, ...)` — state transitions
- `count_by_status()` → dict — aggregate counts

**Tests:** Enqueue single shot, enqueue scene batch, dedup, cancel pending, cancel non-pending fails, queue counts.

- [ ] **Steps: TDD, repository, service, tests, commit**

```bash
git commit -m "M6.B: queue service with enqueue and cancel"
```

---

### Task M6.C: Queue Worker (Execute + Recover)

**Goal:** Poll queue, claim jobs, execute through GenerationService, finalize, and recover on restart.

**Files:** Create `generation/queue_worker.py`.

**QueueWorker:**
- `__init__(queue_repo, generation_service, db, concurrency=1)`
- `run_once()` → int (jobs completed)
  - Claims up to `concurrency` pending jobs
  - For each: resolve take_number, derive seed, call generate_shot variant
  - Finalizes queue job on success/failure
- `recover()` → int (recovered jobs)
  - Scans for `claimed` jobs on startup
  - Checks ComfyUI history / request status
  - Resolves to succeeded/failed/pending

**GenerationService evolution:**
- Add `generate_take(shot_id, take_number, seed_override)` method
  - Reuses the M3/M5.E pipeline (reference selection, prompt, workflow, upload, injection)
  - Uses the provided take_number and seed
  - Returns Take on success
- Existing `generate_shot()` delegates to `generate_take(shot_id, take_number=1)`

**Atomicity:**
- Pre-request: read-only (no transaction)
- Request creation + queue update: single conn
- ComfyUI execution: outside transaction
- Finalization (Take + request + queue): single conn
- Failure: request failed + queue failed in single conn

**Tests:** Worker claims and executes (mocked ComfyUI), multiple takes with different seeds, failure handling, recovery of claimed jobs, idempotent recovery.

- [ ] **Steps: TDD, worker, GenerationService evolution, recovery, tests, commit**

```bash
git commit -m "M6.C: queue worker with execution and restart recovery"
```

---

### Task M6.D: Take Review Service

**Goal:** Approve, reject, favorite, and unfavorite succeeded takes.

**Files:** Create `services/take_service.py`. Extend `persistence/repositories.py` TakeRepository.

**TakeService:**
- `approve(take_id)` → Take
  - Only succeeded takes
  - At most one approved per shot (error if another already approved)
  - Sets status=approved
- `reject(take_id)` → Take
  - Only succeeded takes
  - Sets status=rejected
- `favorite(take_id)` → Take
  - Only succeeded or approved takes
  - Sets is_favorite=True (multiple allowed per shot)
- `unfavorite(take_id)` → Take
  - Only is_favorite=True takes
  - Sets is_favorite=False (no status change)

**TakeRepository extensions:**
- `update_status(take_id, status)` — status transition
- `update_favorite(take_id, is_favorite)` — toggle favorite flag
- `get_approved_for_shot(shot_id)` → Take | None
- `count_by_status(shot_id)` → dict

**Error types:**
- `TakeReviewError(FilmDirectorError)` — illegal review transition
- `TakeNotFoundError(FilmDirectorError)` — take not found

**Tests:** Approve/reject transitions, favorite/unfavorite on succeeded+approved, only succeeded can be approved, at-most-one-approved, multiple favorites, terminal approved state, favorite+approved coexistence.

- [ ] **Steps: TDD, service, repository, tests, commit**

```bash
git commit -m "M6.D: take review service with approve/reject/favorite"
```

---

### Task M6.E: M6 API Endpoints

**Goal:** REST API for queue management and take review.

**Files:** Modify `api/routes.py`, `main.py`.

**Routes:**
- `POST /shots/{shot_id}/enqueue-takes` — enqueue N takes
- `POST /scenes/{scene_id}/enqueue-batch` — enqueue all scene shots
- `GET /queue/status` — aggregate counts
- `GET /queue/jobs` — filtered listing
- `POST /queue/jobs/{job_id}/cancel` — cancel pending
- `GET /shots/{shot_id}/takes` — list takes
- `POST /takes/{take_id}/approve`
- `POST /takes/{take_id}/reject`
- `POST /takes/{take_id}/favorite`
- `POST /takes/{take_id}/unfavorite`
- `GET /shots/{shot_id}/approved-take`

**Error mapping:**
- TakeReviewError → 409
- TakeNotFoundError → 404

**Tests:** All endpoints with mocked services, error mapping, backward compat with M3 generate.

- [ ] **Steps: TDD, routes, wiring, error mapping, tests, commit**

```bash
git commit -m "M6.E: take management and queue API endpoints"
```

---

### Task M6.F: Live Acceptance

**Goal:** Real multi-take generation + queue proof + human approval.

**Project:** `proj_b8b1b8ab40b5`
**Shot:** A single-subject REFERENCE_TO_VIDEO shot with approved reference

**Phase M6.F1 — Three Takes:**
1. Copy M5 production DB to M6 worktree
2. Enqueue 3 takes for one shot
3. Run queue worker
4. Verify 3 distinct takes with different seeds
5. Verify 3 immutable GenerationRequests
6. Report video paths for human inspection

**Phase M6.F2 — Queue Proof (20 shots):**
1. Enqueue batch for multiple shots (up to 20)
2. Run queue worker to completion
3. Verify queue state recovery (simulate restart mid-batch)
4. Verify no duplicate takes
5. Report queue statistics

**Phase M6.F3 — Human Approval:**
1. User selects and approves one take
2. Verify at-most-one-approved invariant
3. Verify rejected/favorite transitions
4. Report approved take path

- [ ] **Steps: H1 three takes, H2 queue proof, H3 human approval, commit**

```bash
git commit -m "M6.F: live multi-take generation and queue acceptance"
```

---

## M6 Exit Criteria

1. Configurable takes_per_shot, default 3
2. Three takes generated for one real shot with different seeds
3. Each take has its own immutable GenerationRequest with exact seed/parameters
4. Deterministic seed derivation from base seed + take index
5. Persisted SQLite generation queue with pending/claimed/succeeded/failed/cancelled states
6. Concurrency control defaults to 1, configurable
7. Queue manages 20+ shots without losing state
8. Restart recovery resolves orphaned claimed jobs without duplicates
9. Human favorite/approve/reject operations on succeeded takes
10. At most one approved take per shot
11. Multiple favorites per shot allowed
12. Batch generation for all eligible shots in a scene
13. Queue status and listing API
14. Cancel pending queue jobs
15. Existing M3 synchronous single-take generation backward compatible
16. Existing M5 reference selection/lifecycle unchanged
17. Historical GenerationRequests and Takes remain immutable
18. M0-M5 deterministic regressions green (1148+)
19. No M7 continuity leakage
20. No M8 AI review leakage
21. No M9 UI leakage

## Task Dependency Audit

| Task | Forward refs? | M7+? | Verdict |
|---|---|---|---|
| M6.A | NO | NO | PASS |
| M6.B | NO | NO | PASS |
| M6.C | NO | NO | PASS |
| M6.D | NO | NO | PASS |
| M6.E | NO | NO | PASS |
| M6.F | NO | NO | PASS |

**Forward dependencies: ZERO.**

## Backlog (NOT M6 scope)

- Previous-frame continuity references — M7
- AI visual quality reviewer — M8
- Smart retry suggestions — M8
- Production UI — M9
- Distributed GPU workers — Post-M10
