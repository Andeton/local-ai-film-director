# Development State — Local AI Film Director

**Last Updated:** 2026-08-17

---

## Current Milestone

**M7 — Continuity**

**Status:** OPEN

**Branch:** `m7-continuity`  
**Worktree:** `D:\Ai\Local AI Film Director\.worktrees\m7-continuity`

### Completed Subtasks

| Subtask | Commit | Tests Added |
|---|---|---|
| M7.A — Continuity models, persistence, ordering, fingerprinting | `d611d11` | +33 |
| M7.B — FLF workflow preflight and versioned continuity bindings | `edaabda` | +33 |
| M7.C — Continuity resolution and GenerationService integration | `33edd9f` | +18 |
| M7.D — Approved-Take replacement and downstream invalidation | `5f936c7` | +26 |
| M7.E — Continuity API, rebuild, and queue dependency hardening | `3eb48e3` | +36 |
| M7.F — Live acceptance (conditional) + identity fix | `9dc24a0` | +20 |
| M7.G.A — VisualAssetPack, AssetRole, AssetRoleBinding | `a4b06a7` | +67 |
| M7.G.B — ConditioningRecipe, CapabilityRegistry (FROZEN) | `f5f5b20` | +112 |

### Current State

- M7.G.B infrastructure is FROZEN — no further expansion
- CapabilityRegistry, runtime probing, capability persistence: available but not wired to production
- AssetRole (18 values) and ConditioningRecipe available for M7.G.C integration
- Next action: **M7.G.C — H3 image-only multi-reference recipe**

### M7.G.C Requirements

Minimal H3 image-only multi-reference integration with at least:
- Picture 1: character identity
- Picture 2: environment view
- Picture 3: predecessor continuity frame
- Picture 4: important prop (optional)

No ref_video. No ref_audio. Derive smallest workflow from proven R2V template.

---

## Deterministic Baseline

**1692 passed, 12 deselected, 0 failed**

Live tests (deselected): Ollama (5), ComfyUI (4), Wind Comic (3)

---

## Frozen Workflow Fingerprints

| Workflow | Fingerprint |
|---|---|
| r2v_v1 | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` |
| r2v_v2 | `b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9` |
| flf_v1 | `47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6` |

---

## Closed Milestones

| Milestone | Merged | Key Outcome |
|---|---|---|
| M1 | `main` | FastAPI, WC adapter, canonical models, import pipeline |
| M2 | `main` | Beats, shots, plans, strategy selection, enrichment |
| M3 | `main` | Shot→R2V→ComfyUI→Take vertical slice |
| M4 | `main` | idea→WC SSE→canonical import→enrichment |
| M5 | `main` at `d4e0fbe` | Reference lifecycle, ingest, generators, r2v_v2, staleness |
| M6 | `main` at `6b6e6e2` | Take approval, persistent queue, worker recovery |

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

## Known Backlog

| Item | Severity | Notes |
|---|---|---|
| H3 Turbo LoRA not installed | LOW | 20-step generation (~4 min) is acceptable |
| WC Writer dialogue quality (qwen3:14b) | LOW | Fields may be empty; LLM fallback works |
| `_find_project_id_for_scene` O(N) scan | MINOR | Acceptable for current project sizes |
| QueueJob may retain transient error on recovery | MINOR | Does not affect correctness |

---

## Key File Locations

| File | Purpose |
|---|---|
| `docs/ARCHITECTURE.md` | System architecture and boundaries |
| `docs/ROADMAP.md` | Product-first roadmap |
| `docs/TECHNOLOGY_RADAR.md` | Model capabilities and workflow status |
| `docs/architecture/ADR-*.md` | Frozen architecture decisions |
| `src/film_director/` | Production source |
| `tests/` | Test suite |
| `workflows/h3/` | Frozen H3 workflow templates |
| `workflows/reference/` | Reference generation workflows |
