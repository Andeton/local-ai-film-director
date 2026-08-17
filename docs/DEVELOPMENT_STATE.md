# Development State — Local AI Film Director

**Last Updated:** 2026-08-16

---

## Current Milestone

**M5 — Reference Management**

**Status:** COMPLETE / CLOSED / MERGED

Branch: `m5-reference-management` (merged to main at `d4e0fbe`)
Plan: `docs/superpowers/plans/2026-08-16-m5-reference-management.md`

**M5 Progress:**
- M5.A: COMPLETE — ReferenceAsset (kind/source/ownership/lifecycle), ReferenceGenerationRequest (immutable), ReferenceGenerationExecution (mutable), 3 DB tables + indexes, 3 repositories
- M5.B: COMPLETE — ReferenceIngestService (user upload + WC media HTTP/local), PIL image validation (PNG/JPEG/WEBP), SHA-256 content identity, managed storage with pathlib confinement, dedicated SQL dedup query, structured IngestOutcome, 50MB download limit
- M5.C: COMPLETE — ReferenceGenerationService with versioned/selectable generator profiles (Z-Image Turbo v1 + Krea 2 Turbo v1), immutable ReferenceGenerationRequest snapshot, mutable execution lifecycle, ComfyUI submit/monitor/get_result/download, managed storage + SHA + dimensions on output CANDIDATE asset
- M5.D: COMPLETE — ReferenceLifecycleService (approve/reject/archive/pin/unpin with invariant enforcement), ReferenceSelector (deterministic provider-neutral selection: pinned+approved+current > approved+current, created_at DESC, id ASC tie-break)
- M5.E: COMPLETE — H3 multi-reference binding evolution + r2v_v2 workflow + production integration
  - H3ReferenceBinding evolution (nullable subject_index, reference_asset_id/kind)
  - r2v_v2 workflow (fingerprint-verified, 2 materialized LoadImage slots)
  - WorkflowResolver.resolve_for_reference_count (v1 for 1 ref, v2 for 2)
  - H3ReferenceResolver.resolve_from_assets() — builds bindings from ReferenceSelector output with managed-path confinement + SHA re-verification
  - GenerationService wired: ReferenceSelector → resolve_from_assets → count-based workflow → asset-provenance reference_snapshot
  - GenerationRequest.reference_snapshot includes reference_asset_id + reference_kind
  - PromptBuilder validation, M5.E.1 preflight (human visual PASS)
  - Unit tests (resolve_from_assets: order, SHA mismatch, missing file, character mismatch, count mismatch, path escape rejection, confinement acceptance)
  - Integration tests (two-asset: v2 workflow selected, node 200/201 image injection, asset provenance in snapshot, both subjects in prompt, rejected/stale ref errors)
- M5.F: COMPLETE — Reference staleness on character appearance changes
  - Shared compute_appearance_fingerprint helper (models/reference.py, used by both generation and stale propagation)
  - ReferenceAssetRepository.mark_generated_stale_for_character (scoped SQL: project+character+GENERATED+CURRENT, NULL fingerprint → STALE)
  - Atomic integration in import_project: character UPSERT + stale transition in same DB transaction
  - Rollback on failure: stale propagation error rolls back character appearance update
  - GENERATED-only: USER_UPLOAD and WIND_COMIC untouched
  - No reactivation: already-STALE remains STALE, revert appearance does not re-CURRENT
  - Status, pinned, SHA, path, provenance, dimensions, created_at preserved
  - 16 unit tests + 8 integration tests
- M5.G: COMPLETE — Reference management API endpoints
  - 10 routes: project/character listing, multipart upload/register, synchronous generate, approve/reject/archive/pin/unpin, shot selected-references
  - Multipart upload: 50MB byte-bounded streaming, temp file cleanup on success+failure, client filename never controls path
  - ReferenceNotFoundError(404) vs ReferenceLifecycleError(409) typed distinction
  - ReferenceIngestError→422, ReferenceGenerationError→502, oversized→413
  - Selected references: ReferenceSelector with default CHARACTER_BODY, missing eligible→409
  - Service wiring: ReferenceIngestService, ReferenceGenerationService, ReferenceLifecycleService, ReferenceSelector
  - 34 API tests (listing, upload, dedup, generation, lifecycle, selection, routing, error mapping)
