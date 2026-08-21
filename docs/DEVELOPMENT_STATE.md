# Development State — Local AI Film Director

**Last Updated:** 2026-08-20
**Test Baseline:** 1997 passed, 1 skipped, 12 deselected (live tests)
**Branch:** p4-operator-workflow (P3 merged to main at 0ea4bce)

---

## Current State

**P3 production: COMPLETE — 6/6 shots APPROVED, scene assembled, HUMAN PASS**
**P4 operator workflow: IN PROGRESS on p4-operator-workflow**

---

## Production Validation Project

**Project:** `proj_cfb89b04f3c8`
**Assembled scene:** 48.741s, 1376x768, 6 shots. HUMAN PASS.

| Shot | Take | Status |
|------|------|--------|
| 1 | take1177d3c08bbd | APPROVED |
| 2 | take5e7ed5c36a09 | APPROVED |
| 3 | takeeae22d01305f | APPROVED |
| 4 | takeaf8e1a965985 | APPROVED |
| 5 | takedda112d68a2f | APPROVED |
| 6 | take418da86f5433 | APPROVED |

---

## P4 — Operator Workflow & Prompt Control (In Progress)

### Completed on p4-operator-workflow

- PRODUCT_SPEC.md: full operator-journey audit, 23 gaps identified, 13+ resolved
- Original idea preservation (`director_context.original_idea`)
- Reference prompt preview/control (character + environment, with overrides)
- Reference prompt provenance on generated cards
- P3 story contamination removed from environment negative prompt
- Shot Production Editor (unified semantic inputs → compiled H3 prompt)
- Take generation provenance (historical immutable details per Take)
- Character data-lineage fix (enrichment ordering, canonical name resolution)
- UX cleanup: Fresh/Outdated badges, Review Prompt buttons, larger thumbnails, individual subject removal, improved field labels, larger prompt area, idea modal, structured Take details

### Demonstrated Gaps Remaining

See docs/PRODUCT_SPEC.md for the complete registry. Key remaining:
- G14: Intentional dialogue control
- G17: Side-by-side Take comparison
- G20: Ephemeral prompt override state
- G21-G23: Lower-priority polish

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
| P3 | 6-shot scene completion, durable async generation, timeout recovery. HUMAN PASS |
