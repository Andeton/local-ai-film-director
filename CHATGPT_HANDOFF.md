# ChatGPT/Claude Code Handoff — Local AI Film Director

**Date**: 2026-08-21
**Branch**: `p4-operator-workflow`
**HEAD**: `557527b53a2ecfa68990a6b5916f8d2c03769628`
**Main HEAD**: `0ea4bce835264621a2513af52cb12aa75b083479`
**Test baseline**: 2168 passed, 1 skipped, 12 deselected (live tests)
**GitHub**: https://github.com/Andeton/local-ai-film-director.git

## 1. Project Purpose

Local AI Film Director (LFDirector) is an **AI Director + Production Manager + ComfyUI Orchestrator**. It owns the complete production pipeline from idea to assembled film.

LFDirector owns the canonical production specification (ADR-002). External systems (Wind Comic, ComfyUI, H3, OpenRouter) are sources and execution providers, not canonical owners.

**Stack**: Python 3.14, FastAPI, SQLite, ComfyUI (MiniMax H3), OpenRouter (planning LLM), Ollama (legacy LLM).

## 2. Paths

| What | Path |
|------|------|
| Project root | `D:\Ai\Local AI Film Director` |
| ComfyUI (READ-ONLY) | `D:\ComfyUI\` (Comfy Desktop) |
| Production database | `data/p2_scene.db` |
| Acceptance database | `data/acceptance_slice6.db` (disposable) |
| Storage root | `storage/` |
| Product specification | `docs/PRODUCT_SPEC.md` |

**CRITICAL**: `D:\ComfyUI\` is external and READ-ONLY.

## 3. Current State Summary

### Location Implementation: Slices 1-6 COMPLETE

| Slice | Status |
|---|---|
| 1. Model/persistence/repository | COMPLETE (29 tests) |
| 2. Legacy migration | COMPLETE (24 tests) |
| 3. API + assignment + staleness | COMPLETE (36 tests) |
| 4. Ref management + generation resolution + readiness | COMPLETE (21 tests) |
| 5. Multi-Location planning/enrichment | COMPLETE + corrected (40 tests) |
| 6. Operator UI | COMPLETE (22 tests) |
| 7. End-to-end acceptance production | NOT STARTED |

**Slice 5 live validation**: OpenRouter check PASSED — 4 scenes (apartment/subway/office/apartment) → 3 Locations with correct apartment reuse via google/gemini-2.5-flash.

### Human UI Acceptance: FAILED

Location Slice 6 UI was tested by human operator. Result: **FAIL**.

Feedback: "The interface is not intuitive. It is unclear what is happening and where to go. Normal production actions should feel native, but the current interface requires too much interpretation."

This is NOT a code bug — the backend/API layer works correctly. The failure is in **information architecture, workflow hierarchy, navigation, and separation of production stages**.

### UX Architecture Audit: COMPLETED (design-only)

A comprehensive UX architecture audit was performed. Key findings:

- Current UI was built incrementally bottom-up (M1→P4→Slices) rather than from a production workflow down
- No production context on project entry — dumps directly into Shot 1
- Flat shot list hides scene/location production structure
- Three workspace tabs (Shots/Locations/Characters) are peers when they should be a hierarchy
- Shot detail mixes specification + generation + results in one scroll
- Technical vocabulary (H3, Picture slots, workflow IDs) is primary operator surface
- Readiness is global rather than per-shot actionable

**Proposed navigation architecture:**
```
PROJECT HOME → STORY → ELEMENTS → SCENES → PRODUCTION → REVIEW → EXPORT
```

**Proposed implementation slices:**
- UX-A: Application Shell + Project Home + Navigation (highest priority)
- UX-B: Scenes Hierarchy
- UX-C: Elements Workspace consolidation
- UX-D: Shot Detail Restructuring
- UX-E: Storyboard View
- UX-F: Review Summary
- UX-G: Export Consolidation

**UX-A was NOT implemented** — only designed. No code changes were made after the Slice 6 commit.

### Product Decisions (PD-1 through PD-10)

All 10 product decisions remain accepted and documented in `docs/PRODUCT_SPEC.md`.

### Completed Productions

**Project `proj_cfb89b04f3c8`**: 6/6 shots APPROVED, 48.741s assembled scene, HUMAN PASS (P3).

## 4. Environment Variables (.env)

```
FILM_DATABASE_PATH=data/p2_scene.db
FILM_STORAGE_ROOT=storage
FILM_WC_DATABASE_PATH=experiments/wind-comic/data/qfmj.db
FILM_COMFYUI_BASE_URL=http://127.0.0.1:8188
FILM_COMFYUI_GENERATION_TIMEOUT=1200
OPENROUTER_API_KEY=...
```

## 5. Runtime

```bash
cd "D:/Ai/Local AI Film Director"
python -m uvicorn "film_director.main:create_app" --factory --host 127.0.0.1 --port 8000
```

## 6. Exact Next Action

**Resume from the UX architecture decision point. Re-read `docs/PRODUCT_SPEC.md` and the UX architecture findings documented in this conversation's session history, then implement UX-A (Application Shell + Project Home + Navigation) as one controlled slice, followed by human acceptance.**

UX-A is the single most impactful change — it establishes the navigation framework and immediately provides production context on project entry. It requires NO backend changes (all data available from existing APIs). All existing functionality remains accessible through reorganized navigation.

Do NOT:
- Skip to UX-B or later without completing UX-A
- Start Location Slice 7 acceptance production until the UI passes human acceptance
- Start Story/Style/Storyboard/Timeline backend work
- Start M8, LTX, or generalized model routing
- Merge p4-operator-workflow to main until human acceptance passes

**Durable documentation:** `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_STATE.md`, `docs/ROADMAP.md`, `docs/TECHNOLOGY_RADAR.md`, `docs/PRODUCT_SPEC.md`.
