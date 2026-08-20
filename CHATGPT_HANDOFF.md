# ChatGPT/Claude Code Handoff — Local AI Film Director

**Date**: 2026-08-20
**Main HEAD**: see `git rev-parse HEAD` after checkout
**Test baseline**: 1907 passed, 12 deselected (live tests)

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

All work is on `main`. Run `git rev-parse HEAD` for current commit.

## 4. Production Project: proj_cfb89b04f3c8

**Original idea**: "A tense nighttime scene in a small New York apartment. An exhausted man sits alone at a kitchen table when he hears someone quietly trying to unlock the front door..."

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

### Six Shots (in order)

| # | ID | Action | Subjects | Status |
|---|-----|--------|----------|--------|
| 1 | shote6b5120632df | Man sits at kitchen table | The Man | APPROVED (take1177d3c08bbd) |
| 2 | shot83bdb22a9c07 | Man hears door, turns off lamp | The Man | APPROVED (take5e7ed5c36a09) |
| 3 | shote7b54e8ac94f | POV: woman enters with envelope | The Woman | APPROVED (takeeae22d01305f) |
| 4 | shot088711c1c99b | Close-up woman, meets man's eyes | Both | SUCCEEDED, awaiting approval (takeaf8e1a965985) |
| 5 | shot84f372d8c579 | Man opens envelope, shock | The Man | NOT GENERATED |
| 6 | shot7d12065a94e4 | Wide: both, police lights | Both | NOT GENERATED |

### Shot 4 Observations

Shot 4 generated successfully using the full 4-slot image-pack:
- Picture 1: The Man (CHARACTER_BODY)
- Picture 2: Environment
- Picture 3: Shot 3 continuity frame
- Picture 4: The Woman (CHARACTER_BODY)

**Human visual assessment**: Shot 4 looks very good. The 4-input image-pack
produced a usable two-character scene despite The Woman being Picture 4.

**Spontaneous dialogue**: H3 generated a small/simple dialogue element despite
dialogue not being explicitly controlled in the prompt. This is an observation
for later audio/dialogue-control evaluation, not a current blocker.

Shot 4 remains `succeeded` / awaiting human approval.

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

### Generation Timeout
- Default: 1200s (20 minutes) — image-pack renders take 10-15 minutes
- Configurable via `FILM_COMFYUI_GENERATION_TIMEOUT`
- Completed renders can be recovered via `finalize_from_result()`

### Browser Persistence
- `localStorage.film_director.selected_project_id`
- `localStorage.film_director.selected_shot_id`
- Restored on page refresh; falls back to first project if stored ID is invalid

## 7. Known Limitations / Technical Debt

1. **Video paths contain Windows backslashes** in the DB (e.g. `storage\takes\...`). The media router normalizes these server-side. Reference image paths were fixed to use forward slashes but Take video paths still use `os.path.join`.

2. **Primary-subject slot ordering**: Shot 4 is a close-up of The Woman, but The Man is listed first in `subjects`, placing The Man in Picture 1 and The Woman in Picture 4. Shot 4 live acceptance with all 4 inputs produced a visually good result despite this ordering. Primary-subject slot ranking remains an open question, but there is currently no evidence requiring a change.

8. **Spontaneous H3 dialogue**: H3 sometimes generates dialogue audio even when not explicitly prompted. Observed in Shot 4. Not currently controlled. Mark for later audio/dialogue-control evaluation.

3. **No H3 prompt compilation stage**: The shot action text is used directly as the video prompt. There is no intermediate "compile shot direction into optimal H3 prompt" step. The H3PromptBuilder assembles sections mechanically.

4. **Wind Comic quality**: WC's gemma4 model produces generic placeholder content for most projects. The project description (original idea) is the primary creative context; WC scene/script/character data is typically generic.

5. **Legacy FLF path**: Still exists as fallback when no environment ref is available. Not the selected production path.

6. **No batch generation queue**: Generation is synchronous per-shot through the UI.

7. **Operator console is a single HTML file**: `src/film_director/ui/static/index.html` (~700 lines). Functional but not a modern frontend.

## 8. Environment Variables (.env)

```
FILM_DATABASE_PATH=data/p2_scene.db
FILM_STORAGE_ROOT=storage
FILM_WC_DATABASE_PATH=experiments/wind-comic/data/qfmj.db
FILM_LLM_PROVIDER=ollama
FILM_LLM_MODEL=qwen3:14b
FILM_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
FILM_COMFYUI_BASE_URL=http://127.0.0.1:8188
FILM_COMFYUI_GENERATION_TIMEOUT=600  # increase to 1200+ for image-pack
FILM_WINDCOMIC_BASE_URL=http://127.0.0.1:3000
OPENROUTER_API_KEY=...  # bare key, no FILM_ prefix
```

## 9. Recommended Next Action

1. **Approve Shot 4**: The operator assessed it as visually good. Approve via UI or `POST /takes/takeaf8e1a965985/approve`.

2. **Generate Shots 5-6**: Continue through the normal UI Generate Take flow.

3. **After all 6 shots approved**: Build the scene assembly (`POST /projects/{id}/build-scene`).

4. **Suggested next feature branch**: `p3-scene-completion`