- M5.H: COMPLETE — Live acceptance with human checkpoints
  - H1: First candidate REJECTED (East Asian features), prompt-override correction (commit 88b4a9a), replacement candidate APPROVED + PINNED
  - H2: Real H3 R2V generation using approved reference, human reference-influence PASS
  - Path confinement fix for relative managed_path (commit 958d826)
  - Quality limitations documented (1024x1024 facial detail, 1376x768 video resolution)
  - 2 @pytest.mark.live tests (evidence verification, no re-generation)

**Baseline:** 1148 deterministic + 9 live deselected (7 M1-M4 + 2 M5), 0 failed

Backlog (NOT M5): MiniMax prompt enhancer, OpenRouter/WC Writer quality

---

## Completed Milestones

| Milestone | Date | Key Outcome |
|---|---|---|
| M0 | 2026-08-14 | Technical discovery: RTX 5090, ComfyUI 0.32.0 verified, H3 models installed, R2V prompt format discovered, upstream projects researched |
| M0.3 | 2026-08-14 | ComfyUI MCP assessed: 40+ tools, all H3 nodes visible, development-only role confirmed |
| M0.4 | 2026-08-14 | Wind Comic v12.320 validated locally: runs on port 3000, Ollama connects (gemma4 fails strict JSON), 10 duplication warnings with M1-M8, full artifact inspection |
| M0.5 | 2026-08-14 | Architecture frozen: hybrid sidecar, 4 ADRs, canonical data model, 10-milestone roadmap, first vertical slice defined |
| M0.6 | 2026-08-14 | Model-agnostic boundary correction: H3 fields removed from Shot/CharacterReference, GenerationPlan + H3PromptV1 as separate artifacts, ADR-005 |
| M0.7 | 2026-08-14 | Documentation consistency: ADR header fixed, M2 uses generic strategy names, ADR-002 clarifies GenerationPlan is model-agnostic, terminology verified across all docs |
| M1 | 2026-08-15 | Integration Core: Python 3.14.3, FastAPI scaffold, WC SQLite read-only adapter (real WC schema verified), canonical Project/Sequence/Scene/CharacterReference with provenance, atomic import with change detection (added/modified/deleted), SQLite persistence with UPSERT/UNIQUE/FK, Ollama LLM provider (gemma4:e4b structured JSON verified), API with 11 endpoints. 185 tests (182 deterministic + 3 live Ollama). |
| M2 | 2026-08-16 | Production Specification: Beat/ShotSpecificationV1/GenerationPlan canonical models (model-agnostic, zero provider fields), BeatEnricher + CoveragePlanner (LLM object-wrapper contract, domain repair), deterministic ShotSpecBuilder (non-lossy character refs), deterministic StrategySelector (explicit context, 5-priority precedence), history-preserving re-enrichment (OUTDATED + new IDs, never delete), human editing API with stale propagation + force protection (409), atomic M1+M2 source-change cascade, 23 API endpoints total. 418 tests (413 deterministic + 5 live Ollama). Exit criteria 12/12 PASS. Backlog: _find_project_id_for_scene O(N) scan (MINOR). |
| M3 | 2026-08-16 | H3 Bridge Vertical Slice: Shot → H3 R2V → ComfyUI → Take. H3ReferenceResolver (content SHA-256, first-ref-only), H3PromptBuilder (deterministic, binding-authority), WorkflowRegistry (fingerprint-verified r2v_v1.json), ParameterResolver (seconds injection, trained 124-362 frame range, explicit aspect mapping), ComfyUIAdapter (sync REST+WS, prompt_id filtering), INSERT-only GenerationRequest + UNIQUE Take repositories with atomic finalization, GenerationService (22-step pipeline with pre/post-request failure boundary), media staging (ffprobe + true last-frame extraction), API (POST generate, GET request, GET comfyui health). Real H3 R2V execution: 1376x768 h264+aac 5.167s in 3:51 on RTX 5090. Human visual acceptance PASS. 673 deterministic + 1 live ComfyUI + 5 live Ollama tests. Exit criteria 15/15 PASS. |
| M4 | 2026-08-16 | Wind Comic Production Handoff: idea → WC SSE → canonical import → source-aware enrichment. WindComicPreproductionClient (JWT+SSE), ShotSourceFacts (frozen transport DTO), StoryboardParser (conservative regex), DialogueIntent (speaker resolution), deterministic source-fact precedence in ShotSpecBuilder (sentinel handling, partial camera merge), PreproductionService (synchronous orchestrator), reimport stale propagation (director+storyboard in hash), POST /projects/from-idea API. Real live acceptance: WC 12.320.0 + qwen3:14b, project 2NNXzW98y4CXQSVM5D8iY → proj_b8b1b8ab40b5 (1 scene, 2 chars, 18 shots, 18 plans). 884 deterministic + 7 live deselected. Exit criteria 13/13 PASS. Known limitation: WC Writer dialogue/action quality with qwen3:14b not production-ready. |

