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

### P4 — Operator Workflow, Prompt Control, and Product Architecture

**Branch:** `p4-operator-workflow` (1997 tests)

#### P4 Phase 1: Operator Workflow (Complete)

- Product specification (docs/PRODUCT_SPEC.md)
- Original idea preservation + legacy degradation
- Reference prompt preview/control + provenance
- P3 story contamination fix
- Shot Production Editor (semantic inputs → compiled H3 prompt)
- Take generation provenance (historical immutable)
- Character data-lineage fix (enrichment ordering, name resolution)
- UX cleanup (Fresh/Outdated badges, Review Prompt buttons, thumbnails, subject chips, field labels, prompt area, idea modal, structured Take details)

#### P4 Phase 2: Product-Model Audit (Complete — 2026-08-21)

Full product archaeology and entity capability audit. Accepted 10 product decisions (PD-1 through PD-10) establishing the target product architecture. Key decisions:
- Location becomes first-class canonical concept (PD-1)
- LFDirector owns canonical pre-production artifacts (PD-5)
- Wind Comic is source, not canonical owner (PD-6)
- Storyboard restored as core pre-generation stage (PD-8)
- Prompt editing has persistent + immutable states (PD-4)

Full product decisions documented in `docs/PRODUCT_SPEC.md`.

---

## Next Priority: Product-Model Consolidation

Before the next acceptance production run, consolidate the product model so the operator journey reflects the target architecture. Priority order based on what blocks a multi-scene production:

### 1. Location Entity and Per-Scene Environment

A multi-scene project with different locations cannot currently have different environment references. This blocks the second production project if it involves more than one location.

**Requires:** Domain model, schema, API, Reference Manager updates.

### 2. Persistent Shot-Level Generation Drafts

Prompt/duration/seed overrides are lost on shot switch, forcing the operator to re-enter customizations. This directly impacts production workflow quality.

**Requires:** Schema extension (shot-level draft fields or separate table), API, UI.

### 3. Storyboard Pre-Generation Review

The operator currently goes from shot plan directly to expensive video generation with no visual preview. A storyboard review stage would catch composition/framing issues before committing GPU time.

**Requires:** Storyboard image import/generation, ReferenceKind extension, shot-level storyboard display.

### 4. H3 Prompt Compilation

Shot action text is used directly as H3 prompt. An intermediate LLM step compiling operator-facing shot direction into optimal H3 prompt format would improve generation quality systematically.

### 5. Second Production Project

Validate pipeline generalization with a multi-scene, multi-location production. Should follow Location entity work to properly test per-scene environments.

---

## Deferred

| Item | Status | Decision |
|---|---|---|
| Story/Treatment/Style entities | ACCEPTED (PD-5) — implementation not yet scheduled | LFDirector will own these canonically |
| Prop entity | ACCEPTED — implementation not yet scheduled | Target concept, no current blocker |
| ReferenceKind extension | ACCEPTED (PD-7) — implementation not yet scheduled | Needed for Location/Prop/Style refs |
| AI reviewer (M8) | DEFERRED | Until operator workflow stable |
| LTX-2.3 fallback | DEFERRED | H3 image-pack works |
| Broad model routing | DEFERRED | |
| Audio/dialogue control | OBSERVATION | H3 controllability not established |
| Modern frontend | DEFERRED | |
| Semantic continuity | DEFERRED (PD-10) | Frame-level continuity is baseline |
| Timeline model | FUTURE (PD-10) | Minimal production timeline, not NLE |

---

## Product Decision Log

| ID | Decision | Date |
|----|----------|------|
| PD-1 | Location is first-class canonical concept | 2026-08-21 |
| PD-2 | CharacterReference is conceptually Character (naming debt) | 2026-08-21 |
| PD-3 | Beat remains in canonical hierarchy | 2026-08-21 |
| PD-4 | Prompt editing: persistent draft + immutable snapshot | 2026-08-21 |
| PD-5 | Story/Treatment/Style are canonical LFDirector artifacts | 2026-08-21 |
| PD-6 | Wind Comic is source, not canonical owner | 2026-08-21 |
| PD-7 | ReferenceKind and AssetRole remain separate concepts | 2026-08-21 |
| PD-8 | Storyboard is core pre-generation stage | 2026-08-21 |
| PD-9 | H3 leakage in routes is acknowledged tech debt | 2026-08-21 |
| PD-10 | Timeline future minimal; semantic continuity deferred | 2026-08-21 |
