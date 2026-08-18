# Development State — Local AI Film Director

**Last Updated:** 2026-08-18

---

## Current State

**Production loop: PROVEN**

**Active branch:** `main`

---

## P3 — Pre-Production UI

**Status:** IN PROGRESS — latest checkpoint merged to main

### Implemented Capabilities

- New Project UI (Wind Comic creation through product path)
- Canonical import (WC → LFDirector project)
- Shot plan editor (add / edit / delete / reorder via API)
- Explicit enrichment only (auto-enrichment removed)
- Project readiness (missing refs disable generation)
- Activity Monitor (operation tracking, duplicate protection)
- Automatic idle model cleanup after expensive stages
- Manual GPU memory release
- System/resource status visibility

### Current P3 Test Project

- **LFDirector:** `proj_b885a403ecac`
- **Wind Comic:** `OOBJ2pik0F5frZWWhaTFe`
- **Scenes:** 1 · **Characters:** 2
- **Shots:** 16 (old auto-enrichment, needs human review)
- **Character reference:** MISSING
- **Environment reference:** MISSING
- **Generation ready:** NO

### Next Action

Open `proj_b885a403ecac` in operator UI → review/correct 16-shot plan → prepare references through UI.

---

## P2 — First Complete Scene

**Status:** COMPLETE / HUMAN PASS / MERGED

- **Project:** `proj_5339656ad20f`
- **Scene:** 5 shots, all approved, 30.825s, 1376x768, 24fps
- **Output:** `storage/exports/proj_5339656ad20f/scene_main_v1.mp4`

---

## M7 — Continuity

**Status:** COMPLETE / CLOSED / MERGED

Selected strategy: H3 R2V image-pack (character + environment + predecessor frame).

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

| Milestone | Key Outcome |
|---|---|
| M1-M6 | Integration, specification, H3 bridge, WC handoff, references, takes/queue |
| M7 | Continuity chain, FLF, image-pack multi-ref |
| P2 | First complete 5-shot scene, operator UI, assembly |

---

## Known Gaps

| Gap | Severity |
|---|---|
| qwen3:14b enrichment quality | PRODUCT_GAP |
| P3 16-shot plan needs correction | PRODUCT_GAP |
| Reference prep not full UI flow | PRODUCT_GAP |
| LTX deferred | DEFERRED |
| CapabilityRegistry frozen | FROZEN |
