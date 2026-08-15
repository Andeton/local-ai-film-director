# Development State — Local AI Film Director

**Last Updated:** 2026-08-16

---

## Current Milestone

**M3 — H3 Bridge (Vertical Slice)**

**Status:** IN PROGRESS — M3.A and M3.B complete, M3.C next

---

## Completed Milestones

| Milestone | Date | Key Outcome |
|---|---|---|
| M0 | 2026-08-14 | Technical discovery: RTX 5090, ComfyUI 0.32.0 verified, H3 models installed, R2V prompt format discovered, upstream projects researched |
| M0.3 | 2026-08-14 | ComfyUI MCP assessed: 40+ tools, all H3 nodes visible, development-only role confirmed |
| M0.4 | 2026-08-14 | Wind Comic v12.320 validated locally: runs on port 3000, Ollama connects (gemma4 fails strict JSON), 10 duplication warnings with M1-M8, full artifact inspection |
| M0.5 | 2026-08-14 | Architecture frozen: hybrid sidecar, 4 ADRs, canonical data model, 10-milestone roadmap, first vertical slice defined |
| M0.6 | 2026-08-14 | Model-agnostic boundary correction: H3 fields removed from Shot/CharacterReference, GenerationPlan + H3PromptV1 as separate artifacts, ADR-005 |
| M0.7 | 2026-08-14 | Documentation consistency: ADR header fixed, M2 uses generic strategy names, ADR-002 clarifies GenerationPlan is model-agnostic, terminology verified across all docs |
| M1 | 2026-08-15 | Integration Core: Python 3.14.3, FastAPI scaffold, WC SQLite read-only adapter (real WC schema verified), canonical Project/Sequence/Scene/CharacterReference with provenance, atomic import with change detection (added/modified/deleted), SQLite persistence with UPSERT/UNIQUE/FK, Ollama LLM provider (gemma4:e4b structured JSON verified), API with 11 endpoints. 185 tests (182 deterministic + 3 live Ollama). |
| M2 | 2026-08-16 | Production Specification: Beat/ShotSpecificationV1/GenerationPlan canonical models (model-agnostic, zero provider fields), BeatEnricher + CoveragePlanner (LLM object-wrapper contract, domain repair), deterministic ShotSpecBuilder (non-lossy character refs), deterministic StrategySelector (explicit context, 5-priority precedence), history-preserving re-enrichment (OUTDATED + new IDs, never delete), human editing API with stale propagation + force protection (409), atomic M1+M2 source-change cascade, 23 API endpoints total. 418 tests (413 deterministic + 5 live Ollama). Exit criteria 12/12 PASS. Backlog: _find_project_id_for_scene O(N) scan (MINOR). |

---

## Architecture Decisions

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Hybrid Wind Comic Sidecar Architecture | Accepted |
| ADR-002 | Canonical Production Specification independent of Wind Comic | Accepted |
| ADR-003 | ComfyUI runtime via REST/WebSocket API only | Accepted |
| ADR-004 | ComfyUI MCP as development tool only | Accepted |
| ADR-005 | Provider-specific generation artifacts separated from canonical model | Accepted |

---

## Known Blockers

| Blocker | Severity | Impact | Mitigation |
|---|---|---|---|
| No 14B+ LLM model installed locally | HIGH | Enrichment agents (beats, coverage) need reliable structured output | Download qwen2.5:14b or use OpenRouter |
| No T2V/I2V API-format workflow template | LOW | Only R2V is API-ready; T2V/I2V need construction from native nodes | Use MCP to construct during M3 |
| No H3 Turbo LoRA installed | LOW | Generation will be slower (20 steps vs 6-10) | Download when needed |
| Wind Comic requires budget hack for demo user | LOW | Budget cap blocks project creation | Set budget_hard_cap_cny to 99999 |

---

## Deferred Items

