# M7 Continuity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish automatic continuity chains between sequential shots within a scene, so each downstream shot consumes the approved predecessor's last frame for visual continuity. Enable deterministic invalidation when upstream approved Takes change, and provide an atomic approved-Take replacement operation.

**Architecture:** Extends M6 Take management with a ContinuityState tracker, deterministic predecessor resolution via canonical ordering, an atomic replace-approved operation with cascading invalidation, and continuity-aware generation eligibility. Uses MiniMaxH3ImageToVideo (fl2va) for continuity shots via a new versioned workflow, with R2V (ref2va) workflows as a secondary preflight candidate. R2V v1/v2 fingerprints remain unchanged.

**Tech Stack:** Python 3.14.3, FastAPI, Pydantic v2, SQLite, ComfyUI REST/WS, FFmpeg

---

## Global Constraints

- Architecture FROZEN V1 — ADR-001 through ADR-005 remain unchanged
- Existing immutable GenerationRequests and historical Takes are never modified or deleted
- r2v_v1 (fingerprint `3893eb...06b4`) and r2v_v2 (fingerprint `b493...f3b9`) must remain unchanged
- M3 synchronous `POST /shots/{shot_id}/generate` backward compatible
- M5 reference selection, lifecycle, and staleness unchanged
- M6 queue, take review, persistent idempotency unchanged
- Approved Take status remains terminal (an approved Take is NEVER silently unapproved — it is explicitly superseded)
- No M8 AI review/scoring
- No M9 UI
- No distributed workers
- No automatic deletion of Takes or media
- No modification of immutable historical GenerationRequests

---

## Architecture Decisions

### A. Continuity Chain Boundaries

**Decision:** Automatic continuity chaining applies **within a scene only**.

Cross-scene boundaries (even within the same sequence) do NOT automatically chain. Cross-sequence and cross-project never chain.

**Justification:**
- Scenes are the natural narrative unit with spatial/temporal/character continuity
- Different scenes typically have different locations, lighting, and character subsets
- A scene's shots share a common beat decomposition and ordered coverage
- Blindly feeding the last frame of scene 1's final shot into scene 2's first shot would produce visual artifacts when location/cast change
- Cross-scene continuity (if desired) can be modeled as explicit links in a future milestone

**Chain definition:** Within a scene, the chain includes all current (non-outdated) shots ordered by canonical ordering (see B). The first shot in the chain has no predecessor and generates without continuity input. Every subsequent shot's predecessor is the immediately prior shot in canonical order.

### B. Canonical Previous Shot

**Decision:** Deterministic predecessor resolution via scene-scoped canonical ordering.

**Canonical shot order within a scene:**
```
shots ordered by: (beats.order_index ASC, beats.id ASC,
                   shots.order_index ASC, shots.id ASC)
WHERE beats.scene_id = :scene_id
  AND beats.status != 'outdated'
  AND shots.status != 'outdated'
```

**Predecessor resolution algorithm:**
1. Build the ordered list of current shots in the scene.
2. Find the target shot's position in the list.
3. If the target is the first shot: predecessor is NULL (chain head).
4. If the target has a prior shot: that shot is the predecessor.

**Edge cases:**
- **First shot in scene:** No predecessor. Generates without continuity (existing R2V path or standalone FLF).
- **Missing/outdated beats or shots:** Excluded from the ordered list. Successor auto-adjusts to the next valid predecessor.
- **Predecessor without an approved Take:** Downstream cannot generate. Pre-request validation fails with `ContinuityError("Predecessor shot {id} has no approved Take")`.
- **Predecessor with outdated continuity:** Downstream cannot generate until the chain is resolved head-to-tail.
- **Shot removed from scene:** Remaining shots' predecessor resolution re-derives automatically from the ordered list (no stale foreign keys).

**Implementation:** A pure function `resolve_predecessor(shot_id, scene_id, conn)` that queries the ordered list and returns `predecessor_shot_id | None`.

### C. Continuity State Model

**Decision:** New `continuity_states` table tracks per-shot continuity chain membership and validity.

```sql
CREATE TABLE IF NOT EXISTS continuity_states (
    id                          TEXT PRIMARY KEY,
    shot_id                     TEXT NOT NULL UNIQUE,
    scene_id                    TEXT NOT NULL,
    predecessor_shot_id         TEXT,           -- NULL for chain heads
    upstream_take_id            TEXT,           -- approved Take used as source
    upstream_take_number        INTEGER,
    upstream_last_frame_path    TEXT,           -- managed path to last frame
    upstream_last_frame_sha256  TEXT,           -- SHA-256 of the frame file
    state                       TEXT NOT NULL DEFAULT 'unresolved',
                                -- unresolved: no upstream Take approved yet
                                -- current: upstream Take approved, frame valid
                                -- outdated: upstream Take changed/replaced
    continuity_revision         INTEGER NOT NULL DEFAULT 0,
    invalidation_reason         TEXT,
    invalidation_source_shot_id TEXT,           -- which shot triggered invalidation
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    invalidated_at              TEXT,
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    FOREIGN KEY (scene_id) REFERENCES scenes(id),
    FOREIGN KEY (predecessor_shot_id) REFERENCES shots(id),
    FOREIGN KEY (upstream_take_id) REFERENCES takes(id)
);

CREATE INDEX IF NOT EXISTS idx_continuity_shot ON continuity_states(shot_id);
CREATE INDEX IF NOT EXISTS idx_continuity_scene ON continuity_states(scene_id);
CREATE INDEX IF NOT EXISTS idx_continuity_predecessor ON continuity_states(predecessor_shot_id);
```

