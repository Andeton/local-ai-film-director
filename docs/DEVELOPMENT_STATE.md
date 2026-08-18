# Development State — Local AI Film Director

**Last Updated:** 2026-08-18

---

## Current State

**Production loop: PROVEN**

P2 completed the first real end-to-end scene: idea → Wind Comic → canonical shots → references → H3 generation → Takes → approval → continuity → scene assembly → playable MP4 export. Human acceptance: PASS.

**Active branch:** `main`

---

## P2 — First Complete Scene

**Status:** COMPLETE / HUMAN PASS / MERGED

**Project:** `proj_5339656ad20f`  
**Scene:** 5 shots, all approved  
**Output:** `storage/exports/proj_5339656ad20f/scene_main_v1.mp4`  
**Duration:** 30.825s · 1376x768 · 24fps · H264+AAC

**Operator UI:** Minimal production console at `http://127.0.0.1:8000/`
- Project/shot navigation, video playback, approve/reject
- Generation preview with prompt/duration/seed controls
- Continuity visualization, blocked-shot messaging
- Scene assembly with one-click build

**Known P2 Gaps:**

| Gap | Description |
|---|---|
| Enrichment quality | qwen3:14b did not reliably convert scene idea into correct shots. Human correction was required. |
| Shot editing path | Corrected shots were inserted via direct SQLite, not through operator UI. |
| Pre-production UI | No UI for new project creation, WC launch, shot-plan inspection, or reference preparation. |

---

## M7 — Continuity

**Status:** COMPLETE / CLOSED / MERGED at `30aab73`

Selected strategy: H3 R2V image-pack (character + environment + predecessor frame). Human PASS.

---

## Deterministic Baseline

**1743 passed, 12 deselected, 0 failed**

---

## Frozen Workflow Fingerprints

| Workflow | Fingerprint |
|---|---|
| r2v_v1 | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` |
| r2v_v2 | `b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9` |
| flf_v1 | `47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6` |
| r2v_image_pack_v1 | `32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351` |

---

## Closed Milestones

| Milestone | Merged | Key Outcome |
|---|---|---|
| M1 | `main` | FastAPI, WC adapter, canonical models, import pipeline |
| M2 | `main` | Beats, shots, plans, strategy selection, enrichment |
| M3 | `main` | Shot→R2V→ComfyUI→Take vertical slice |
| M4 | `main` | idea→WC SSE→canonical import→enrichment |
| M5 | `main` | Reference lifecycle, ingest, generators, r2v_v2, staleness |
| M6 | `main` | Take approval, persistent queue, worker recovery |
| M7 | `main` | Continuity: chain state, FLF, image-pack multi-ref |
| P2 | `main` | First complete scene: 5 shots, operator UI, assembly, human PASS |

---

## Architecture Decisions (Frozen)

| ADR | Decision |
|---|---|
| ADR-001 | Hybrid Wind Comic Sidecar |
| ADR-002 | Canonical Production Specification |
| ADR-003 | ComfyUI runtime via REST/WebSocket only |
| ADR-004 | ComfyUI MCP as development tool only |
| ADR-005 | Provider-specific artifacts separated from canonical model |

---

## Frozen Infrastructure

- CapabilityRegistry: FROZEN — no expansion
- VisualAssetPack: available, not required by current path
- LTX Ingredients: DEFERRED FALLBACK

---

## Next Product Target

**P3 — Second project entirely through the operator UI**

Acceptance: Tony starts with a new idea and reaches generation-ready shots WITHOUT direct SQLite mutation or Claude manual data creation.

Required new UI capabilities:
- New project creation (Wind Comic launch)
- Imported shot-plan inspection
- Shot editing/correction in UI
- Reference preparation in UI

Then reuse proven: Generate → Review → Approve → Continuity → Assembly