| Item | Reason | When |
|---|---|---|
| Wind Comic fork/modification | Sidecar architecture — don't modify WC | Revisit if WC becomes blocking |
| Blender/ControlNet spatial continuity | Not MVP | Post-M10 |
| Distributed GPU rendering | Not MVP | Post-M10 |
| Multi-engine routing (WAN, LTX, etc.) | H3-only for MVP | Post-M10 |
| Mobile / cloud deployment | Not MVP | Post-M10 |
| Automatic rejection (AI auto-deletes) | Human review required in MVP | Post-M10 |
| Full Wind Comic v1 API | Requires API_KEYS env var; SQLite read is simpler | Revisit if multi-machine setup needed |

---

## Next Approved Action

**M3.C — H3ReferenceResolver + H3PromptBuilder**

Resume from branch `m3-h3-bridge` at commit `673ccca`.
Worktree: `D:\Ai\Local AI Film Director\.worktrees\m3-h3-bridge`

**M3 Progress:**
- M3.A: COMPLETE — runtime preflight, real R2V execution validated, template SHA `3893eb4a...`
- M3.B: COMPLETE — H3ReferenceBinding/WorkflowInjection/H3PromptV1/GenerationRequest/Take models, DB tables, 9 errors
- M3.C: NEXT — H3ReferenceResolver (content SHA, min resolver) + H3PromptBuilder (deterministic)
- M3.D–M3.H: NOT STARTED

**Authoritative docs:** `docs/superpowers/plans/2026-08-14-m3-h3-bridge.md`, `docs/M3_PREFLIGHT.md`
**Baseline:** 454 deterministic + 5 live = 459 tests, 0 failed

---

## Key File Locations

| File | Purpose |
|---|---|
| `Техническое задание и roadmap_...md` | Original specification (historical, do not modify) |
| `docs/M0_DISCOVERY.md` | M0 technical discovery report |
| `docs/M0_COMPONENT_MATRIX.md` | Component matrix |
| `docs/M0_OPEN_QUESTIONS.md` | Open questions + phase/milestone mapping |
| `docs/M0_3_COMFYUI_MCP_ASSESSMENT.md` | MCP tooling assessment |
| `docs/M0_4_WIND_COMIC_VALIDATION.md` | Wind Comic local validation |
| `docs/M0_4_WIND_TO_LOCAL_FILM_DIRECTOR_MAPPING.md` | WC → our spec field mapping |
| `docs/ARCHITECTURE_V1.md` | Frozen architecture (V1) |
| `docs/ROADMAP_V2.md` | Rebased roadmap |
| `docs/DEVELOPMENT_STATE.md` | This file |
| `docs/architecture/ADR-001-*.md` | Hybrid sidecar decision |
| `docs/architecture/ADR-002-*.md` | Canonical data model decision |
| `docs/architecture/ADR-003-*.md` | ComfyUI runtime boundary |
| `docs/architecture/ADR-004-*.md` | MCP development boundary |
| `docs/architecture/ADR-005-*.md` | Provider-specific generation artifacts |
| `experiments/wind-comic/` | Wind Comic clone (isolated, do not modify) |
| `src/film_director/` | M1 production source (FastAPI backend) |
| `src/film_director/config.py` | Application configuration (pydantic-settings) |
| `src/film_director/main.py` | FastAPI application factory |
| `src/film_director/adapters/wind_comic.py` | Wind Comic SQLite read-only adapter |
| `src/film_director/models/canonical.py` | Canonical production models |
| `src/film_director/models/provenance.py` | Provenance tracking + source hash |
| `src/film_director/persistence/` | Our SQLite persistence layer |
| `src/film_director/services/import_service.py` | WC import pipeline + change detection |
| `src/film_director/llm/` | LLM provider abstraction (Ollama) |
| `src/film_director/api/routes.py` | API route definitions |
| `src/film_director/enrichment/` | M2 enrichment layer (BeatEnricher, CoveragePlanner, ShotSpecBuilder, StrategySelector, StalePropagator) |
| `src/film_director/services/enrichment_service.py` | M2 enrichment orchestrator + atomic M1/M2 change cascade |
| `src/film_director/generation/` | M3 H3 provider layer (h3_types, h3_prompt, generation_request models) |
| `workflows/h3/r2v_v1.json` | Verified H3 R2V API workflow template |
| `docs/M3_PREFLIGHT.md` | M3.A runtime preflight evidence + frozen implementation facts |
| `tests/` | Test suite (unit + integration + live) |