---

## Architecture Decisions

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Hybrid Wind Comic Sidecar Architecture | Accepted |
| ADR-002 | Canonical Production Specification independent of Wind Comic | Accepted |
| ADR-003 | ComfyUI runtime via REST/WebSocket API only | Accepted |
| ADR-004 | ComfyUI MCP as development tool only | Accepted |
| ADR-005 | Provider-specific generation artifacts separated from canonical model | Accepted |

---

## Known Blockers

| Blocker | Severity | Impact | Mitigation |
|---|---|---|---|
| ~~No 14B+ LLM model installed locally~~ | ~~HIGH~~ | ~~Enrichment agents need reliable structured output~~ | RESOLVED: qwen3:14b installed, works for both WC and LFDirector enrichment |
| No T2V/I2V API-format workflow template | LOW | Only R2V is API-ready; T2V/I2V need construction from native nodes | Use MCP to construct in future milestone |
| No H3 Turbo LoRA installed | LOW | Generation will be slower (20 steps vs 6-10) | Download when needed |
| Wind Comic requires budget hack for demo user | LOW | Budget cap blocks project creation | Set budget_hard_cap_cny to 99999 |

---

## Deferred Items

| Item | Reason | When |
|---|---|---|
| Wind Comic fork/modification | Sidecar architecture — don't modify WC | Revisit if WC becomes blocking |
| Blender/ControlNet spatial continuity | Not MVP | Post-M10 |
| Distributed GPU rendering | Not MVP | Post-M10 |
| Multi-engine routing (WAN, LTX, etc.) | H3-only for MVP | Post-M10 |
| Mobile / cloud deployment | Not MVP | Post-M10 |
| Automatic rejection (AI auto-deletes) | Human review required in MVP | Post-M10 |
| Full Wind Comic v1 API | Requires API_KEYS env var; SQLite read is simpler | Revisit if multi-machine setup needed |

---

## Next Approved Action

**M6 — Take Management**

**Status:** PLANNING COMPLETE, IMPLEMENTATION NOT STARTED

Branch: `m6-take-management`
Worktree: `D:\Ai\Local AI Film Director\.worktrees\m6-take-management`
Plan: `docs/superpowers/plans/2026-08-16-m6-take-management.md`

