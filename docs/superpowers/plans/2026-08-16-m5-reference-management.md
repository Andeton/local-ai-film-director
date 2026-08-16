# M5 Reference Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manage image reference assets for canonical characters and storyboard shots — ingestion from WC/user/generation, provenance + review lifecycle, deterministic selection, provider-specific H3 binding, and real character-reference generation with human approval.

**Architecture:** ReferenceAsset is a new canonical entity with kind/source separation, approval lifecycle independent from source freshness, and managed file storage. References are selected deterministically and drive one authoritative binding list that controls prompt tags, uploads, workflow slots, and GenerationRequest snapshots. H3ReferenceBinding evolves to support nullable subject_index for future non-subject references. A new versioned H3 R2V workflow proves multi-reference support empirically.

**Tech Stack:** Python 3.14.3, FastAPI, Pydantic v2, SQLite, ComfyUI REST/WS, PIL/imagemagick for validation, hashlib for SHA-256

## Global Constraints

- Architecture FROZEN V1 — ADR-001 through ADR-005 remain unchanged
- Wind Comic is read-only sidecar — no WC SQLite writes
- ReferenceAsset is source-neutral canonical entity — no provider fields
- H3ReferenceBinding is provider-specific — no canonical lifecycle semantics
- ReferenceGenerationRequest is immutable; execution lifecycle is separate
- CANDIDATE references never auto-enter production selection
- STALE is independent from approval — no "reapprove to make current"
- User-uploaded images are not auto-staled by WC appearance text changes
- ReferenceSelector does NOT silently change generation strategy
- Existing M3 r2v_v1.json workflow preserved for reproducibility
- M0-M4 tests (884 deterministic + 7 live) must remain green
- No M6 queue/multi-take. No M7 continuity/previous-frame. No UI.

## Frozen Enums

### ReferenceKind

```python
class ReferenceKind(str, Enum):
    CHARACTER_FACE = "character_face"
    CHARACTER_BODY = "character_body"
    STORYBOARD = "storyboard"
```

Kind = what production role the image serves. Deferred: SCENE, STYLE, PREVIOUS_FRAME.

### ReferenceSource

```python
class ReferenceSource(str, Enum):
    USER_UPLOAD = "user_upload"
    WIND_COMIC = "wind_comic"
    GENERATED = "generated"
```

Source = where the asset came from. Independent of kind.

### ReferenceStatus

```python
class ReferenceStatus(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"
```

### ReferenceSourceState

```python
class ReferenceSourceState(str, Enum):
    CURRENT = "current"
    STALE = "stale"
```

## Frozen Canonical Models

### ReferenceAsset

```python
class ReferenceAsset(BaseModel):
    id: str
    project_id: str
    character_id: str | None = None    # set for CHARACTER_FACE/BODY
    shot_id: str | None = None         # set for STORYBOARD
    kind: ReferenceKind
    source: ReferenceSource
    managed_path: str                  # relative path in managed storage
    content_sha256: str
    source_provenance: str             # origin ID (gen request / WC asset / upload)
    source_fingerprint: str | None = None  # char appearance hash at creation
    status: ReferenceStatus = ReferenceStatus.CANDIDATE
    source_state: ReferenceSourceState = ReferenceSourceState.CURRENT
    pinned: bool = False
    width: int | None = None
    height: int | None = None
    created_at: str = ""
    updated_at: str = ""
```

Ownership invariant: exactly one of (character_id, shot_id) is non-None.

Storage layout: `storage/references/{project_id}/{reference_asset_id}/original.{ext}`

### ReferenceGenerationRequest (immutable input)

```python
class ReferenceGenerationRequest(BaseModel):
    id: str
    project_id: str
    character_id: str
    requested_kind: ReferenceKind      # CHARACTER_FACE or CHARACTER_BODY
    source_appearance_hash: str        # SHA of character appearance at request time
    prompt: str
    negative_prompt: str = ""
    workflow_definition_id: str
    workflow_definition_version: str
    workflow_template_fingerprint: str
    parameters_snapshot: list[dict] = Field(default_factory=list)
    seed: int
    created_at: str = ""
```

Once inserted, NEVER modified.

### ReferenceGenerationExecution (mutable lifecycle)

```python
class ReferenceGenerationExecution(BaseModel):
    id: str
    request_id: str
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    comfyui_prompt_id: str | None = None
    output_reference_asset_id: str | None = None
    submitted_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
```