**State machine:**
```
unresolved → current    (upstream Take approved/replaced, frame captured)
current    → outdated   (upstream Take replaced or upstream became outdated)
outdated   → current    (re-resolved after upstream chain repaired)
unresolved → unresolved (no change — predecessor still has no approved Take)
```

**Continuity revision:** Monotonically increasing integer per shot. Incremented whenever the state transitions to `current` (new upstream frame resolved). Used in GenerationRequest snapshots to detect which upstream version was used.

**Separation from Take.status:** ContinuityState.state tracks whether the shot's _upstream input_ is valid. Take.status tracks the review lifecycle of the shot's _output_. These are orthogonal:
- A shot can have `state=outdated` AND `Take.status=approved` (the Take is historically approved but was generated with now-outdated upstream input).
- A shot with `state=current` may have no Take at all (upstream is ready but this shot hasn't been generated yet).

### D. Immutable Generation Snapshot

**Decision:** Extend GenerationRequest with a `continuity_snapshot` JSON field containing the exact upstream provenance used at generation time.

**New field on GenerationRequest:**
```python
continuity_snapshot: dict | None = None  # None for non-continuity shots
```

**Continuity snapshot structure:**
```json
{
    "upstream_shot_id": "shot_abc",
    "upstream_take_id": "take_xyz",
    "upstream_take_number": 2,
    "upstream_last_frame_managed_path": "storage/takes/proj/shot/take_2/last_frame.png",
    "upstream_last_frame_sha256": "a1b2c3...",
    "continuity_revision": 3,
    "continuity_workflow_definition_id": "h3_flf_v1",
    "continuity_workflow_definition_version": "1.0.0",
    "continuity_workflow_template_fingerprint": "deadbeef...",
    "upstream_frame_slot": "first_frame",
    "upstream_frame_node_id": "300"
}
```

**Database change:** Add nullable `continuity_snapshot TEXT` column to `generation_requests` table. For non-continuity shots, this is NULL. For continuity shots, it is a JSON blob.

**Immutability:** Once a GenerationRequest is created, the continuity_snapshot (like all other fields) is never modified. If the upstream Take changes, the GenerationRequest retains its historical snapshot. A new generation creates a new GenerationRequest with the updated snapshot.

### E. Approved-Take Replacement

**Decision:** Privileged `replace-approved` operation that atomically demotes the old approved Take to `superseded` status and approves the new Take.

**New terminal status:** `superseded` — added to the Take.status enum. An approved Take that has been replaced by a newer approval. Terminal (cannot transition further).

**Updated Take status enum:**
```python
Literal["pending", "generating", "succeeded", "failed", "approved", "rejected", "superseded"]
```

**Updated status transitions:**
```
succeeded → approved      (first approval for shot, or via replace-approved)
succeeded → rejected      (human rejects)
approved  → superseded    (replace-approved demotes the old)
superseded                (terminal, no further transitions)
```

**Operation: `replace_approved(shot_id, new_take_id)`**
1. Validate `new_take_id` exists, belongs to `shot_id`, status is `succeeded`.
2. Find current approved Take for shot (if any).
3. In one transaction:
   a. If old approved exists: CAS update `approved → superseded`.
   b. CAS update new Take `succeeded → approved`.
   c. Propagate downstream invalidation (see F).
4. Return the newly approved Take.

**Database constraints:**
- Partial unique index `idx_takes_one_approved_per_shot` on `(shot_id) WHERE status = 'approved'` — still works because at most one Take has `status='approved'` at any time.
- The `superseded` status does not violate the index.

**Audit history:** Both the old and new Takes are preserved. The old Take's status = `superseded` with its original `created_at` timestamp. A query `WHERE status IN ('approved', 'superseded')` reconstructs the full approval history.

**API contract:**
```
POST /shots/{shot_id}/replace-approved
  Body: { "take_id": "take_xyz" }
  Response: TakeResponse (newly approved)
  Errors:
    404 — shot or take not found
    409 — take not in 'succeeded' status, or take belongs to wrong shot
    409 — no change (take already approved)
```

**Concurrency:** CAS transitions prevent race conditions. If two concurrent `replace_approved` calls race, one succeeds and one gets a CAS failure (409).

**Backward compatibility:**
- `POST /takes/{take_id}/approve` continues to work for first approval (no existing approved Take).
- If a shot already has an approved Take, `approve` returns 409. The user must use `replace-approved`.
- `GET /shots/{shot_id}/approved-take` returns the current `status='approved'` Take (never `superseded`).

### F. Downstream Invalidation

**Decision:** When a shot's approved Take changes (via `approve`, `replace-approved`, or `superseded`), all downstream shots in the same scene have their ContinuityState marked `outdated`.

**Algorithm: `propagate_invalidation(shot_id, scene_id, conn)`**
1. Build the ordered list of current shots in the scene (same query as B).
2. Find the position of `shot_id` in the ordered list.
3. For every shot AFTER `shot_id` in the list:
   a. If that shot has a ContinuityState:
      - Set `state = 'outdated'`
      - Set `invalidation_reason = 'upstream_take_changed'`
      - Set `invalidation_source_shot_id = shot_id`
      - Set `invalidated_at = now`
      - Increment `continuity_revision` (so next resolution gets a new revision)
      - Set `updated_at = now`

**Properties:**
- **Deterministic:** Same input always produces the same output.
- **Idempotent:** Running twice with the same trigger produces the same result.
- **Transitive:** If shot 2 changes, shots 3, 4, 5 all become outdated (single pass, not recursive).
- **Scoped:** Only shots in the same scene are affected. Other scenes/projects are untouched.
- **Non-destructive:** Existing Takes and media are never deleted. Existing GenerationRequests remain unchanged. An outdated downstream Take retains its historical `approved` or `superseded` status.

**Trigger points:**
- `TakeService.approve(take_id)` — after first approval, propagate invalidation.
- `TakeService.replace_approved(shot_id, new_take_id)` — after replacement, propagate invalidation.

**What "outdated" means for production:**
- An outdated downstream Take is historically valid (it was approved at the time) but is production-ineligible until its continuity chain is repaired.
- `GET /shots/{shot_id}/approved-take` still returns the Take (it's still `status=approved`), but `GET /shots/{shot_id}/continuity-state` shows `state=outdated`.
- The generation service refuses to generate a downstream shot whose predecessor has `state=outdated`.

### G. Generation Eligibility

**Decision:** Downstream generation requires the immediate predecessor to have a `current` ContinuityState with an approved Take.

**Pre-request validation (added to GenerationService.generate_take):**
1. After loading shot + plan, resolve predecessor via canonical ordering.
2. If predecessor exists:
   a. Load predecessor's ContinuityState.
   b. If no approved Take for predecessor: raise `ContinuityError("Predecessor has no approved Take")`.
   c. If predecessor's ContinuityState is `outdated`: raise `ContinuityError("Predecessor continuity is outdated")`.
   d. If predecessor's approved Take has no `last_frame_path` or the file is missing: raise `ContinuityError("Predecessor last frame unavailable")`.
   e. Re-verify SHA-256 of the last frame file.
3. If predecessor is NULL (chain head): proceed without continuity input.

**Queue behavior:**
- Jobs are enqueued regardless of predecessor state. The queue does not check continuity.
- When the QueueWorker claims a job and calls `generate_take`, the pre-request validation checks predecessor state.
- If the predecessor is unavailable, the job fails with `ContinuityError` and the queue job is marked `failed`.
- The user can re-enqueue after resolving the predecessor.

**Explicit chain break:** A future enhancement could add a `break_continuity=True` flag to allow starting a new chain at any shot. This is NOT in M7 scope — every non-head shot in a scene requires its predecessor.

### H. H3 Workflow Strategy

**Decision:** Construct a new `h3_flf_v1` workflow using `MiniMaxH3ImageToVideo` (fl2va model) with `first_frame` input for continuity shots. R2V as secondary preflight candidate. Require live preflight before committing.

**H3 Node Analysis (empirical from ComfyUI /object_info):**

| Node | Model | Character Refs | First/Last Frame | Audio VAE |
|------|-------|---------------|-------------------|-----------|
| MiniMaxH3ReferenceToVideo | ref2va | Yes (ref_images, 1-9) | No | Required |
| MiniMaxH3ImageToVideo | fl2va | No | Yes (optional) | Not required |

**Primary approach — FLF workflow (h3_flf_v1):**
- Uses `MiniMaxH3ImageToVideo` with `first_frame = predecessor's approved Take's last frame`
- Uses `fl2va` UNET model (different from ref2va)
- Character identity propagates through the continuity frame (no explicit character refs)
- No `last_frame` input in M7 (only first_frame for forward continuity)
- Audio VAE handling: determine during preflight whether fl2va produces audio without explicit audio_vae input. The R2V node 104 takes an explicit audio_vae input, but FLF may not. If fl2va does NOT produce audio latents, the workflow template must omit VAEDecodeAudio (node 23) and the audio input to CreateVideo (node 91). This is a structural difference, not just a node swap. The preflight must verify audio output presence/absence and adjust the template accordingly.

**Secondary approach — R2V with continuity ref (r2v_v3):**
- Uses `MiniMaxH3ReferenceToVideo` with predecessor's last frame as an additional `ref_image`
- Keeps ref2va model (proven)
- Character refs + continuity frame combined
- Uncertain: R2V treats all ref_images as `<Picture N>` subject references, not temporal continuity signals
- Would require new r2v_v3 template (r2v_v1/v2 unchanged)

**Preflight requirement (M7.B):**
Before committing to either approach, M7.B must execute a live technical preflight:
1. Construct h3_flf_v1 workflow template from MiniMaxH3ImageToVideo node schema.
2. Test with a real predecessor last frame as first_frame input.
3. Verify: video output quality, character identity propagation, temporal coherence, audio output presence.
4. Optionally test r2v_v3 approach for comparison.
5. Only commit to the workflow that empirically demonstrates acceptable continuity.

**Workflow definition (h3_flf_v1 — tentative, finalized after preflight):**
```python
WorkflowDefinition(
    id="h3_flf_v1",
    version="1.0.0",
    strategy="FIRST_LAST_FRAME",  # existing strategy enum value
    template_path="workflows/h3/flf_v1.json",
    template_fingerprint="<computed after preflight>",
    parameter_mappings={
        "prompt": {"node_id": "104", "field": "prompt"},
        "first_frame": {"node_id": "300", "field": "image"},  # LoadImage for predecessor frame
        "duration": {"node_id": "111", "field": "value"},
        "seed": {"node_id": "15", "field": "noise_seed"},
        "aspect": {"node_id": "115", "field": "aspect_ratio"},
        "output_prefix": {"node_id": "92", "field": "filename_prefix"},
    },
    # Note: no ref_image_0/1 — FLF does not use character reference images
    constraints={
        "materialized_reference_slots": 0,
        "continuity_frame_slots": 1,
        "fps": 24,
        "frame_grid": "17k+5",
    },
)
```

**Backward compatibility:**
- r2v_v1 and r2v_v2 remain unchanged (fingerprints frozen).
- Existing REFERENCE_TO_VIDEO shots continue using r2v workflows.
- FIRST_LAST_FRAME strategy shots use the new flf_v1 workflow.
- WorkflowResolver gains a new method: `resolve_for_continuity(has_first_frame, reference_count)`.

**Interaction with character references:**
- FLF workflow does NOT accept ref_images. Character identity comes from the first_frame.
- For the first shot in a continuity chain (no predecessor): R2V workflow is used (character refs, no continuity).
- For subsequent shots: FLF workflow is used (continuity frame, no explicit character refs).
- This is an acceptable tradeoff for M7. Combined character-ref + continuity is a future enhancement.

**Maximum capacity:**
- FLF: 1 first_frame + 1 optional last_frame = 2 frame inputs max.
- R2V: 1-2 character refs (current), up to 9 provider max.
- M7 uses 1 first_frame only.

### I. First/Last Frame Handling

**Decision:** Strict validation, SHA verification, storage-root confinement, and atomic extraction.

**Input last-frame validation (before generation):**
1. **Path confinement:** `pathlib.Path(frame_path).resolve().is_relative_to(storage_root)` — reject any path outside storage root.
2. **Symlink prevention:** After resolution, verify the resolved path equals the original resolved path (no symlink chains escaping confinement).
3. **File existence:** `os.path.isfile(frame_path)` — must exist.
4. **File size:** Must be > 0 bytes.
5. **SHA-256 re-verification:** Compute SHA-256 of the file and compare against `ContinuityState.upstream_last_frame_sha256`. Reject on mismatch (corruption or tampering).
6. **Format validation:** Must be PNG (the format produced by `extract_last_frame`). Verify magic bytes `\x89PNG`.
7. **Dimension validation:** Use Pillow or ffprobe to read dimensions. Must be > 0 in both axes.

**Output last-frame extraction (after generation):**
- Same as M3/M6: `extract_last_frame(video_path, output_path)` using ffmpeg `-update 1`.
- Output path: `{final_dir}/last_frame.png` — already implemented.
- `last_frame_path` stored on Take record — already implemented.

**SHA-256 computation for new last frames:**
- After extraction, compute SHA-256 of the new last_frame.png.
- Store in ContinuityState when the Take is approved and the downstream state is resolved.

**Storage-root confinement:**
- All last_frame_path values must be within `{storage_root}/takes/`.
- The `make_final_dir` function already enforces this structure.
- Upload to ComfyUI uses the same `upload_image` mechanism as reference images.

**Cleanup and atomicity:**
- Same staging → final directory move as existing generation pipeline.
- If extraction fails, the entire Take finalization fails (no partial state).

**Missing or corrupted frame:**
- If a predecessor's last_frame.png is missing: `ContinuityError` at generation time.
- If SHA mismatch: `ContinuityError` at generation time.
- Neither condition triggers automatic re-generation. The user must approve a new Take for the predecessor (which re-extracts the last frame).

### J. API Contract

**New M7 endpoints:**

```
GET /shots/{shot_id}/continuity-state
  Response: ContinuityStateResponse | 404
  Returns the continuity state for a shot, including predecessor,
  upstream Take provenance, and current/outdated/unresolved state.

GET /shots/{shot_id}/predecessor
  Response: { predecessor_shot_id: str | null, scene_id: str }
  Returns the resolved predecessor for a shot using canonical ordering.

POST /shots/{shot_id}/replace-approved
  Body: { "take_id": "take_xyz" }
  Response: TakeResponse (newly approved Take)
  Atomically replaces the approved Take and propagates downstream invalidation.
  Errors: 404, 409

GET /scenes/{scene_id}/continuity-chain
  Response: list[ContinuityChainEntry]
  Returns the full continuity chain for a scene: ordered shots with their
  continuity state, predecessor, and approved Take info.

GET /scenes/{scene_id}/outdated-shots
  Response: list[ShotSummary]
  Returns shots with outdated continuity state in the scene.
```

**Response DTOs:**

```python
class ContinuityStateResponse(BaseModel):
    shot_id: str
    scene_id: str
    predecessor_shot_id: str | None
    state: Literal["unresolved", "current", "outdated"]
    upstream_take_id: str | None
    upstream_take_number: int | None
    upstream_last_frame_sha256: str | None
    continuity_revision: int
    invalidation_reason: str | None
    invalidation_source_shot_id: str | None
    updated_at: str

class ContinuityChainEntry(BaseModel):
    shot_id: str
    order_position: int          # 0-based position in scene
    predecessor_shot_id: str | None
    continuity_state: Literal["unresolved", "current", "outdated"] | None
    approved_take_id: str | None
    has_last_frame: bool
```

**Error mappings:**
- `ContinuityError` → 409 Conflict
- Shot/Take not found → 404

**Existing endpoint changes:**
- `POST /takes/{take_id}/approve` — after successful approval, resolves downstream continuity states.
- `POST /shots/{shot_id}/enqueue-takes` — no change (queue does not check continuity).

### K. Live Acceptance

**M7.F Live Acceptance Plan:**

Uses a copy of M6 live data. M5/M6 worktrees and evidence are never touched.

**Setup:**
1. Copy `data/m6_live.db` to `data/m7_live.db` in the M7 worktree.
2. Run `db.init_schema()` to add M7 tables (continuity_states, generation_request migration).
3. Verify M6 data integrity (existing Takes, approved Take, references).

**Phase M7.F1 — Five-Shot Continuity Chain:**
1. Create or reuse 5 sequential shots in one scene (ordered by canonical ordering).
2. Verify predecessor resolution: shot 1 has no predecessor, shots 2-5 each have the prior shot.
3. Generate and approve Take for shot 1 (using R2V, no continuity input).
4. Verify ContinuityState for shot 2 transitions to `current` after shot 1 approval.
5. Generate shot 2 using FLF workflow with shot 1's last frame as first_frame.
6. Approve shot 2. Verify shot 3's ContinuityState becomes `current`.
7. Repeat for shots 3, 4, 5.
8. Human visually inspects all 5 videos for continuity (character, environment, temporal coherence).

**Phase M7.F2 — Provenance Verification:**
1. For each downstream Take (shots 2-5), verify GenerationRequest.continuity_snapshot contains:
   - Correct upstream_shot_id, upstream_take_id, upstream_take_number
   - Correct upstream_last_frame_sha256 (matches actual file)
   - Correct continuity_revision
   - Correct workflow definition (h3_flf_v1)
2. Verify upstream frame file paths are confined to storage root.

**Phase M7.F3 — Replace and Invalidation:**
1. Replace the approved Take for shot 2 with a different succeeded Take (or generate a new one).
2. Verify shot 2's old Take status becomes `superseded`.
3. Verify shots 3, 4, 5 ContinuityState becomes `outdated`.
4. Verify invalidation_reason = 'upstream_take_changed' and invalidation_source_shot_id = shot 2's ID.
5. Verify shot 1 is unaffected (still `current` or chain head).
6. Verify all existing Takes and media files remain present and unmodified.

**Phase M7.F4 — Chain Repair:**
1. Regenerate and approve shot 3 using the new shot 2's last frame.
2. Verify shot 3's ContinuityState becomes `current`.
3. Verify shots 4, 5 remain `outdated` (they depend on shot 3's old output, not the new one).
4. Regenerate and approve shots 4, 5 in order.
5. Verify entire chain is `current`.

**M5/M6 Evidence Preservation:**
- SHA-256 verification of M5/M6 worktree files before and after M7 live tests.
- M7 operates exclusively in its own worktree with its own database and storage.

### L. Scope Boundaries

**Explicitly excluded from M7:**
- M8 AI visual review and regeneration recommendations
- M9 UI
- M10 audio-language correction and export
- Distributed workers
- Automatic deletion of Takes or media
- Modification of immutable historical GenerationRequests
- Cross-scene continuity chains
- Cross-sequence continuity chains
- Combined character-ref + continuity in a single workflow (FLF has no ref_images)
- Automatic re-generation of outdated downstream shots

**M10 hardening notes carried forward (not implemented):**
1. Clear or separate historical QueueJob error after successful recovery.
2. Replace timestamp-based claim retrieval with `UPDATE ... RETURNING`.
3. Explicit H3 output-language control and native-audio validation.

---

## Continuity Model Extension

```python
class ContinuityState(BaseModel):
    """Per-shot continuity chain membership and upstream validity."""
    id: str
    shot_id: str
    scene_id: str
    predecessor_shot_id: str | None = None
    upstream_take_id: str | None = None
    upstream_take_number: int | None = None
    upstream_last_frame_path: str | None = None
    upstream_last_frame_sha256: str | None = None
    state: Literal["unresolved", "current", "outdated"] = "unresolved"
    continuity_revision: int = 0
    invalidation_reason: str | None = None
    invalidation_source_shot_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    invalidated_at: str | None = None
```

## Take Status Extension

```python
# Updated status enum
Literal["pending", "generating", "succeeded", "failed",
        "approved", "rejected", "superseded"]
```

**New transition:** `approved → superseded` (via `replace_approved` only).

## File Structure

```
src/film_director/
  continuity/
    continuity_models.py        # CREATE: ContinuityState model
    continuity_service.py       # CREATE: predecessor resolution, state management,
                                #         invalidation propagation, frame validation
    continuity_resolver.py      # CREATE: canonical ordering query, predecessor lookup
  generation/
    generation_request.py       # MODIFY: add continuity_snapshot field
    generation_service.py       # MODIFY: continuity-aware generation pipeline
    parameter_resolver.py       # MODIFY: continuity frame injection
    workflow_registry.py        # MODIFY: add h3_flf_v1 definition, resolve_for_continuity
  services/
    take_service.py             # MODIFY: replace_approved, invalidation trigger
  persistence/
    database.py                 # MODIFY: continuity_states table, generation_request migration
    repositories.py             # MODIFY: ContinuityStateRepository, extend ShotRepository
  api/routes.py                 # MODIFY: M7 endpoints
  errors.py                     # MODIFY: ContinuityError
workflows/h3/
  flf_v1.json                  # CREATE: FLF workflow template (after preflight)
tests/
  unit/
    test_continuity_models.py   # CREATE
    test_predecessor.py         # CREATE
    test_invalidation.py        # CREATE
  integration/
    test_continuity_service.py  # CREATE
    test_replace_approved.py    # CREATE
    test_continuity_api.py      # CREATE
    test_continuity_generation.py # CREATE
    test_m7_live.py             # CREATE: @pytest.mark.live
```

## Task Dependency Table

| Task | Requires | Produces |
|------|----------|----------|
| M7.A | None | ContinuityState model, table, repository, predecessor resolver, continuity_snapshot field |
| M7.B | M7.A | FLF workflow preflight, finalized h3_flf_v1 template + definition |
| M7.C | M7.A, M7.B | Continuity-aware GenerationService, frame validation, parameter injection |
| M7.D | M7.A | Replace-approved operation, superseded status, downstream invalidation |
| M7.E | M7.C, M7.D | Continuity API endpoints, queue dependency behavior |
| M7.F | M7.A-E | Five-shot live acceptance, invalidation proof |

**Forward dependencies: ZERO.**

---

### Task M7.A: Continuity Models, Persistence, Ordering, and Fingerprinting

**Goal:** Create the ContinuityState model, database table, repository, canonical predecessor resolver, and extend GenerationRequest with continuity_snapshot.

**Files:** Create `continuity/continuity_models.py`, `continuity/continuity_resolver.py`. Modify `persistence/database.py`, `persistence/repositories.py`, `generation/generation_request.py`, `errors.py`.

**ContinuityState model:**
- Pydantic model as specified in Architecture Decision C.
- State enum: `unresolved`, `current`, `outdated`.

**Database:**
- Add `continuity_states` table as specified in C.
- Add `continuity_snapshot TEXT` column to `generation_requests` (nullable, migration-safe).

**ContinuityStateRepository:**
- `save(state, conn)` — UPSERT by shot_id.
- `get_by_shot(shot_id, conn)` → ContinuityState | None.
- `get_by_scene(scene_id, conn)` → list[ContinuityState].
- `mark_outdated(shot_id, reason, source_shot_id, conn)` — state → outdated.
- `resolve_current(shot_id, upstream_take, frame_path, frame_sha256, conn)` — state → current, bump revision.

**ContinuityResolver:**
- `get_scene_shot_order(scene_id, conn)` → list[str] (ordered shot_ids).
- `resolve_predecessor(shot_id, conn)` → str | None.
- `get_downstream_shots(shot_id, scene_id, conn)` → list[str] (all shots after shot_id in scene order).
- Pure query functions, no side effects.

**GenerationRequest extension:**
- Add `continuity_snapshot: dict | None = None` field.
- Database column: `continuity_snapshot TEXT` (nullable JSON).
- Repository: serialize/deserialize JSON.

**Error extension:**
- Add `ContinuityError(FilmDirectorError)`.

**Tests:** ContinuityState round-trip, predecessor resolution (first/middle/last/missing), downstream enumeration, GenerationRequest with continuity_snapshot, state transitions.

- [ ] **Steps: TDD, models, schema, repository, resolver, tests, commit**

```bash
git commit -m "M7.A: continuity models, persistence, ordering, and fingerprinting"
```

**STOP — verify M7.A before proceeding.**

---

### Task M7.B: Workflow Technical Preflight and Versioned Continuity Bindings

**Goal:** Construct the FLF workflow template, execute a live preflight, and finalize the h3_flf_v1 WorkflowDefinition.

**Files:** Create `workflows/h3/flf_v1.json`. Modify `generation/workflow_registry.py`.

**Preflight procedure:**
1. Use ComfyUI MCP (development tool, ADR-004) to inspect `MiniMaxH3ImageToVideo` full schema.
2. Construct `flf_v1.json` workflow template:
   - Node 104: MiniMaxH3ImageToVideo with `first_frame` from LoadImage node 300.
   - Node 300: LoadImage (predecessor's last frame).
   - Node 6: UNETLoader with `fl2va` model (verify exact model filename).
   - Node 13: CLIPLoader (same qwen3vl as R2V).
   - All other nodes identical to r2v_v1 EXCEPT:
     - No audio_vae input on node 104 (FLF node doesn't accept it).
     - Determine audio handling: check if fl2va produces audio latents via the same sampler output.
   - Output: SaveVideo node 92.
3. Submit a test prompt with a real predecessor last frame (from M6 approved Take).
4. Verify:
   - Video output produced (non-zero, ffprobe valid).
   - Audio presence/absence (determines whether audio VAE path is needed).
   - Visual continuity: first frames of output match the input frame.
   - Character identity: recognizable from the input frame.
   - Temporal coherence: smooth motion continuation.
5. If FLF quality is acceptable: compute fingerprint, register h3_flf_v1.
6. If FLF quality is unacceptable: test R2V + extra ref_image approach as fallback.

**WorkflowResolver update:**
- Register `h3_flf_v1` definition with computed fingerprint.
- Add `resolve_for_continuity(has_predecessor_frame: bool, reference_count: int) → WorkflowDefinition`:
  - If has_predecessor_frame: return h3_flf_v1.
  - If no predecessor: return r2v_v1 or r2v_v2 based on reference_count (existing behavior).

**fl2va model discovery:**
- Check `D:\ComfyUI\TD_1\ComfyUI\models\diffusion_models\` for fl2va checkpoint.
- If not installed: download before preflight (use ComfyUI MCP `download_model`).
- Record exact filename for workflow template.

**Human checkpoint:** Preflight results reviewed by human before proceeding. The chosen workflow approach is frozen for the remainder of M7.

**Tests:** WorkflowDefinition h3_flf_v1 registration, fingerprint verification, resolve_for_continuity routing, template loading and node validation.

- [ ] **Steps: node inspection, workflow construction, live preflight, human review, definition registration, tests, commit**

```bash
git commit -m "M7.B: FLF workflow preflight and versioned continuity bindings"
```

**STOP — human must review preflight results before proceeding.**

---

### Task M7.C: Continuity Resolution and GenerationService Integration

**Goal:** Make the generation pipeline continuity-aware: resolve predecessor, validate upstream frame, upload to ComfyUI, inject into FLF workflow, and persist continuity_snapshot.

**Files:** Create `continuity/continuity_service.py`. Modify `generation/generation_service.py`, `generation/parameter_resolver.py`.

**ContinuityService:**
- `resolve_for_generation(shot_id, conn)` → ContinuityInput | None:
  (Internally derives scene_id via shot→beat→scene join, same pattern as GenerationService._find_project_id.)
  1. Resolve predecessor via ContinuityResolver.
  2. If no predecessor: return None (chain head).
  3. Load predecessor's approved Take.
  4. Validate last_frame_path: exists, SHA-256 matches, confined to storage_root, PNG format, dimensions valid.
  5. Return ContinuityInput (upstream_shot_id, upstream_take_id, upstream_take_number, frame_path, frame_sha256, continuity_revision).
- `resolve_after_approval(shot_id, take_id, conn)`:
  1. Compute SHA-256 of the approved Take's last_frame_path.
  2. For each downstream shot in the scene:
     a. Create or update ContinuityState to `current` with the upstream Take info.
     b. If the downstream shot already had a `current` state with a different upstream Take: mark `outdated` instead (the old continuity is broken).

**GenerationService.generate_take evolution:**
- After loading shot + plan, before reference resolution:
  1. Call `ContinuityService.resolve_for_generation(shot_id)`.
  2. If ContinuityInput returned:
     a. Select FLF workflow via `WorkflowResolver.resolve_for_continuity(True, 0)`.
     b. Upload predecessor frame to ComfyUI via `upload_image`.
     c. Build continuity-specific injections (first_frame → node 300).
     d. Build prompt WITHOUT character references (FLF workflow).
     e. Skip ReferenceSelector (no character refs in FLF).
  3. If None returned (chain head or no continuity):
     a. Proceed with existing R2V path (unchanged).
- After creating GenerationRequest:
  - Populate `continuity_snapshot` with ContinuityInput data.
- Existing R2V path remains 100% unchanged for non-continuity shots.

**ParameterResolver extension:**
- `build_continuity_injections(continuity_input, workflow_def, uploaded_frame_filename)` → list[WorkflowInjection]:
  - first_frame injection: node 300, field "image", value = uploaded filename.
  - Combined with prompt, duration, seed, aspect, output_prefix injections.

**ContinuityInput (frozen dataclass):**
```python
@dataclass(frozen=True)
class ContinuityInput:
    upstream_shot_id: str
    upstream_take_id: str
    upstream_take_number: int
    frame_path: str
    frame_sha256: str
    continuity_revision: int
```

**Tests:** End-to-end generation with mocked predecessor frame, continuity_snapshot persisted correctly, chain head skips continuity, FLF workflow selected for continuity shots, R2V workflow selected for chain heads, frame validation failures, SHA mismatch detection.

- [ ] **Steps: TDD, ContinuityService, GenerationService evolution, ParameterResolver extension, tests, commit**

```bash
git commit -m "M7.C: continuity resolution and GenerationService integration"
```

**STOP — verify M7.C before proceeding.**

---

### Task M7.D: Approved-Take Replacement and Downstream Invalidation

**Goal:** Implement the atomic replace-approved operation with `superseded` status and cascading downstream invalidation.

**Files:** Modify `generation/generation_request.py`, `services/take_service.py`, `persistence/repositories.py`, `persistence/database.py`.

**Take status extension:**
- Add `superseded` to Take.status Literal type.
- Add CAS transition: `approved → superseded` (in TakeRepository).

**TakeService.replace_approved(shot_id, new_take_id):**
1. Validate new_take_id exists, belongs to shot_id, status=succeeded.
2. In one transaction (`db.connection()`):
   a. Find current approved Take for shot (`get_approved_for_shot`).
   b. If current approved exists AND is not the new take:
      - CAS update `approved → superseded`.
      - If CAS fails: raise TakeConflictError.
   c. If new take is already approved: return it (idempotent).
   d. CAS update new Take `succeeded → approved`.
   e. If CAS fails: raise TakeLifecycleError.
   f. Compute SHA-256 of new Take's last_frame_path.
   g. Call `ContinuityService.propagate_invalidation(shot_id, scene_id, conn)`.
   h. Call `ContinuityService.resolve_immediate_downstream(shot_id, take_id, conn)` to update the immediate successor's ContinuityState.
3. Return the newly approved Take.

**TakeService.approve evolution:**
- After existing approval logic, add:
  - Compute SHA-256 of approved Take's last_frame_path (if present).
  - Resolve scene_id from shot.
  - Call `ContinuityService.propagate_invalidation(shot_id, scene_id, conn)`.
  - Call `ContinuityService.resolve_immediate_downstream(shot_id, take_id, conn)`.

**Invalidation propagation:**
- `ContinuityService.propagate_invalidation(shot_id, scene_id, conn)`:
  1. Get downstream shots via ContinuityResolver.
  2. For each downstream shot with a ContinuityState:
     - Mark outdated with reason and source.

**Tests:** Replace-approved atomic operation, superseded status terminal, one-approved invariant preserved, downstream invalidation cascades, idempotent replacement, concurrent replacement CAS, invalidation scope limited to scene, chain head unaffected, existing Takes/media preserved.

- [ ] **Steps: TDD, status extension, replace_approved, invalidation, tests, commit**

```bash
git commit -m "M7.D: approved-Take replacement and downstream invalidation"
```

**STOP — verify M7.D before proceeding.**

---

### Task M7.E: Continuity API and Queue Dependency Behavior

**Goal:** Expose continuity state via REST API. Document queue behavior for continuity-dependent shots.

**Files:** Modify `api/routes.py`, `main.py`.

**New routes:**
```python
@router.get("/shots/{shot_id}/continuity-state")
@router.get("/shots/{shot_id}/predecessor")
@router.post("/shots/{shot_id}/replace-approved")
@router.get("/scenes/{scene_id}/continuity-chain")
@router.get("/scenes/{scene_id}/outdated-shots")
```

**Error mapping:**
- ContinuityError → 409 Conflict

**Wiring:**
- ContinuityService, ContinuityResolver, ContinuityStateRepository injected via main.py.

**Queue behavior documentation:**
- Queue enqueue does NOT check continuity state. Jobs are accepted regardless.
- When the worker executes a job, GenerationService checks continuity at generation time.
- If predecessor is unavailable, the job fails with ContinuityError.
- The queue job is marked `failed` with the error message.
- The user must resolve the predecessor (approve a Take) and re-enqueue.
- This is intentional: the queue is a generation scheduler, not a continuity planner.

**Tests:** All API endpoints with mocked services, error mapping, predecessor resolution via API, chain listing, outdated-shots filtering.

- [ ] **Steps: TDD, routes, wiring, error mapping, tests, commit**

```bash
git commit -m "M7.E: continuity API and queue dependency behavior"
```

**STOP — verify M7.E before proceeding.**

---

### Task M7.F: Five-Shot Live Acceptance and Invalidation Proof

**Goal:** Real end-to-end proof of five-shot continuity chain, provenance, and invalidation.

**Project:** Uses M6 live data (copied, not modified).
**Shot:** Five sequential shots in one scene.

**Phase M7.F1 — Five-Shot Chain:**
1. Copy M6 production DB to M7 worktree.
2. Run `db.init_schema()` for M7 tables.
3. Create or identify 5 sequential shots in one scene.
4. Verify predecessor resolution chain: 1→NULL, 2→1, 3→2, 4→3, 5→4.
5. Generate and approve shot 1 (R2V, no continuity).
6. Verify shot 2 ContinuityState = current.
7. Generate shot 2 (FLF with shot 1's last frame). Approve.
8. Repeat for shots 3, 4, 5.
9. Human visual inspection: 5 videos with visible continuity.

**Phase M7.F2 — Provenance Verification:**
1. For shots 2-5: verify GenerationRequest.continuity_snapshot correctness.
2. Verify SHA-256 matches for all upstream frames.
3. Verify workflow = h3_flf_v1 for shots 2-5, h3_r2v_v1/v2 for shot 1.

**Phase M7.F3 — Replace and Invalidate:**
1. Replace shot 2's approved Take.
2. Verify old Take → superseded, new Take → approved.
3. Verify shots 3, 4, 5 → outdated.
4. Verify shot 1 unaffected.
5. Verify all media files preserved.

**Phase M7.F4 — Chain Repair:**
1. Regenerate and approve shots 3, 4, 5 in order.
2. Verify entire chain → current.

**M5/M6 preservation:** SHA verification of source worktree files before and after.

- [ ] **Steps: setup, five-shot chain, provenance, replace+invalidate, repair, preservation check, commit**

```bash
git commit -m "M7.F: five-shot live continuity acceptance and invalidation proof"
```

**STOP — human reviews live results before declaring M7 complete.**

---

## M7 Exit Criteria

1. ContinuityState model persists per-shot chain membership with state machine (unresolved/current/outdated)
2. Deterministic predecessor resolution from canonical scene ordering
3. Five sequential shots generated with automatic continuity chain
4. Each downstream shot consumes predecessor's approved Take's last frame
5. FLF workflow (h3_flf_v1) empirically proven with live preflight
6. Continuity snapshot immutably persisted in every downstream GenerationRequest
7. SHA-256 verified upstream frame provenance
8. Replace-approved atomically demotes old Take to superseded
9. Downstream invalidation cascades correctly (shots 3-5 outdated when shot 2 replaced)
10. Invalidation scoped to scene (other scenes unaffected)
11. Existing Takes and media never deleted
12. Existing immutable GenerationRequests unchanged
13. r2v_v1 and r2v_v2 fingerprints unchanged
14. M3 synchronous single-take generation backward compatible
15. M5 reference selection/lifecycle unchanged
16. M6 queue/take review unchanged
17. Storage-root confinement and path validation for all frame inputs
18. Continuity API endpoints functional (state, predecessor, chain, outdated, replace)
19. M0-M6 deterministic regressions green (1320+)
20. No M8 AI review leakage
21. No M9 UI leakage
22. No cross-scene automatic chaining
23. Human visual acceptance of 5-shot continuity chain

## Task Dependency Audit

| Task | Forward refs? | M8+? | Verdict |
|------|-------------|------|---------|
| M7.A | NO | NO | PASS |
| M7.B | NO | NO | PASS |
| M7.C | NO | NO | PASS |
| M7.D | NO | NO | PASS |
| M7.E | NO | NO | PASS |
| M7.F | NO | NO | PASS |

**Forward dependencies: ZERO.**

## Backlog (NOT M7 scope)

- AI visual quality reviewer — M8
- Smart retry/regeneration suggestions — M8
- Combined character-ref + continuity workflow (R2V with continuity frame) — post-M7
- Cross-scene continuity links — post-M7
- Automatic re-generation of outdated downstream shots — M8
- Production UI — M9
- Audio-language control — M10
- Distributed GPU workers — Post-M10
