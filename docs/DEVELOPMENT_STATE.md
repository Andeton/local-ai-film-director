# Development State — Local AI Film Director

**Last Updated:** 2026-08-21 (session close — UI acceptance FAILED, UX audit complete)
**Test Baseline:** 2168 passed, 1 skipped, 12 deselected (live tests)
**Branch:** p4-operator-workflow (P3 merged to main at 0ea4bce)

---

## Current State

**P3 production: COMPLETE — 6/6 shots APPROVED, scene assembled, HUMAN PASS**
**P4: Phase 1 (operator workflow) COMPLETE, Phase 2 (product-model audit) COMPLETE, Phase 3 (Location design) COMPLETE**

---

## Product-Model Audit (2026-08-21)

Full product archaeology and entity capability audit completed. Reconciled ORIGINAL_SPEC.md against current implementation. Accepted 10 product decisions (PD-1 through PD-10) establishing the target product architecture.

### Key Findings

**Current implementation vs target architecture:**

| Concept | Current Status | Target Status | Blocker? |
|---|---|---|---|
| Original Idea | EXISTS (P4) | Complete | — |
| Story | Fragment in `director_context.description` | Canonical entity (PD-5) | No immediate blocker |
| Director Treatment | Imported, never consumed | Canonical entity (PD-5) | No immediate blocker |
| Style Bible | MISSING | Canonical entity (PD-5) | No immediate blocker |
| Character | EXISTS as `CharacterReference` | Conceptually Character (PD-2, naming debt) | — |
| Location | Slices 1-6 COMPLETE — model/persistence/migration/API/refs/generation/readiness/enrichment/UI | First-class entity (PD-1). **UI acceptance FAILED** — information architecture rejected. UX audit completed. UX-A (shell + nav) designed but NOT implemented. | **Needs UX restructuring** |
| Prop | MISSING | Future entity | No immediate blocker |
| Scene/Beat | EXISTS (Beat invisible in UI) | Beat as lightweight grouping (PD-3) | — |
| Storyboard | Schema exists, never populated | Core pre-generation stage (PD-8) | No immediate blocker |
| Prompt drafts | Ephemeral JS state | Persistent per-shot (PD-4) | Impacts workflow quality |
| ReferenceKind | 4 values | Extended scope (PD-7) | Blocks Location/Prop/Style refs |
| Timeline | MISSING | Future minimal (PD-10) | Not blocking |
| Semantic continuity | MISSING | Deferred (PD-10) | Not blocking |

### Implementation Naming Debt

| Code Name | Product Name | Location | Notes |
|---|---|---|---|
| `CharacterReference` | Character | `canonical.py`, `character_references` table | PD-2: code/schema rename deferred |
| `director_context` dict | Story + Treatment + Style fragments | `production_projects.director_context` JSON | PD-5: will become canonical entities |
| `environment_description` | Location description | `director_context.environment_description` | PD-1: will move to Location entity |

### H3 Provider Leakage (Acknowledged Debt — PD-9)

`routes.py` imports H3 types (`H3PromptBuilder`, `H3ReferenceBinding`), contains hardcoded H3 workflow IDs (`h3_r2v_image_pack_v1` etc.) and resolution (`1376x768`). Cleanup will occur when Generation API is consolidated. Not a standalone milestone.

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

## P4 — Operator Workflow & Product Architecture

### Phase 1: Operator Workflow (Complete)

- PRODUCT_SPEC.md: full operator-journey audit, 23 gaps identified, 13+ resolved
- Original idea preservation (`director_context.original_idea`)
- Reference prompt preview/control (character + environment, with overrides)
- Reference prompt provenance on generated cards
- P3 story contamination removed from environment negative prompt
- Shot Production Editor (unified semantic inputs → compiled H3 prompt)
- Take generation provenance (historical immutable details per Take)
- Character data-lineage fix (enrichment ordering, canonical name resolution)
- UX cleanup: Fresh/Outdated badges, Review Prompt buttons, larger thumbnails, individual subject removal, improved field labels, larger prompt area, idea modal, structured Take details

### Phase 2: Product-Model Audit (Complete)

- Full product archaeology report
- Entity capability matrix (35 target concepts classified)
- Provider leakage audit
- Dependency/invalidation readiness assessment
- 10 product decisions accepted (PD-1 through PD-10)
- Documentation consolidated (PRODUCT_SPEC, ARCHITECTURE, ROADMAP, DEVELOPMENT_STATE, CHATGPT_HANDOFF)

### Phase 3: Location Design (Complete)

- Location domain design finalized: `Location(id, project_id, name, description, source, version, created_at, updated_at)` — no lifecycle status field
- Three conceptual layers: Location (persistent identity) / Scene Environment State (future, on Scene) / Shot Environment (existing `Shot.environment` dict)
- Multi-Location enrichment is target behavior for new projects, not a post-MVP enhancement
- Legacy migration: one Location per existing project from `director_context.environment_description`
- Staleness: Location description edit → generated refs STALE; Scene Location change → affected shots/plans `outdated`; Takes remain immutable
- Readiness evaluated per-shot via Scene → Location; ready scenes can generate independently
- Shot alternate-location override architecturally reserved but not implemented
- 7 implementation slices defined (see ROADMAP.md)

### Demonstrated Gaps Remaining

See `docs/PRODUCT_SPEC.md` Gap Registry for the complete list (31 gaps tracked, 11 resolved).

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
