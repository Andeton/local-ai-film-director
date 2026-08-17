# ChatGPT Cold-Start Handoff — Local AI Film Director

**Last Updated:** 2026-08-17
**Purpose:** Primary cold-start context for new ChatGPT sessions directing this project.

---

## 1. Project Identity

**Project:** Local AI Film Director

**Pipeline:** user idea → Wind Comic pre-production → canonical production specification → references → provider prompt/workflow → ComfyUI → Takes → review → continuity → assembly/export

**Architecture (ADR-001):** Hybrid Wind Comic Sidecar
- **Wind Comic** = read-only pre-production sidecar (LFDirector reads WC SQLite with `mode=ro`, triggers WC via HTTP SSE, NEVER writes to WC DB)
- **LFDirector** = canonical production/orchestration owner
- **ComfyUI** = generation runtime via REST/WebSocket (NOT MCP for production)
- **MiniMax H3** = current primary video provider, NOT canonical architecture

**Canonical hierarchy:** Project → Sequence → Scene → Beat → Shot → Take

---

## 2. Authority Order

1. Accepted ADRs (ADR-001 through ADR-005)
2. `docs/ARCHITECTURE_V1.md`
3. `docs/ROADMAP_V2.md`
4. `docs/DEVELOPMENT_STATE.md`
5. Current approved milestone implementation plan
6. Empirical accepted milestone/live results
7. Original historical specification

**Critical rule:** Implemented closed interfaces override stale illustrative examples in older documentation.

**GenerationPlan** intentionally does NOT contain `engine_family` or `workflow_profile`. These fields were removed during implementation. Provider/model/workflow selection is a derived provider-layer concern below the canonical layer. Never reintroduce them without a new accepted architecture decision.

---

## 3. Current Checkpoint

| Item | Value |
|---|---|
| Main HEAD | M6 docs checkpoint (see below) |
| M6 feature commit | `3a2fac7` (branch `m6-take-management`) |
| M6 merge commit | `6b6e6e2` |
| Deterministic tests | 1513 passed, 12 live deselected, 0 failed |
| Current milestone | M7 — Continuity (OPEN, M7.G not started) |
| M7 planning branch | `m7-continuity` |
| M7 worktree | `D:\Ai\Local AI Film Director\.worktrees\m7-continuity` |

**M5 Status:** COMPLETE / CLOSED / MERGED at `d4e0fbe`.

**M6 Status:** COMPLETE / CLOSED / MERGED at `6b6e6e2`.
- Plan: `docs/superpowers/plans/2026-08-16-m6-take-management.md`
- Take status (approved/rejected/favorite), persistent queue (QueueBatch idempotency + QueueJob), QueueWorker (atomic claim+finalization, 12-state recovery), TakeService (single-approved CAS), 11 API routes, standalone queue runner.
- Live acceptance: 3 real queued Takes (visual PASS), restart recovery proven, 20-shot/60-job queue proof.
- Chinese-audio observation: H3 inferred Chinese from WC source text (deferred to M10).

**M7 Status:** OPEN — M7.A-E complete, M7.F conditional, M7.G not started.
- Continuity plan: `docs/superpowers/plans/2026-08-17-m7-continuity.md`
- Multi-model plan: `docs/superpowers/plans/2026-08-17-multimodel-conditioning.md`
- Architecture: `docs/MULTIMODEL_CONDITIONING_ARCHITECTURE.md`
- **FLF limitation:** MiniMaxH3ImageToVideo has NO ref_images. Identity relies on predecessor frame + text.
- **H3 R2V remains active.** Only full-video hybrid (124-frame ref_video + audio) is impractical (>120min on RTX 5090).
- M7.A-E: ContinuityState, FLF workflow, ContinuityService, replace-approved+invalidation, API+rebuild+CAS. 193 deterministic tests.
- M7.F: Initial FAIL/PARTIAL → identity fix (IdentityResolver) → A/B preflight → **CONDITIONAL ACCEPTANCE**.
- Human verdict: "условно ок, двигаемся дальше" — text identity helps but is not a universal solution.
- Next H3 recipe: image-only R2V (char ref + env view + predecessor frame, no video, no audio).
- Subtasks: M7.A ✓ → M7.B ✓ → M7.C ✓ → M7.D ✓ → M7.E ✓ → M7.F (conditional) → M7.G (not started).
- Next action: M7.G.A (VisualAssetPack and asset-role definitions).

