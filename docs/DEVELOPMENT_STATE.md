# Development State — Local AI Film Director

**Last Updated:** 2026-08-17

---

## Current Milestone

**M7 — Continuity**

**Status:** M7.B COMPLETE / M7.C NOT STARTED

Plan: `docs/superpowers/plans/2026-08-17-m7-continuity.md`
Branch: `m7-continuity`
Worktree: `D:\Ai\Local AI Film Director\.worktrees\m7-continuity`
Base: main at `18c6405`

**M7.A — Continuity Models, Persistence, Ordering, and Fingerprinting: COMPLETE**
- ContinuityState model, continuity_states table, ContinuityStateRepository, ContinuityResolver
- Nullable continuity_snapshot on GenerationRequest, compute_continuity_fingerprint, ContinuityError
- M7.A review: 7 PASS, 1 WARN (fixed), 0 FAIL

**M7.B — Workflow Technical Preflight and Versioned Continuity Bindings: COMPLETE**
- Technical preflight: HUMAN PASS (prompt `e8f2ba02-1127-4217-8a7d-eb889cbdaf2c`)
- FLF does NOT support simultaneous character references (MiniMaxH3ImageToVideo has no ref_images input)
- Downstream character identity relies on the predecessor frame propagated through first_frame
- Workflow: `h3_flf_v1` v1.0.0, fingerprint `47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6`
- UNET: `minimax_h3_fl2va_pruned_int8_convrot.safetensors` (different from ref2va)
- Audio finding: fl2va produces joint video+audio latents; audio_vae needed only at decoder stage
- ContinuityBinding frozen dataclass with verified upstream provenance
- build_continuity_injections for FLF parameter injection (prompt, first_frame, duration, seed, aspect, output)
- resolve_for_continuity selection: continuity frame → FLF, no continuity → R2V v1/v2
- r2v_v1/v2 fingerprints verified unchanged
- M7.B review: 9 PASS, 0 WARN, 0 FAIL
- Baseline: 1413 passed, 12 deselected, 0 failed (1380 + 33 new)

**Subtasks:** M7.A ✓ → M7.B ✓ → M7.C → M7.D → M7.E → M7.F

Next action: M7.C implementation only.

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
| M5 | 2026-08-16 | Reference Management: ReferenceAsset lifecycle, ingest (user upload + WC HTTP), versioned generator profiles (Z-Image Turbo + Krea 2 Turbo), lifecycle service, H3 multi-reference binding + r2v_v2 workflow, staleness propagation, 10 API routes. Live acceptance: human reject+approve cycle, real H3 generation with reference influence PASS. 1148 deterministic + 9 live deselected. Merged to main at `d4e0fbe`. |
| M6 | 2026-08-17 | Take Management: Take status (approved/rejected/favorite), persistent queue (QueueBatch idempotency + QueueJob), QueueWorker (atomic claim+finalization, 12-state recovery, prompt-ID resume), TakeService (single-approved CAS), 11 API routes, standalone queue runner. Live acceptance: 3 real queued Takes (visual PASS), restart recovery proven, 20-shot/60-job queue proof. Feature `3a2fac7`, merged to main at `6b6e6e2`. 1320 deterministic + 12 live deselected. |

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

## Last Closed Milestone

**M6 — Take Management**

**Status:** COMPLETE / CLOSED / MERGED

Feature branch: `m6-take-management` (final commit `3a2fac7`)
Merge commit: `6b6e6e2` (merged to main 2026-08-17)
Plan: `docs/superpowers/plans/2026-08-16-m6-take-management.md`

**Baseline:** 1320 deterministic + 12 live deselected (9 M1-M5 + 3 M6), 0 failed

Next: M7 — Continuity. PLANNING COMPLETE. Next action is M7.A implementation only.

**Non-blocking hardening observations (deferred to M10):**
1. A QueueJob recovered to succeeded may retain an earlier transient error message; consider clearing or separating historical error state.
2. claim_next() retrieves the claimed row using claimed_at timestamp matching; consider SQLite UPDATE ... RETURNING for stronger identification.
3. Explicit H3 output-language control and native-audio validation.

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