### H3ReferenceBinding (evolved)

```python
@dataclass(frozen=True)
class H3ReferenceBinding:
    reference_asset_id: str          # NEW — canonical ref ID
    reference_kind: str              # NEW — ReferenceKind value
    subject_index: int | None        # nullable for non-subject refs
    character_id: str | None         # nullable for non-character refs
    character_name: str | None       # None for non-character refs
    appearance: str | None           # None for non-character refs
    picture_index: int               # 1-based, from position in selected list
    local_path: str
    content_sha256: str
    uploaded_filename: str = ""
```

picture_index derived from position in selected list. subject_index may be None or repeat.

## Selection Rules (frozen)

Priority:
1. pinned=True, status=APPROVED, source_state=CURRENT
2. pinned=False, status=APPROVED, source_state=CURRENT
3. No eligible reference → ReferenceResolutionError

Tie-breaker: created_at DESC, then id ASC.

CANDIDATE never production-eligible. REJECTED/ARCHIVED never eligible.
Pinned does not override REJECTED/ARCHIVED/STALE.

ReferenceSelector returns bindings or errors. No hidden strategy downgrade.

## Production Eligibility

`status == APPROVED AND source_state == CURRENT`

Stale approved reference is NOT eligible. No "reapprove to make current" path.
Generated refs become STALE when source appearance fingerprint changes.
User-uploaded refs stay CURRENT unless owning character is outdated.

## Stale Rules

- Character appearance hash changes → GENERATED refs for that character: source_state=STALE
- USER_UPLOAD refs: NOT auto-staled by WC text changes
- Stale refs: file preserved, SHA preserved, status unchanged, pin unchanged
- Historical GenerationRequests/Takes using stale refs: immutable, valid
- No deletion, no automatic archive

## Error Taxonomy (additions)

```python
class ReferenceIngestError(FilmDirectorError):
    """Reference file validation, download, or storage failure."""

class ReferenceGenerationError(FilmDirectorError):
    """ComfyUI reference image generation failure."""
```

## File Structure

```
src/film_director/
  models/
    reference.py                 # CREATE: ReferenceAsset, ReferenceKind, etc.
  generation/
    h3_types.py                  # MODIFY: evolve H3ReferenceBinding
    h3_prompt.py                 # MODIFY: updated binding validation
    h3_reference_resolver.py     # MODIFY: use ReferenceSelector output
    parameter_resolver.py        # MODIFY: multi-ref injection
    workflow_registry.py         # MODIFY: add r2v_v2 definition
    reference_generator.py       # CREATE: ComfyUI image generation service
  services/
    reference_service.py         # CREATE: ingest, generate, approve, select
  persistence/
    database.py                  # MODIFY: new tables
    repositories.py              # MODIFY: new repositories
  errors.py                      # MODIFY: new error types
  api/routes.py                  # MODIFY: reference API endpoints
  main.py                        # MODIFY: wire reference services
workflows/
  h3/
    r2v_v1.json                  # PRESERVE: M3 reproducibility
    r2v_v2.json                  # CREATE: 2-picture R2V (after M5.E preflight)
  reference/
    <versioned generator profile workflows>  # CREATE: selectable profiles for both Z-Image Turbo + Krea 2 Turbo (after M5.C preflight)
tests/
  unit/
    test_reference_model.py
    test_reference_selector.py
    test_h3_binding_evolution.py
  integration/
    test_reference_ingest.py
    test_reference_generation.py
    test_reference_selection.py
    test_reference_api.py
    test_m5_live.py              # @pytest.mark.live
```

## Task Dependency Table

| Task | Requires | Produces |
|---|---|---|
| M5.A | None | ReferenceAsset, RefGenRequest, RefGenExecution models + persistence + storage |
| M5.B | M5.A | User ingest, WC media ingest, image validation, managed storage |
| M5.C | M5.A | ComfyUI image workflow discovery, reference generation service |
| M5.D | M5.A, M5.B (M5.C optional — tests use fixture images) | Approval/pinning, deterministic ReferenceSelector |
| M5.E | M5.D | H3 multi-ref preflight, binding evolution, r2v_v2 workflow |
| M5.F | M5.A | Stale propagation on character appearance changes |
| M5.G | M5.A-F | Reference management API endpoints |
| M5.H | M5.A-G | Live acceptance with human checkpoints |

**Forward dependencies: ZERO.**

---

### Task M5.A: Reference Data Model + Persistence