**M6 Progress:**
- M6.A: COMPLETE — Take status evolution (approved/rejected), is_favorite boolean field, derive_take_seed (SHA-256, masked to signed-63-bit [0, 2^63-1]), QueueJob model, generation_queue table + indexes + UNIQUE(shot_id, take_number), is_favorite migration for existing takes, 40 tests
- M6.B: COMPLETE — Persistent queue batch idempotency + enqueue + cancel
  - QueueBatch: persistent idempotency record with UNIQUE(project_id, idempotency_key) + request_fingerprint (SHA-256 of canonical JSON)
  - QueueJob.batch_id: links every job to its batch for status-independent retry
  - Same key + same payload → returns original jobs (regardless of job status)
  - Same key + different payload → QueueConflictError
  - New key → creates new batch with new take numbers
  - Seed persistence: base_seed + derived seed immutable at enqueue time
  - QueueJobRepository + QueueBatchRepository
  - QueueService.enqueue_shot/scene: atomic batch creation, deterministic seeds
  - cancel_job: pending→cancelled (idempotent), claimed/succeeded/failed rejected
  - Scene retry returns original jobs (not empty list)
  - Error classes: QueueJobNotFoundError, QueueValidationError, QueueConflictError, QueueTransitionError
  - 32 integration tests (idempotency, conflict, scene retry, cancellation, repository)
- M6.C: COMPLETE — QueueWorker + atomic finalization + 12-state recovery + concurrency
  - Atomic claim: UPDATE with subquery, WAL-serialized, attempt_count incremented once
  - GenerationService.generate_take + _finalize_callback for atomic QueueJob update
  - Atomic finalization: Take + request succeeded + QueueJob succeeded in single transaction
  - False-success prevention: request succeeded + no Take/missing media → invariant failure
  - Prompt-ID recovery: never calls submit() during recovery
  - Recovery matrix: 12 states covering all (claimed, request_status, Take_exists, media_exists) combinations
  - Prompt-ID resume: check_prompt_status → queued/running=leave claimed, succeeded=finalize_from_result, failed/unknown=mark failed
  - GenerationService.finalize_from_result: shared download→validate→stage→finalize path for recovery (no submit)
  - ComfyUIAdapter.check_prompt_status: non-blocking prompt state resolution
  - Status-check exceptions leave job claimed (never false-fail a completed prompt)
  - run_available(): bounded ThreadPoolExecutor, recovery before first claim, stop() prevents new claims
  - Concurrency: 1–4 (validated), default 1
  - max_attempts=1: no retry in M6
  - 32 integration tests (claim, execution, failure, recovery false-success, prompt-ID resume, concurrency, stop)
- M6.D: COMPLETE — TakeService approve/reject/favorite/unfavorite
  - Transition matrix: succeeded→approved/rejected (terminal), is_favorite orthogonal
  - Single-approved invariant: partial unique index + service-level CAS check
  - TakeRepository: update_review_status (CAS), update_favorite, get_approved_for_shot, count_approved_for_shot
  - Media validation: video file existence + path confinement before approval
  - TakeNotFoundError, TakeLifecycleError, TakeConflictError
  - 31 unit tests (transitions, single-approved, favorites, media, immutability, migration)
- M6.E: COMPLETE — Take management and queue API endpoints
  - 11 routes: shot/scene enqueue, queue listing/get/cancel, take listing/approved, approve/reject/favorite/unfavorite
  - Idempotency-Key HTTP header required for enqueue (validated 1-128 chars)
  - HTTP 202 for new batches, 409 for conflicts, 422 for validation
  - Error mappings: QueueJobNotFoundError→404, QueueValidationError→422, QueueConflictError→409, QueueTransitionError→409, TakeNotFoundError→404, TakeLifecycleError→409, TakeConflictError→409
  - Service wiring: QueueService, TakeService, QueueJobRepository, QueueBatchRepository in main.py
  - Standalone queue worker: `python -m film_director.queue_runner` (separate process)
  - Worker factory: build_worker(settings) constructs production QueueWorker from shared config
  - Single-instance lock: OS file lock under data dir, auto-releases on crash
  - Recovery before first claim, controlled polling, CTRL+C shutdown
  - Settings: queue_worker_concurrency (1-4), queue_worker_poll_interval_seconds (1-300), queue_worker_enabled
  - API 404 correction: missing shot/scene → 404 (not 422)
  - 37 API+runner integration tests
- M6.F: NOT STARTED

