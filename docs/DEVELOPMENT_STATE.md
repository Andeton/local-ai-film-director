# Development State — Local AI Film Director

**Last Updated:** 2026-08-20
**Test Baseline:** 1923 passed, 12 deselected (live tests)
**Branch:** p3-scene-completion

---

## Current State

**Production loop: COMPLETE — 6/6 shots APPROVED, scene assembled, HUMAN PASS**

---

## Production Validation Project

**Project:** `proj_cfb89b04f3c8`
**Idea:** "A tense nighttime scene in a small New York apartment..."
**Assembled scene:** 48.741s, 1376x768, 6 shots. HUMAN PASS.

| Shot | ID | Inputs | Take | Status |
|------|----|--------|------|--------|
| 1 | shote6b5120632df | 2 (char+env) | take1177d3c08bbd | APPROVED |
| 2 | shot83bdb22a9c07 | 3 (char+env+cont) | take5e7ed5c36a09 | APPROVED |
| 3 | shote7b54e8ac94f | 3 (char+env+cont) | takeeae22d01305f | APPROVED |
| 4 | shot088711c1c99b | 4 (char+env+cont+char2) | takeaf8e1a965985 | APPROVED |
| 5 | shot84f372d8c579 | 3 (char+env+cont) | takedda112d68a2f | APPROVED |
| 6 | shot7d12065a94e4 | 4 (char+env+cont+char2) | take418da86f5433 | APPROVED |

**Approved References:**
- ref_cd7e358fc55f — CHARACTER_BODY (The Man)
- ref_44bc8980faf4 — CHARACTER_BODY (The Woman)
- ref_c9fa948003bd — ENVIRONMENT

---

## Validated Production Capabilities

- Wind Comic pre-production and canonical import
- OpenRouter shot planning (5-7 shot concise plans from project description)
- Character enrichment (deficient placeholder characters → production-ready)
- Environment description derivation (narrative → physical set description)
- Editable character name/appearance with staleness propagation
- Editable environment description with staleness propagation
- Z-Image Turbo character body and environment reference generation
- Reference approval/rejection lifecycle
- Generation readiness gate (CHARACTER_BODY + ENVIRONMENT required)
- H3 image-pack generation with up to 4 simultaneous references
- Downstream continuity frame propagation
- Unused image-slot pruning before ComfyUI submission
- Subject-scoped preview/execution consistency
- Take approval and continuity chain
- Durable async generation via persistent queue + embedded worker
- Generation timeout recovery (worker leaves claimed, recovery finalizes)
- Duplicate generation protection (409 on active jobs)
- UI polling with page-refresh discovery of active generation
- Operator overrides (prompt, duration, seed) via queue
- Browser project/shot localStorage persistence
- Scene assembly (6 shots → single MP4)

### Production Observations

- Shots 3, 5, 6: timed out under old 600s timeout, recovered via finalize_from_result
- Shot 6: 73.8 min actual render (4 inputs, 10s video) — anomalously long, cause unknown
- Shots 4, 6: 4/4 image-pack inputs produced visually acceptable two-character scenes
- H3 occasionally produces spontaneous dialogue audio

---

## P3 — Scene Completion

**Status:** COMPLETE — HUMAN PASS

### Implementation (this branch)

**Async durable generation:**
- `POST /shots/{id}/generate` → 202 async enqueue (was synchronous blocking)
- Embedded QueueWorker background thread in FastAPI app
- Timeout leaves job claimed for recovery (not permanently failed)
- Recovery checks ComfyUI history for failed requests with prompt_id (State 12b)
- Queue overrides column for prompt/duration operator overrides
- UI polling, status badges, page-refresh discovery, duplicate protection

**Production shots completed:**
- Shots 4-6 generated, recovered (5 and 6 via finalize_from_result), approved
- Scene assembled and accepted by human operator

---

## Frozen Workflow Fingerprints

| Workflow | Fingerprint |
|---|---|
| r2v_v1 | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` |
| r2v_v2 | `b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9` |
| flf_v1 | `47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6` |
| r2v_image_pack_v1 | `32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351` |

---

## Known Limitations / Technical Debt

| Issue | Severity |
|---|---|
| Windows backslashes in Take video paths | LOW — server normalizes |
| Primary-subject slot ordering open question | OPEN — no demonstrated failure |
| No H3 prompt compilation stage | DESIGN_GAP |
| WC generic placeholder content | KNOWN — project description compensates |
| Legacy FLF fallback remains | LEGACY |
| Single-file HTML operator console | DEFERRED |
| Spontaneous H3 dialogue not controllable | OBSERVATION |
| Shot 6 anomalous 73.8 min render | OBSERVATION — single occurrence |
| LTX deferred | DEFERRED |
| CapabilityRegistry frozen | FROZEN |

---

## Closed Milestones

| Milestone | Key Outcome |
|---|---|
| M1-M6 | Integration, specification, H3 bridge, WC handoff, references, takes/queue |
| M7 | Continuity chain, FLF, image-pack multi-ref |
| P2 | First complete 5-shot scene, operator UI, assembly |
| P3 | 6-shot scene completion, durable async generation, timeout recovery. HUMAN PASS |
