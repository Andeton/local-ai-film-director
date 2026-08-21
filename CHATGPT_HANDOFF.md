# ChatGPT/Claude Code Handoff — Local AI Film Director

**Date**: 2026-08-21
**Branch**: `p4-operator-workflow` (run `git rev-parse HEAD` for current commit)
**Main HEAD**: `0ea4bce835264621a2513af52cb12aa75b083479`
**Test baseline**: 1997 passed, 1 skipped, 12 deselected (live tests)
**GitHub**: https://github.com/Andeton/local-ai-film-director.git

## 1. Project Purpose

Local AI Film Director (LFDirector) is an **AI Director + Production Manager + ComfyUI Orchestrator**. It owns the complete production pipeline from idea to assembled film:

idea → canonical pre-production (story/treatment/style/characters/locations) → scene/beat/shot planning → storyboard → reference management → generation → take review → continuity → timeline → export.

LFDirector owns the canonical production specification (ADR-002). External systems (Wind Comic, ComfyUI, H3, OpenRouter) are sources and execution providers, not canonical owners.

**Stack**: Python 3.14, FastAPI, SQLite, ComfyUI (MiniMax H3), OpenRouter (planning LLM), Ollama (legacy LLM).

## 2. Paths

| What | Path |
|------|------|
| Project root | `D:\Ai\Local AI Film Director` |
| ComfyUI (READ-ONLY) | `D:\ComfyUI\` (Comfy Desktop) |
| Production database | `data/p2_scene.db` |
| Storage root | `storage/` |
| Product specification | `docs/PRODUCT_SPEC.md` |

**CRITICAL**: `D:\ComfyUI\` is external and READ-ONLY.

## 3. Current Branch/HEAD

Work is on branch `p4-operator-workflow`. P3 was merged to main at `0ea4bce`.

## 4. Completed Production

**Project `proj_cfb89b04f3c8`**: 6/6 shots APPROVED, 48.741s assembled scene, HUMAN PASS.

## 5. P4 State

P4 has two completed phases:

### Phase 1: Operator Workflow (Complete)
- Original idea preservation, reference prompt preview/control, Shot Production Editor, Take generation provenance, character data-lineage fix, UX cleanup. See `docs/DEVELOPMENT_STATE.md` for full list.

### Phase 2: Product-Model Audit (Complete — 2026-08-21)
- Full product archaeology and entity capability audit
- 10 product decisions accepted (PD-1 through PD-10)
- Target product architecture documented in PRODUCT_SPEC.md
- Documentation consolidated across all five handoff files

### Accepted Product Decisions (PD-1 through PD-10)

| ID | Decision |
|----|----------|
| PD-1 | Location is first-class canonical concept. Scenes reference reusable Locations. |
| PD-2 | CharacterReference is conceptually Character. Code rename deferred. |
| PD-3 | Beat remains in canonical hierarchy (Scene → Beat → Shot). |
| PD-4 | Prompt editing has persistent shot-level draft + immutable per-generation snapshot. |
| PD-5 | Story, Treatment, Style are canonical LFDirector-owned artifacts. |
| PD-6 | Wind Comic is source/sidecar, not canonical owner. Output-quality revisit condition demonstrated. |
| PD-7 | ReferenceKind (what asset IS) and AssetRole (how asset is USED) remain separate. |
| PD-8 | Storyboard is core pre-generation stage. |
| PD-9 | H3 leakage in routes is acknowledged tech debt, not a standalone milestone. |
| PD-10 | Timeline is future minimal. Semantic continuity deferred. |

## 6. Key Architecture Clarifications (Audit)

### Ownership Principle
LFDirector is the canonical owner of ALL production data. Wind Comic is a SOURCE that enters through `WindComicAdapter`. WC's output quality has been demonstrated as insufficient (generic placeholder content), confirming ADR-001's revisit condition. LFDirector compensates via OpenRouter planning and operator input.

### Current Implementation vs Target
The current implementation successfully produces complete films (P3 proved this) but represents a subset of the target product model. Key gaps that block multi-scene/multi-location productions:
- No Location entity (environment is project-level only)
- No persistent prompt drafts (lost on shot switch)
- No storyboard review stage

Story, Treatment, Style entities and Prop/Timeline models are accepted for the target but do not block current single-scene production.

### Naming Debt
`CharacterReference` class/table = Character entity. `director_context` dict = fragments of Story + Treatment + Style. Code/schema renames deferred.

## 7. Environment Variables (.env)

```
FILM_DATABASE_PATH=data/p2_scene.db
FILM_STORAGE_ROOT=storage
FILM_WC_DATABASE_PATH=experiments/wind-comic/data/qfmj.db
FILM_COMFYUI_BASE_URL=http://127.0.0.1:8188
FILM_COMFYUI_GENERATION_TIMEOUT=1200
OPENROUTER_API_KEY=...
```

## 8. Runtime

```bash
cd "D:/Ai/Local AI Film Director"
python -m uvicorn "film_director.main:create_app" --factory --host 127.0.0.1 --port 8000
```

## 9. Exact Next Action

**Product-model consolidation on `p4-operator-workflow`.**

The product-model audit is complete and documented. The next step is implementation planning for the highest-priority product-model gaps, in this order:

1. **Location entity + per-scene environment** — blocks multi-scene production
2. **Persistent shot-level generation drafts** — improves operator workflow
3. **Storyboard pre-generation review** — reduces wasted GPU time
4. **H3 Prompt Compilation** — improves generation quality
5. **Second production project** — validates generalization (after Location entity)

Do NOT start broad infrastructure, new model adapters, or generalized routing until Location entity and a successful multi-scene production validate the target architecture.

**Durable documentation:** `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_STATE.md`, `docs/ROADMAP.md`, `docs/TECHNOLOGY_RADAR.md`, `docs/PRODUCT_SPEC.md`.