**Baseline:** 1320 deterministic + 9 live deselected, 0 failed

Next: M6.F — Live acceptance: 3 takes + queue proof + human approval.

---

## Previous Milestones

**M4 — Wind Comic Production Handoff (CLOSED)**

Branch: `m4-wc-handoff`
Worktree: `D:\Ai\Local AI Film Director\.worktrees\m4-wc-handoff`
Plan: `docs/superpowers/plans/2026-08-15-m4-wind-comic-production-handoff.md`

**M4 Progress:**
- M4.A: COMPLETE — WindComicPreproductionClient (SSE + JWT auth, typed events, error taxonomy)
- M4.B: COMPLETE — DialogueIntent, ShotSourceFacts (frozen transport), StoryboardParser (conservative regex), ProductionProject.director_context + DB migration
- M4.C: COMPLETE — WCScriptShot/WCDirectorPlan DTOs, extended WCProjectBundle (script+storyboard+plan), shotNumber correlation, build_shot_source_facts, director_context import, source hash includes script_data
- M4.D: COMPLETE — Deterministic source-fact precedence in ShotSpecBuilder, EnrichmentService recomputation path, sentinel handling, partial camera merge, dialogue speaker resolution, project-scoped subject resolution
- M4.E: COMPLETE — PreproductionService synchronous orchestrator (idea → WC SSE → persisted validation → import → enrich), artifact validation, error propagation
- M4.F: COMPLETE — Reimport stale propagation: project source hash extended with director_plan + storyboard data, script/storyboard/director changes trigger project-level stale cascade, human edits + historical requests/takes preserved
- M4.G: COMPLETE — POST /projects/from-idea synchronous API, PreproductionService wiring, WindComicPreproductionError→502 mapping, request/response DTOs
- M4.H: COMPLETE — Live acceptance: real idea → WC qwen3:14b pipeline → canonical import with source-fact precedence, 1 live test

**M4 Status:** CLOSED — 13/13 exit criteria PASS

**Baseline:** 884 deterministic + 7 live deselected, 0 failed

**M4.H Live Evidence:**
- WC project: `2NNXzW98y4CXQSVM5D8iY` (qwen3:14b, WC 12.320.0)
- Canonical project: `proj_b8b1b8ab40b5`
- API: POST /projects/from-idea → HTTP 200 in ~267s
- Counts: 1 scene, 2 characters, 6 script shots, 6 storyboard shots, 3 beats, 18 canonical shots, 18 plans
- Source precedence: storyboard duration (10.0s), camera angle, lighting all applied from source; sentinels (動作/情緒) correctly fell back to LLM
- Exit criteria: 13/13 PASS

**M4.H Known Limitations (not architecture blockers — WC Writer quality):**
- WC Writer (qwen3:14b) did NOT generate the explicitly requested detective dialogue — all dialogue fields empty
- Script action/emotion remained WC template sentinels (動作/情緒) in all 6 shots — M4.D correctly treated as non-meaningful and used LLM fallback
- WC Writer mixed movement/lighting text into `camera angle:` field — StoryboardParser field boundary is correct (stops at `, lighting:` marker), but extracted camera_angle contains movement/lighting text because WC Writer put it there
- Dialogue preservation mechanism PASS (empty source preserved as empty); non-empty dialogue live round-trip NOT EXERCISED
- M4 handoff architecture verified; meaningful WC Writer dialogue/action quality with qwen3:14b is NOT proven
- Future improvement: evaluate WC with higher-quality models or prompt engineering for richer structured output

**M3 Closure Evidence:**
- Branch: `m3-h3-bridge`
- Final implementation HEAD: `dfd8994`
- Live GenerationRequest: `greq31767e233ca3`
- Live Take: `takee024e17d050a`
- ComfyUI prompt: `d4e450d0-8ed1-48b4-8d57-5a2335065e80`
- Video: `storage/takes/proj-live/shot-live/take_1/96efcf92_00001_.mp4`
- Human visual acceptance: PASS
- Exit criteria: 15/15 PASS
- Deterministic tests: 673 passed, 6 live deselected, 0 failed

