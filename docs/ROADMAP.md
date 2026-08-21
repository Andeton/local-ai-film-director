# Roadmap — Local AI Film Director

**Principle:** Build the working product first. Use ready-made solutions. Write only the minimum glue required. Generalize only after a real workflow proves the need.

---

## Completed

| Milestone | Date | Outcome |
|---|---|---|
| M0 | 2026-08-14 | Discovery: RTX 5090, ComfyUI, H3 models, WC v12.320, architecture frozen |
| M1 | 2026-08-15 | Integration core: FastAPI, WC adapter, canonical models, import pipeline. 185 tests |
| M2 | 2026-08-16 | Production specification: beats, shots, plans, strategy selection, enrichment. 418 tests |
| M3 | 2026-08-16 | H3 bridge: Shot→R2V→ComfyUI→Take. First real generation. 673 tests |
| M4 | 2026-08-16 | WC production handoff: idea→WC→canonical import→enrichment. 884 tests |
| M5 | 2026-08-16 | Reference management: ingest, lifecycle, Z-Image/Krea2 generators, r2v_v2. 1148 tests |
| M6 | 2026-08-17 | Take management: approve/reject, persistent queue, worker recovery. 1320 tests |
| M7 | 2026-08-18 | Continuity: chain state, FLF, image-pack multi-reference. 1716 tests |
| P2 | 2026-08-18 | First complete 5-shot scene: generated, approved, assembled. HUMAN PASS |
| P3 | 2026-08-20 | 6-shot scene completion + durable async generation. 1923 tests. HUMAN PASS |

---

## In Progress

### P4 — Operator Workflow & Prompt Control

**Branch:** `p4-operator-workflow` (1997 tests)

Completed:
- Product specification (docs/PRODUCT_SPEC.md)
- Original idea preservation + legacy degradation
- Reference prompt preview/control + provenance
- P3 story contamination fix
- Shot Production Editor (semantic inputs → compiled H3 prompt)
- Take generation provenance (historical immutable)
- Character data-lineage fix (enrichment ordering, name resolution)
- UX cleanup (Fresh/Outdated badges, Review Prompt buttons, thumbnails, subject chips, field labels, prompt area, idea modal, structured Take details)

Remaining (human acceptance demonstrated):
- G14: Dialogue control — H3 controllability not established
- G17: Side-by-side Take comparison
- G20-G23: Lower-priority polish

---

## Next Priorities

After P4 human acceptance:

1. **Second production project** — validate pipeline generalization beyond P3 project
2. **H3 Prompt Compilation** — LLM-optimized prompts from shot direction
3. **Environment View Packs** — multi-view environment references for varied camera angles

---

## Deferred

| Item | Status |
|---|---|
| LTX-2.3 fallback | DEFERRED — H3 image-pack works |
| Broad model routing | DEFERRED |
| AI reviewer (M8) | DEFERRED |
| Audio/dialogue control | OBSERVATION — H3 controllability not established |
| Modern frontend | DEFERRED |
