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

Full product archaeology and entity capability audit. Accepted 10 product decisions (PD-1 through PD-10) establishing the target product architecture.

#### P4 Phase 3: Location Design (Complete — 2026-08-21)

Location domain design finalized. Three-layer model (Location / Scene Environment State / Shot Environment), multi-Location enrichment as target behavior, per-shot readiness, outdated propagation on Scene Location change. Implementation slices defined.

Full product decisions documented in `docs/PRODUCT_SPEC.md`.

---

## Next Priority: Location Entity Implementation

Location is the highest-priority product-model gap — it blocks multi-scene/multi-location production.

### Location Implementation Slices

| Slice | Scope | Dependency |
|---|---|---|
| ~~**1. Location domain/persistence/repository**~~ | ~~`Location` model, `locations` table, `LocationRepository`, nullable `location_id` FK on `scenes` and `reference_assets`. Unit tests.~~ | **COMPLETE — 2026-08-21. 29 tests. 2026 total passed.** |
| ~~**2. Legacy migration**~~ | ~~Deterministic backfill: one Location per project with env_desc, assign scenes + ENVIRONMENT refs. Idempotent. Historical data immutable.~~ | **COMPLETE — 2026-08-21. 24 tests. 2050 total passed.** |
| ~~**3. Location API + assignment + staleness**~~ | ~~CRUD, Scene assignment with outdated propagation, description edit staleness. 5 endpoints.~~ | **COMPLETE — 2026-08-21. 36 tests. 2086 total passed.** |
| **4. Location-scoped reference management/resolution/readiness** | Location ref generate/upload/preview. `ReferenceSelector.select_location_ref()`. Update `GenerationService` and generation preview to resolve via Scene → Location. Per-shot readiness. | Slice 3 |
| **5. Multi-Location planning/enrichment** | Enrichment identifies distinct physical places from story/scene structure, creates/reuses Location entities, assigns Scenes, derives per-Location descriptions. Single-Location fallback when only one place identified. | Slice 4 |
| **6. Operator UI surfaces** | Location management panel, location ref cards, scene Location selector, generation preview Location labels. Remove project-level environment controls. | Slice 4 |
| **7. Multi-scene/multi-location acceptance production** | End-to-end production with 2+ Locations, validating the complete pipeline. | Slices 5-6 |

### After Location: Remaining Priorities

| Priority | Scope |
|---|---|
| Persistent shot-level generation drafts (PD-4) | Schema + API + UI |
| Storyboard pre-generation review (PD-8) | Storyboard import/generation, ReferenceKind extension |
| H3 Prompt Compilation | LLM-optimized prompts from shot direction |

---

## Deferred

| Item | Status | Decision |
|---|---|---|
| Story/Treatment/Style entities | ACCEPTED (PD-5) — implementation not yet scheduled | LFDirector will own these canonically |
| Prop entity | ACCEPTED — implementation not yet scheduled | Target concept, no current blocker |
| ReferenceKind extension (beyond ENVIRONMENT) | ACCEPTED (PD-7) — after Location | Needed for Prop/Style refs |
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
