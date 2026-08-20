# ChatGPT/Claude Code Handoff — Local AI Film Director

**Date**: 2026-08-20
**Branch**: `p3-scene-completion` (run `git rev-parse HEAD` for current commit)
**Test baseline**: 1923 passed, 12 deselected (live tests)

## 1. Project Purpose

Local AI Film Director orchestrates end-to-end AI film production:
idea -> Wind Comic pre-production -> canonical import -> LLM enrichment (shot plan + character definitions + environment description) -> reference image generation -> H3 video generation -> take approval -> continuity chain -> scene assembly.

**Stack**: Python 3.14, FastAPI, SQLite, ComfyUI (MiniMax H3), Ollama (local LLM), OpenRouter (planning LLM).

## 2. Paths

| What | Path |
|------|------|
| Project root | `D:\Ai\Local AI Film Director` |
| ComfyUI (READ-ONLY) | `D:\ComfyUI\` (Comfy Desktop) |
| ComfyUI models | `D:\ComfyUI\TD_1\ComfyUI\models\` |
| Real H3 user workflows | `D:\ComfyUI\TD_1\ComfyUI\user\default\workflows\` |
| Project copies of real workflows | `workflows/source_reference/minimax_h3/` |
| LFDirector API templates | `workflows/h3/` |
| Reference gen templates | `workflows/reference/` |
| Production database | `data/p2_scene.db` |
| Storage root | `storage/` |
| Wind Comic DB | `experiments/wind-comic/data/qfmj.db` |

**CRITICAL**: `D:\ComfyUI\` is external and READ-ONLY. Never modify files there.

## 3. Current Branch/HEAD

Work is on branch `p3-scene-completion`. Run `git rev-parse HEAD` for current commit.

## 4. Production Project: proj_cfb89b04f3c8

**Original idea**: "A tense nighttime scene in a small New York apartment. An exhausted man sits alone at a kitchen table when he hears someone quietly trying to unlock the front door..."

**Scene assembly**: COMPLETE — HUMAN PASS. 48.741s, 1376x768, 6 shots.

### Characters

| ID | Name | Role |
|----|------|------|
| char_5fdd33d62bbc | The Man | Exhausted man at kitchen table |
| char_e04f59773286 | The Woman | Woman with blood-stained envelope |

### Approved References

| ID | Kind | Owner |
|----|------|-------|
| ref_cd7e358fc55f | character_body | The Man |
| ref_44bc8980faf4 | character_body | The Woman |
| ref_c9fa948003bd | environment | Project-level |

### Six Shots — ALL APPROVED

| # | ID | Action | Subjects | Inputs | Take | Status |
|---|-----|--------|----------|--------|------|--------|
| 1 | shote6b5120632df | Man sits at kitchen table | The Man | 2 (char+env) | take1177d3c08bbd | APPROVED |
| 2 | shot83bdb22a9c07 | Man hears door, turns off lamp | The Man | 3 (char+env+cont) | take5e7ed5c36a09 | APPROVED |
| 3 | shote7b54e8ac94f | POV: woman enters with envelope | The Woman | 3 (char+env+cont) | takeeae22d01305f | APPROVED |
| 4 | shot088711c1c99b | Close-up woman, meets man's eyes | Both | 4 (char+env+cont+char2) | takeaf8e1a965985 | APPROVED |
| 5 | shot84f372d8c579 | Man opens envelope, shock | The Man | 3 (char+env+cont) | takedda112d68a2f | APPROVED |
| 6 | shot7d12065a94e4 | Wide: both, police lights | Both | 4 (char+env+cont+char2) | take418da86f5433 | APPROVED |

### Production Observations

- Shots 3, 5, 6 timed out under the old synchronous 600s timeout and were recovered via `finalize_from_result()`
- Shot 6 (4-input, 10s video) took 73.8 minutes in ComfyUI — significantly longer than typical 7-13 minute renders. No execution error; ComfyUI completed successfully. Cause unknown but did not affect output quality.
- H3 occasionally generates spontaneous dialogue audio without explicit prompt control.
- Primary-subject slot ordering remains an open question with no demonstrated failure.

## 5. H3 Image-Pack Slot Policy

**Workflow**: `h3_r2v_image_pack_v1` (4 materialized slots)

| Slot | Content |
|------|---------|
| Picture 1 | Primary character identity (CHARACTER_BODY) |
| Picture 2 | Environment (ENVIRONMENT) |
| Picture 3 | Predecessor continuity frame (downstream only) |
| Picture 4 | Second character (when 2-subject shot) |

- Unused slots are **pruned** before ComfyUI submission (LoadImage nodes + ref_images connections removed)
- Overflow (>4 required inputs) raises `ParameterResolutionError`
- Preview and execution use identical subject-scoped reference selection

## 6. Key Architecture Decisions

### Durable Async Generation (P3)

The Operator UI now uses the persistent queue lifecycle for all generation:

1. Operator clicks Generate
2. `POST /shots/{id}/generate` enqueues a durable queue job → returns 202 immediately
3. Embedded QueueWorker (background thread in FastAPI process) claims and executes
4. Worker monitors ComfyUI via WebSocket, finalizes Take on completion
5. UI polls `GET /queue/jobs/{job_id}` every 4 seconds for status
6. Page refresh rediscovers active jobs via `GET /queue/jobs?shot_id=X`

**Timeout semantics**: The 1200s timeout is a monitoring liveness safeguard only. If monitoring times out, the job stays `claimed` (not `failed`). On next worker poll cycle, recovery checks ComfyUI history and finalizes if completed. Correctness does not depend on H3 finishing within the timeout.

**Duplicate protection**: `has_active_jobs(shot_id)` prevents concurrent generation for the same shot. Returns 409 if pending/claimed jobs exist.

**Recovery on restart**: Worker runs `recover()` on startup, reconciling all `claimed` jobs against ComfyUI history. Failed requests with a `comfyui_prompt_id` are also checked (State 12b).

### OpenRouter for Planning
- `OPENROUTER_API_KEY` (bare env var, no FILM_ prefix) loaded via model_validator fallback
- Default model: `google/gemini-2.5-flash` (configurable via `FILM_OPENROUTER_MODEL`)
- Used for: shot planning, character enrichment, environment description derivation
- Ollama remains available for legacy beat/coverage chain

### Enrichment Semantics
- "Enrich Missing Data": idempotent — only creates shots if none exist, enriches deficient characters, derives environment description if missing
- "Regenerate Shot Plan": separate destructive action, refuses if Takes exist
- Character enrichment skips characters with meaningful appearance (>20 chars + non-generic name)

### Reference Staleness
- Editing character appearance: GENERATED refs become STALE, USER_UPLOAD refs unaffected
- Editing environment description: GENERATED env refs become STALE, USER_UPLOAD unaffected
- Based on SHA-256 fingerprint comparison of source text

### Browser Persistence
- `localStorage.film_director.selected_project_id`
- `localStorage.film_director.selected_shot_id`
- Restored on page refresh; falls back to first project if stored ID is invalid

## 7. Known Limitations / Technical Debt

1. **Video paths contain Windows backslashes** in the DB (e.g. `storage\takes\...`). The media router normalizes these server-side.

2. **Primary-subject slot ordering**: Open design question, no demonstrated failure. Shot 4 and Shot 6 (both 4-input) produced visually acceptable results.

3. **Spontaneous H3 dialogue**: H3 sometimes generates dialogue audio even when not explicitly prompted. Not currently controlled.

4. **No H3 prompt compilation stage**: Shot action text used directly as video prompt. No intermediate prompt optimization step.

5. **Wind Comic quality**: WC's gemma4 model produces generic placeholder content. Project description compensates.

6. **Legacy FLF path**: Still exists as fallback. Not the selected production path.

7. **Operator console is a single HTML file**: `src/film_director/ui/static/index.html` (~700 lines). Functional but not a modern frontend.

8. **Shot 6 anomalous duration**: 73.8 min actual render vs typical 7-13 min. No execution error. Cause unknown. Single observation — may be a ComfyUI scheduling anomaly, not a systematic issue.

## 8. Environment Variables (.env)

```
FILM_DATABASE_PATH=data/p2_scene.db
FILM_STORAGE_ROOT=storage
FILM_WC_DATABASE_PATH=experiments/wind-comic/data/qfmj.db
FILM_LLM_PROVIDER=ollama
FILM_LLM_MODEL=qwen3:14b
FILM_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
FILM_COMFYUI_BASE_URL=http://127.0.0.1:8188
FILM_COMFYUI_GENERATION_TIMEOUT=1200
FILM_WINDCOMIC_BASE_URL=http://127.0.0.1:3000
OPENROUTER_API_KEY=...  # bare key, no FILM_ prefix
```

## 9. Exact Next Action

**Merge `p3-scene-completion` to `main`.**

Then choose the next product-first priority from demonstrated production gaps:

1. **H3 Prompt Compilation** — Shot action text is used directly as the H3 video prompt with no optimization. An intermediate "compile shot direction into optimal H3 prompt" step could improve generation quality. This is the most impactful demonstrated gap.

2. **Second production project** — Run a second complete idea-to-scene pipeline to validate generalization beyond the first project.

3. **AI reviewer (M8)** — Automated quality assessment of generated Takes before human review.

Do NOT start LTX, broad model routing, or generalized infrastructure until a second production validates the current pipeline.

**Runtime commands:**
```bash
cd "D:/Ai/Local AI Film Director"
python -m uvicorn "film_director.main:create_app" --factory --host 127.0.0.1 --port 8000
```

**Durable documentation:** See `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_STATE.md`, `docs/ROADMAP.md`, `docs/TECHNOLOGY_RADAR.md` for system design and implementation status.