**M3 Known Limitations (not blockers — deferred to later milestones):**
- Only REFERENCE_TO_VIDEO execution supported
- Checked-in template materializes 1 reference slot (provider supports 9)
- Generation API is synchronous (no queue/background worker)
- One Take per execution (no multi-take)
- No continuity chain
- No M5 reference ranking
- No multi-take selection/review
- Live acceptance used project fixture (strict WC storyboard traceability exercised later)

**M3 Verified Runtime Contract:**
- ComfyUI 0.33.1, H3 R2V template `workflows/h3/r2v_v1.json`
- Template SHA-256: `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4`
- Prompt: node 104, Reference: node 200 (1 materialized / 9 provider), Duration: node 111 (seconds, trained 124-362 frames), Seed: node 15, Aspect: node 115, Output: node 92
- Media: MP4/H264 + muxed AAC, 24fps, 1376x768 (16:9)

**Authoritative docs:** `docs/superpowers/plans/2026-08-14-m3-h3-bridge.md`, `docs/M3_PREFLIGHT.md`

---

## Key File Locations

| File | Purpose |
|---|---|
| `Техническое задание и roadmap_...md` | Original specification (historical, do not modify) |
| `docs/M0_DISCOVERY.md` | M0 technical discovery report |
| `docs/M0_COMPONENT_MATRIX.md` | Component matrix |
| `docs/M0_OPEN_QUESTIONS.md` | Open questions + phase/milestone mapping |
| `docs/M0_3_COMFYUI_MCP_ASSESSMENT.md` | MCP tooling assessment |
| `docs/M0_4_WIND_COMIC_VALIDATION.md` | Wind Comic local validation |
| `docs/M0_4_WIND_TO_LOCAL_FILM_DIRECTOR_MAPPING.md` | WC → our spec field mapping |
| `docs/ARCHITECTURE_V1.md` | Frozen architecture (V1) |
| `docs/ROADMAP_V2.md` | Rebased roadmap |
| `docs/DEVELOPMENT_STATE.md` | This file |
| `docs/architecture/ADR-001-*.md` | Hybrid sidecar decision |
| `docs/architecture/ADR-002-*.md` | Canonical data model decision |
| `docs/architecture/ADR-003-*.md` | ComfyUI runtime boundary |
| `docs/architecture/ADR-004-*.md` | MCP development boundary |
| `docs/architecture/ADR-005-*.md` | Provider-specific generation artifacts |
| `experiments/wind-comic/` | Wind Comic clone (isolated, do not modify) |
| `src/film_director/` | M1 production source (FastAPI backend) |
| `src/film_director/config.py` | Application configuration (pydantic-settings) |
| `src/film_director/main.py` | FastAPI application factory |
| `src/film_director/adapters/wind_comic.py` | Wind Comic SQLite read-only adapter |
| `src/film_director/models/canonical.py` | Canonical production models |
| `src/film_director/models/provenance.py` | Provenance tracking + source hash |
| `src/film_director/persistence/` | Our SQLite persistence layer |
| `src/film_director/services/import_service.py` | WC import pipeline + change detection |
| `src/film_director/llm/` | LLM provider abstraction (Ollama) |
| `src/film_director/api/routes.py` | API route definitions |
| `src/film_director/enrichment/` | M2 enrichment layer (BeatEnricher, CoveragePlanner, ShotSpecBuilder, StrategySelector, StalePropagator) |
| `src/film_director/services/enrichment_service.py` | M2 enrichment orchestrator + atomic M1/M2 change cascade |
| `src/film_director/generation/` | M3 H3 provider layer (types, resolver, prompt, workflow, parameters, adapter, service, media) |
| `workflows/h3/r2v_v1.json` | Verified H3 R2V API workflow template |
| `docs/M3_PREFLIGHT.md` | M3.A runtime preflight evidence + frozen implementation facts |
| `tests/` | Test suite (unit + integration + live) |
