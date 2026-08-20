# Development State — Local AI Film Director

**Last Updated:** 2026-08-20
**Test Baseline:** 1907 passed, 12 deselected (live tests)
**Branch:** main

---

## Current State

**Production loop: VALIDATED through Shot 4 of 6**

---

## Production Validation Project

**Project:** `proj_cfb89b04f3c8`
**Idea:** "A tense nighttime scene in a small New York apartment..."

| Shot | ID | Status | Take |
|------|----|--------|------|
| 1 | shote6b5120632df | APPROVED | take1177d3c08bbd |
| 2 | shot83bdb22a9c07 | APPROVED | take5e7ed5c36a09 |
| 3 | shote7b54e8ac94f | APPROVED | takeeae22d01305f |
| 4 | shot088711c1c99b | SUCCEEDED | takeaf8e1a965985 |
| 5 | shot84f372d8c579 | NOT GENERATED | — |
| 6 | shot7d12065a94e4 | NOT GENERATED | — |

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
- Generation timeout recovery (finalize_from_result)
- Browser project/shot localStorage persistence

### Shot 4 Observations

- 4/4 image-pack produced a visually good result despite The Woman in Picture 4
- H3 spontaneously produced dialogue audio without explicit control
- Spontaneous dialogue is an observation for future evaluation, not a guaranteed feature or bug

---

## P3 — Pre-Production and Reference Management

**Status:** Production-validated through Shot 4. Scene completion in progress.

### Completed This Session (18 commits)

- Reference Management UI
- Editable character/environment definitions
- Environment reference generation
- OpenRouter provider + ShotPlanner
- Character enrichment
- Environment description derivation
- Enrichment idempotency + explicit replan
- ENVIRONMENT reference kind
- REFERENCE_TO_VIDEO strategy fix
- Real ComfyUI workflow source-reference policy
- H3 image-pack integration into generate_take
- Downstream image-pack continuity
- Subject-scoped preview
- Unused LoadImage slot pruning
- ComfyUI error propagation
- Generation timeout increase + recovery
- Browser localStorage persistence
- Activity event log

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
| No batch generation queue | DEFERRED |
| Single-file HTML operator console | DEFERRED |
| Spontaneous H3 dialogue not controllable | OBSERVATION |
| LTX deferred | DEFERRED |
| CapabilityRegistry frozen | FROZEN |

---

## Closed Milestones

| Milestone | Key Outcome |
|---|---|
| M1-M6 | Integration, specification, H3 bridge, WC handoff, references, takes/queue |
| M7 | Continuity chain, FLF, image-pack multi-ref |
| P2 | First complete 5-shot scene, operator UI, assembly |