**Non-blocking hardening observations (deferred to M10):**
1. A QueueJob recovered to succeeded may retain an earlier transient error message; consider clearing or separating historical error state.
2. claim_next() retrieves the claimed row using claimed_at timestamp matching; consider SQLite UPDATE ... RETURNING for stronger identification.
3. Explicit H3 output-language control and native-audio validation.

**Next action:** M7.A implementation only. Do not skip to later subtasks.

---

## 4. Completed Milestones Summary

| Milestone | Key Outcome |
|---|---|
| M0–M0.7 | Discovery, WC validation, architecture freeze, 5 ADRs |
| M1 | Integration Core: FastAPI, WC SQLite adapter, canonical models, provenance, Ollama LLM. 185 tests. |
| M2 | Production Specification: Beat/Shot/Plan enrichment, human editing API, stale propagation. 418 tests. |
| M3 | H3 Bridge: Shot → H3 R2V → ComfyUI → Take. Real R2V execution proved (1376x768, 5.167s). 673 tests. |
| M4 | WC Production Handoff: idea → WC SSE → import → source-fact precedence → enrichment. Real live acceptance. 884 tests. |
| M5 | Reference Management: ingest, versioned generator profiles, lifecycle, H3 multi-ref binding, staleness. Merged at `d4e0fbe`. 1148 tests. |
| M6 | Take Management: persistent queue, QueueWorker, TakeService, 11 API routes, standalone runner. Merged at `6b6e6e2`. 1320 tests. |

**M3 live evidence:** Real H3 R2V generation, human visual acceptance PASS.

**M4 live evidence:**
- WC project: `2NNXzW98y4CXQSVM5D8iY` (WC 12.320.0 + qwen3:14b)
- Canonical project: `proj_b8b1b8ab40b5`
- Main character: 陆砚 (`char_df6a4feecb8d`)
- API: POST /projects/from-idea → HTTP 200 in ~267s
- Known limitation: WC Writer produced empty dialogue + sentinel action/emotion (architecture handles via fallback). Non-empty dialogue roundtrip NOT PROVEN.

---

## 5. M5 Reference Architecture (Frozen)

### Enums

| Enum | Values | Meaning |
|---|---|---|
| ReferenceKind | CHARACTER_FACE, CHARACTER_BODY, STORYBOARD | What production role the image serves |
| ReferenceSource | USER_UPLOAD, WIND_COMIC, GENERATED | Where the asset came from |
| ReferenceStatus | CANDIDATE, APPROVED, REJECTED, ARCHIVED | Review lifecycle (independent from freshness) |
| ReferenceSourceState | CURRENT, STALE | Source freshness (independent from approval) |

### ReferenceAsset

Provider-neutral canonical entity. Ownership: exactly one of (character_id, shot_id) non-None.

**Production eligibility:** `status == APPROVED AND source_state == CURRENT`

Rules:
- CANDIDATE never auto-enters production selection
- Pinned does not override REJECTED/ARCHIVED/STALE
- Generated refs become STALE when source appearance fingerprint changes
- User-uploaded refs are NOT auto-staled by WC text changes
- No automatic deletion/archive of historical references
- No "reapprove to make current" — STALE is permanent until a new ref is generated

### Generation Audit (immutable/mutable separation)

- **ReferenceGenerationRequest:** INSERT-ONLY immutable input snapshot (prompt, model, workflow, seed, params)
- **ReferenceGenerationExecution:** Separate mutable lifecycle (pending → running → succeeded/failed)

### Storage

`storage/references/{project_id}/{reference_asset_id}/original.{ext}`

### Dedup

Scope: project + semantic owner + kind + SHA-256. Repository SQL query (not service list/filter).

---

## 6. M5.C1 Empirical Preflight Results

**ComfyUI:** D:\ComfyUI\TD_1\ComfyUI, v0.33.1, http://127.0.0.1:8188, RTX 5090 (34.2 GB VRAM)

### Z-Image Turbo

| Item | Value |
|---|---|
| Model | z_image_turbo_bf16.safetensors (UNETLoader) |
| Encoder | qwen_3_4b.safetensors (CLIPLoader, type=qwen_image) |
| VAE | ae.safetensors |
| Workflow | UNETLoader → CLIPLoader → CLIPTextEncode(pos/neg) → EmptyLatentImage → KSampler → VAEDecode → SaveImage |
| Settings | 1024x1024, seed 42, 8 steps, euler/simple, cfg 1.5 |
| Elapsed | ~5s |
| Notes | Standard KSampler workflow. CRT auto-download nodes failed; standard loaders work. |