**Goal:** ReferenceAsset, ReferenceGenerationRequest, ReferenceGenerationExecution models, DB tables, repositories, and managed storage layout.

**Files:** Create `models/reference.py`. Modify `persistence/database.py`, `persistence/repositories.py`, `errors.py`.

**DB tables:**
- `reference_assets` — full ReferenceAsset schema with FKs
- `reference_generation_requests` — immutable input snapshots
- `reference_generation_executions` — mutable execution lifecycle

**Repositories:**
- ReferenceAssetRepository — UPSERT, get by project/character/shot, filter by status/state
- ReferenceGenerationRequestRepository — INSERT-ONLY
- ReferenceGenerationExecutionRepository — INSERT + status updates

**Storage:** `storage/references/{project_id}/{reference_asset_id}/original.{ext}`

**Tests:** Model validation (kind/source enums, ownership invariants, SHA format), repository round-trip, managed path construction, lifecycle state transitions.

- [ ] **Steps: TDD, models, tables, repositories, tests, commit**

```bash
git commit -m "M5.A: reference asset data model and persistence"
```

---

### Task M5.B: Managed File Ingest

**Goal:** User-provided and WC media reference ingestion with image validation, SHA-256, managed storage.

**Files:** Create `services/reference_service.py` (ingest methods). Tests in `tests/integration/test_reference_ingest.py`.

**User ingest:**
- Accept file path → validate image (PIL) → compute SHA → copy to managed storage → create ReferenceAsset(CANDIDATE)
- Dedup: same project+character+kind+SHA → return existing (idempotent)
- Extract width/height from image

**WC media ingest:**
- Download from WC media_urls/persistent_url → validate → store → ReferenceAsset(WIND_COMIC, CANDIDATE)
- Per-item outcome: IMPORTED / DUPLICATE / MISSING_SOURCE / DOWNLOAD_FAILED / INVALID_IMAGE
- No direct WC SQLite writes

**Tests:** Valid image ingest, invalid file rejected, SHA computed, dedup behavior, WC download (fake HTTP), missing URL handling.

- [ ] **Steps: TDD, ingest service, image validation, managed storage, tests, commit**

```bash
git commit -m "M5.B: managed reference file ingest with validation"
```

---

### Task M5.C: Character Reference Generation Workflow

**Goal:** Build versioned/selectable ComfyUI txt2img workflow profiles for character reference generation using verified installed local models.

**Files:** Create `generation/reference_generator.py`, versioned workflow JSON files under `workflows/reference/`. Modify `generation/workflow_registry.py`.

**Preflight (M5.C.1) — COMPLETE:**
Both Z-Image Turbo and Krea 2 Turbo verified runnable via ComfyUI REST API:
- Z-Image Turbo: ~5s, UNETLoader → CLIPLoader(qwen_image) → KSampler, cfg 1.5
- Krea 2 Turbo: ~25s, UNETLoader → CLIPLoader(krea2) → Krea2PromptWeight → KSampler, cfg 1.0
- Both produced valid 1024x1024 character reference images from canonical character description
- No ReferenceAsset or ReferenceGenerationRequest created during preflight

**Model/Workflow Profile Policy (M5.C.2 — Frozen):**
- Both models remain available/selectable as versioned generator profiles
- Z-Image Turbo is currently recommended default for CHARACTER_BODY (faster, strong canonical adherence)
- Krea 2 Turbo remains supported/selectable
- ReferenceGenerationRequest must snapshot exact selected profile/model/workflow/settings
- Existing workflow versions NEVER silently overwritten; new versions added alongside
- Do NOT permanently hard-code one model; implement selectable versioned profiles

**Implementation (M5.C.3):**
- Versioned WorkflowDefinitions for each verified image generator (fingerprint-verified)
- ReferenceGenerationService: character description → prompt → selected profile → ComfyUI → image → ReferenceAsset(CANDIDATE)
- Immutable ReferenceGenerationRequest + ReferenceGenerationExecution records
- ComfyUI submission reuses existing ComfyUIAdapter patterns

**Tests:** Deterministic tests with mocked ComfyUI. Live test deferred to M5.H.

- [ ] **Steps: preflight, workflow construction, generation service, tests, commit**

```bash
git commit -m "M5.C: character reference generation with ComfyUI"
```

---

### Task M5.D: Approval + Deterministic Selection

**Goal:** Approval/pinning lifecycle and deterministic ReferenceSelector.

