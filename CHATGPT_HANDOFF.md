# ChatGPT/Claude Code Handoff — Local AI Film Director

**Date**: 2026-08-20
**Branch**: `p4-operator-workflow` (run `git rev-parse HEAD` for current commit)
**Main HEAD**: `0ea4bce835264621a2513af52cb12aa75b083479`
**Test baseline**: 1997 passed, 1 skipped, 12 deselected (live tests)

## 1. Project Purpose

Local AI Film Director orchestrates end-to-end AI film production:
idea -> Wind Comic pre-production -> canonical import -> LLM enrichment (shot plan + character definitions + environment description) -> reference image generation -> H3 video generation -> take approval -> continuity chain -> scene assembly.

**Stack**: Python 3.14, FastAPI, SQLite, ComfyUI (MiniMax H3), Ollama (local LLM), OpenRouter (planning LLM).

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

## 5. P4 Implementation State

P4 — Operator Workflow & Prompt Control. Partially complete on `p4-operator-workflow`:

**Completed:**
- PRODUCT_SPEC.md: full operator-journey audit with gap tracking
- Original idea preservation (`director_context.original_idea`, legacy degradation)
- Reference prompt preview/control (character + environment)
- Reference prompt provenance on cards
- P3 story contamination removed from environment negative prompt
- Shot Production Editor (unified semantic editor for all shot inputs)
- H3 prompt compilation visible in preview (from actual H3PromptBuilder)
- Take generation provenance (historical immutable details per Take)
- Character data-lineage fix (enrichment ordering, current canonical name resolution)
- UX cleanup: Fresh/Outdated badges, Review Prompt buttons, larger thumbnails, individual subject removal, improved intent labels, larger prompt area, idea modal, structured Take details

**Demonstrated gaps remaining (from human acceptance):**
- G14: Intentional dialogue control — H3 controllability not established
- G17: Side-by-side Take comparison
- G20: Ephemeral prompt override state
- G21-G23: Lower-priority polish items

See `docs/PRODUCT_SPEC.md` for the complete gap registry.

## 6. Key Architecture Decisions (P4)

### Original Idea Preservation
- `director_context.original_idea`: exact operator input, preserved before WC processing
- `director_context.description`: WC-processed version (may contain WC template text)
- Legacy projects: `original_idea` absent, UI shows "Imported Description / legacy project"

### Reference Lifecycle Terminology
- Internal domain: `ReferenceSourceState.CURRENT` / `STALE` (unchanged)
- Operator-facing UI: badges show `Fresh` / `Outdated`
- `REJECTED + CURRENT` (now `REJECTED + Fresh`) is a valid state: not stale, just not approved

### Character Name Resolution
- UI resolves subject display names from current `CharacterReference.name` by `character_id`
- Stale `shot.subjects[].name` snapshots no longer control current display
- Historical Take provenance remains immutable (shows names used at generation time)
- Enrichment ordering: characters enriched before shot planning (future projects get enriched names in ShotSubject snapshots)

### Durable Async Generation
Architecture documented in `docs/ARCHITECTURE.md`. Embedded QueueWorker, timeout recovery, duplicate protection — all from P3, unchanged in P4.

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

**Resume P4 on `p4-operator-workflow`.**

The P4 UX Cleanup (items 1-9 from human acceptance) has been implemented. Perform human UI acceptance of the UX cleanup before deciding the next functional P4 slice.

After UX acceptance, choose from:
1. **Second production project** — validate generalization
2. **H3 Prompt Compilation** — LLM-optimized prompts
3. Additional P4 gaps from PRODUCT_SPEC.md

Do NOT start Environment 360, M8, LTX, broad model routing, or generalized infrastructure.

**Durable documentation:** See `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_STATE.md`, `docs/ROADMAP.md`, `docs/TECHNOLOGY_RADAR.md`, `docs/PRODUCT_SPEC.md`.