### Krea 2 Turbo

| Item | Value |
|---|---|
| Model | krea2_turbo_fp8_scaled.safetensors (UNETLoader) |
| Encoder | Qwen3-VL-4B-Instruct-abliterated-fp8_scaled.safetensors (CLIPLoader, type=krea2) |
| VAE | qwen_image_vae.safetensors |
| Workflow | UNETLoader → CLIPLoader → Krea2PromptWeight → CLIPTextEncode(neg) → EmptyLatentImage → KSampler → VAEDecode → SaveImage |
| Settings | 1024x1024, seed 42, 8 steps, euler/simple, cfg 1.0 |
| Elapsed | ~25s |
| Notes | Requires Krea2PromptWeight node. Krea2ImageNode self-contained path failed via REST; standard component graph works. |

Both models executed successfully. No ReferenceAsset or ReferenceGenerationRequest was created during preflight.

---

## 7. Model / Workflow Upgrade Policy (Frozen)

- Canonical production data MUST remain model/provider agnostic
- Models are exposed through versioned provider/workflow profiles
- Multiple models may coexist as SUPPORTED
- One profile may be RECOMMENDED/default without disabling alternatives
- ReferenceGenerationRequest preserves the exact selected generator/profile
- Existing workflow versions are NEVER silently overwritten
- Historical GenerationRequests preserve exact model/workflow/fingerprint/prompt/seed/params
- New models are added; old historical definitions retained

**Current image-generator decision:** Both Z-Image Turbo and Krea 2 Turbo remain available/selectable M5 reference-generator candidates. Z-Image is currently the stronger default candidate for CHARACTER_BODY. Krea remains selectable. M5.C implements versioned/selectable profiles, not permanent single-model hard-code.

---

## 8. H3 Reference Binding Invariant

- Picture index is NOT synonymous with Subject index
- M3 one-picture case used `subject_index == picture_index` but M5 evolves this
- `picture_index` = position in one authoritative ordered selected-reference list (1-based)
- `subject_index` = associated ShotSubject index (may repeat, may be None for non-subject refs)
- One ordered list drives: prompt Picture tags, upload order, workflow slots, GenerationRequest reference snapshot
- No independent sorting in later layers
- Multi-picture H3 behavior PROVEN at ComfyUI level — M5.E.1 preflight: 2 references, human visual PASS (see docs/M5_E_PREFLIGHT.md)
- Production integration COMPLETE — GenerationService uses ReferenceSelector → resolve_from_assets → count-based workflow → asset-provenance snapshot
- `workflows/h3/r2v_v1.json` must remain unchanged for M3 reproducibility

---

## 9. Known Backlogs (NOT M5 scope)

| Item | Context | Future |
|---|---|---|
| MiniMax prompt enhancer | geocine/minimax-video-prompt-enhancer-2.6b-gguf tested via LM Studio, strong H3 prompt results | Provider-layer milestone |
| OpenRouter/WC Writer quality | qwen3:14b produced empty dialogue + sentinels | Test stronger model via OpenRouter |
| Scene/style references | SCENE, STYLE, PREVIOUS_FRAME kinds | M7+ |
| Previous-frame continuity | Last frame from approved take | M7 |

---

## 10. Working Protocol

- ChatGPT directs development; user relays commands to Claude Code
- ONE active Claude Code command at a time
- Commands are complete Markdown code blocks in English
- Review Claude's checkpoint before issuing next command
- Do not skip milestones/subtasks or silently begin next task
- TDD for implementation; systematic-debugging only for unexpected failure
- Fresh reviewer/subagent after substantial task
- No speculative provider/model fields in canonical models
- No direct WC SQLite writes
- ComfyUI production = REST/WebSocket, not MCP
- MCP may be used only as development/discovery tooling

---

## 11. Files to Upload for Cold-Start

1. `docs/CHATGPT_HANDOFF.md` (this file — primary context)
2. `docs/DEVELOPMENT_STATE.md` (detailed milestone state)
3. `docs/ROADMAP_V2.md` (strategic roadmap)
4. `docs/ARCHITECTURE_V1.md` (frozen architecture)