**Files:** Extend `services/reference_service.py`. Create `tests/unit/test_reference_selector.py`.

**Lifecycle operations:**
- approve(reference_id) → status=APPROVED
- reject(reference_id) → status=REJECTED
- archive(reference_id) → status=ARCHIVED
- pin(reference_id) → pinned=True (must also be APPROVED+CURRENT)
- unpin(reference_id) → pinned=False

**ReferenceSelector:**
- Input: shot (with subjects), project characters, all project ReferenceAssets
- Output: ordered list of eligible references for the shot's subjects
- Priority: pinned+approved+current > approved+current > error
- Tie-break: created_at DESC, id ASC
- Scope: project-local, correct character_id, correct kind
- CANDIDATE never selected. STALE never selected. REJECTED/ARCHIVED never selected.
- Missing ref for any subject → ReferenceResolutionError (no silent strategy change)

**Tests:** Selection priority, tie-breaking, candidate exclusion, stale exclusion, missing ref error, pin behavior, multi-character selection.

- [ ] **Steps: TDD, lifecycle, selector, tests, commit**

```bash
git commit -m "M5.D: reference approval and deterministic selection"
```

---

### Task M5.E: H3 Multi-Reference Binding + Workflow v2

**Goal:** Empirically verify H3 multi-picture behavior, evolve H3ReferenceBinding, create r2v_v2 workflow.

**Preflight (M5.E.1 — MANDATORY before workflow mutation):**
1. Start ComfyUI
2. Inspect H3 MiniMaxH3ReferenceToVideo node — it uses COMFY_AUTOGROW_V3 for
   ref_images (min=0, max=9). See M3_PREFLIGHT.md section 6 for verified contract.
   Verify ref_image_1 can be added via `ref_images.ref_image_1` dot notation.
3. Build test workflow with 2 LoadImage nodes (200, 201) wired to ref_images
4. Submit with 2 reference images and appropriate `<Picture 1>` / `<Picture 2>` prompt
5. Verify execution succeeds
6. Verify both references influence output (visual inspection)
7. Document exact JSON representation for 2-ref workflow

**Binding evolution:**
- H3ReferenceBinding gains: reference_asset_id, reference_kind, nullable subject_index/character_id
- H3PromptBuilder validation updated: subject_index no longer == picture_index
- ParameterResolver: inject ref_image_0..ref_image_N from binding list order

**Workflow:**
- Create `workflows/h3/r2v_v2.json` with 2 materialized reference slots
- New WorkflowDefinition with updated mappings and fingerprint
- Existing r2v_v1.json preserved unchanged

**GenerationService:**
- Use workflow version matching reference count (v1 for 1 ref, v2 for 2 refs)
- Or use v2 universally if autogrow omission works (verify in preflight)

**Tests:** Binding construction, prompt with multiple subjects, parameter injection for 2 refs, backward compat with 1-ref case.

- [ ] **Steps: preflight, binding evolution, workflow, prompt/param updates, tests, commit**

```bash
git commit -m "M5.E: multi-reference H3 binding and r2v_v2 workflow"
```

---

### Task M5.F: Reference Staleness

**Goal:** Propagate source freshness when character appearance changes.

**Files:** Extend `services/reference_service.py`, integration with existing stale propagation.

**Rules:**
- Character appearance hash changes → GENERATED refs for that character: source_state=STALE
- USER_UPLOAD refs: NOT auto-staled by WC appearance text changes
- source_fingerprint on ReferenceAsset records the character appearance hash at creation time
- On reimport: compare current appearance hash vs stored source_fingerprint
- Stale refs remain preserved. No auto-archive. No deletion.

**Tests:** Generated ref becomes stale on appearance change, user ref stays current, historical preserved, stale ref excluded from selection.

- [ ] **Steps: TDD, stale propagation, tests, commit**

```bash
git commit -m "M5.F: reference staleness on character appearance changes"
```

---

### Task M5.G: Reference Management API

**Goal:** Backend API endpoints for reference management.

**Files:** Modify `api/routes.py`, `main.py`.

**Routes:**
- `GET /projects/{id}/references` — list all project refs
- `GET /characters/{id}/references` — refs for one character
- `POST /characters/{id}/references/register` — user upload
- `POST /characters/{id}/references/generate` — trigger ComfyUI generation
- `POST /references/{id}/approve` — approve candidate
- `POST /references/{id}/reject` — reject candidate
- `POST /references/{id}/archive` — archive
- `POST /references/{id}/pin` — pin as preferred
- `POST /references/{id}/unpin` — unpin
- `GET /shots/{id}/selected-references` — current selection

**Error mapping:**
- ReferenceIngestError → 422
- ReferenceGenerationError → 502

**Tests:** API tests with mocked service (no live ComfyUI).

- [ ] **Steps: TDD, routes, wiring, error mapping, tests, commit**

```bash
git commit -m "M5.G: reference management API endpoints"
```

---

### Task M5.H: Live Acceptance (two-phase with human checkpoint)

**Goal:** Real character reference generation + real H3 generation using approved reference.

**Project:** `proj_b8b1b8ab40b5` (M4 real project)
**Character:** 陆砚 (`char_df6a4feecb8d`)

**Phase M5.H1 — Generate + Human Approval:**
1. Generate real character reference via ComfyUI (Z-Image Turbo or verified model)
2. ReferenceGenerationRequest persisted with immutable snapshot
3. ReferenceAsset created as CANDIDATE
4. Report exact image path to user
5. **STOP** — mandatory human visual approval checkpoint
6. User says approve or reject
7. If approved → APPROVED + PINNED

**Phase M5.H2 — H3 Generation + Human Influence Check:**
1. Select approved reference for a real canonical shot
2. Bind via evolved H3ReferenceBinding
3. Run real H3 R2V generation using character reference
4. GenerationRequest preserves exact reference SHA snapshot
5. Report video path to user
6. **STOP** — user visually checks whether reference influenced output
7. User confirms influence or reports failure

**Tests:** `@pytest.mark.live` test verifying end-to-end path.

- [ ] **Steps: H1 generation + human checkpoint, H2 H3 generation + human influence check, commit**

```bash
git commit -m "M5.H: live character reference generation and H3 acceptance"
```

---

## M5 Exit Criteria

1. ReferenceKind and ReferenceSource are separate enums
2. ReferenceAsset supports character and storyboard ownership with validated invariants
3. Every managed reference preserves exact content SHA-256, provenance, dimensions, managed path
4. Multiple ReferenceAssets per character supported
5. User upload: validates image, stores managed copy, CANDIDATE, idempotent dedup
6. WC media ingested to managed ReferenceAssets without WC DB writes
7. Generated character refs produced through real ComfyUI execution
8. Immutable ReferenceGenerationRequest preserved; execution lifecycle separate
9. CANDIDATE never automatically production-eligible
10. APPROVED+CURRENT and pinning selection rules deterministic
11. Approval lifecycle independent from source freshness
12. Generated refs become STALE on character appearance fingerprint change; old asset preserved
13. One authoritative selected-reference ordering drives picture indices, prompt, upload, workflow, snapshot
14. H3ReferenceBinding subject_index != picture_index supported (nullable subject_index)
15. Existing M3 r2v_v1 workflow preserved and reproducible
16. New H3 R2V v2 workflow empirically proven with 2 picture inputs (2-reference variant)
17. Storyboard H3 binding claimed only if empirically proven; storyboard asset management may complete without it
18. H3 video GenerationRequest preserves exact reference SHA values
19. Real M4 character produces real ComfyUI-generated reference (live)
20. Generated reference requires user visual approval before production use
21. User-approved character reference used in real H3 generation (live)
22. User visually verifies reference meaningfully influenced video
23. M0-M4 deterministic regressions green (884+)
24. No hidden strategy downgrade in ReferenceSelector
25. No M6 queue/multi-take leakage
26. No M7 continuity/previous-frame leakage

## Task Dependency Audit

| Task | Forward refs? | M6+? | Verdict |
|---|---|---|---|
| M5.A | NO | NO | PASS |
| M5.B | NO | NO | PASS |
| M5.C | NO | NO | PASS |
| M5.D | NO | NO | PASS |
| M5.E | NO | NO | PASS |
| M5.F | NO | NO | PASS |
| M5.G | NO | NO | PASS |
| M5.H | NO | NO | PASS |

**Forward dependencies: ZERO.**

## Backlog (NOT M5 scope)

- MiniMax prompt enhancer (geocine/minimax-video-prompt-enhancer-2.6b-gguf) — future provider-layer evaluation
- OpenRouter/WC Writer quality — future pre-production model improvement
- Scene/style references — future milestone
- Previous-frame continuity references — M7
- AI visual quality reviewer — M8
