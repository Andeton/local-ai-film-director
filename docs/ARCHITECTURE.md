# Architecture — Local AI Film Director

**Status:** Active
**ADRs:** ADR-001 through ADR-005 (frozen)
**Last updated:** 2026-08-21 (product-model audit)

---

## 1. System Overview

Local AI Film Director (LFDirector) is an **AI Director + Production Manager + ComfyUI Orchestrator** that assembles a working AI film production pipeline from ready-made solutions:

- **Wind Comic** — external pre-production source/sidecar (story, script, storyboard, characters). A SOURCE of pre-production data, not the canonical owner. Known limitation: WC's local gemma4 model produces generic placeholder content. ADR-001 output-quality revisit condition has been demonstrated.
- **ComfyUI** — execution runtime (REST/WebSocket API). External installation at `D:\ComfyUI\`, treated as READ-ONLY by LFDirector development.
- **MiniMax H3** — current video generation model (R2V image-pack, legacy R2V, legacy FLF workflows)
- **Z-Image Turbo / Krea 2 Turbo** — reference image generation
- **OpenRouter** — planning LLM (shot planning, character enrichment, environment description derivation)
- **Ollama** — local LLM (legacy beat/coverage enrichment chain)
- **LFDirector** — canonical production owner: pre-production artifacts, production specification, orchestration, Takes, continuity, approval, assembly

---

## 2. Ownership Principle

LFDirector owns the canonical production specification (ADR-002). External systems are SOURCES of data that enter through adapter boundaries. LFDirector is the canonical owner of:

- Story, Director Treatment, Style Bible (pre-production artifacts, per PD-5)
- Characters, Locations, Props (production elements)
- Scenes, Beats, Shots (production hierarchy)
- References, Generation Plans, Takes (production execution)
- Continuity, Timeline, Assembly (production output)

Wind Comic is a source/sidecar (ADR-001) — its output is imported through `WindComicAdapter` but LFDirector owns the accepted/current canonical version of all production data.

---

## 3. System Boundary Matrix

| Capability | Wind Comic | LFDirector | ComfyUI | H3/Models | OpenRouter | Ollama |
|---|---|---|---|---|---|---|
| Project/story/script/storyboard | SOURCE | CANONICAL OWNER | — | — | — | — |
| Character design/library | SOURCE | CANONICAL OWNER | — | — | — | — |
| Director treatment / style | SOURCE | CANONICAL OWNER | — | — | — | — |
| Shot planning (direct) | — | OWNER | — | — | PROVIDER | — |
| Beat/coverage (legacy) | — | OWNER | — | — | — | PROVIDER |
| Character enrichment | — | OWNER | — | — | PROVIDER | — |
| Environment/location derivation | — | OWNER | — | — | PROVIDER | — |
| Shot specification | — | OWNER | — | — | — | — |
| Generation strategy | — | OWNER | — | — | — | — |
| Prompt building | — | OWNER | — | — | — | — |
| Workflow registry | — | OWNER | — | — | — | — |
| Workflow execution | — | CONSUMER | OWNER | — | — | — |
| Video generation | — | — | CONSUMER | OWNER | — | — |
| Reference generation | — | OWNER | CONSUMER | CONSUMER | — | — |
| Take management | — | OWNER | — | — | — | — |
| Continuity chain | — | OWNER | — | — | — | — |
| Human review | — | OWNER | — | — | — | — |

---

## 4. Target Canonical Production Hierarchy

```
ProductionProject
  ├── Original Idea (immutable operator input)
  ├── Story (canonical, sourced from WC/LLM/operator)
  ├── Director Treatment (canonical, sourced from WC/LLM/operator)
  ├── Style Bible (canonical, sourced from LLM/operator)
  ├── Characters (canonical, sourced from WC/LLM/operator)
  │     └── ReferenceAssets (CHARACTER_BODY, CHARACTER_FACE)
  ├── Locations (canonical, reusable across scenes)
  │     └── ReferenceAssets (ENVIRONMENT / location views)
  ├── Props (canonical)
  │     └── ReferenceAssets (PROP)
  └── Sequence
        └── Scene (references Location, cast, props)
              └── Beat (dramatic unit)
                    └── ShotSpecificationV1 (coverage shot)
                          ├── Storyboard frame
                          ├── GenerationPlan (strategy, requirements)
                          ├── Provider Prompt (H3PromptV1 — below adapter boundary)
                          ├── GenerationRequest (immutable execution snapshot)
                          └── Take (generated video + approval state)
```

All canonical entities above the adapter boundary are **provider/model agnostic** (ADR-005).

### Current Implementation vs Target

The current implementation represents a subset of this hierarchy:

| Concept | Current Implementation | Notes |
|---|---|---|
| Original Idea | `director_context.original_idea` | Exists (P4) |
| Story | `director_context.description` | Fragment, not canonical entity |
| Director Treatment | `director_context.genre/style/story_structure` | Imported, never consumed |
| Style Bible | Not represented | — |
| Character | `CharacterReference` class/table | Naming debt — see PD-2 |
| Location | Not represented | `Scene.location` exists but unused |
| Prop | Not represented | `AssetRole.PROP_REFERENCE` exists in enum |
| Sequence/Scene/Beat/Shot | Full schema | Beat invisible in UI |
| Storyboard | `storyboard_image_path` field exists | Never populated |
| GenerationPlan | Full schema | Model-agnostic |
| Provider Prompt | `H3PromptV1` / `h3_prompts` table | Correctly isolated per ADR-005 |
| GenerationRequest | Full schema | Immutable snapshot |
| Take | Full schema | Full lifecycle |

---

## 5. Enrichment Architecture

### Enrichment Semantics

"Enrich Missing Data" (`POST /projects/{id}/enrich`) is idempotent:
- Creates shots only if NO current shots exist for the project
- Enriches deficient characters (empty appearance or generic template names)
- Derives `environment_description` from the project description if not yet set
- Does NOT overwrite characters with meaningful existing appearances
- Does NOT duplicate or append shots to an existing plan

"Regenerate Shot Plan" (`POST /projects/{id}/replan`) is destructive:
- Atomically replaces current beats/shots/plans with a fresh 5-7 shot plan
- Refuses if any Takes exist (production work would be lost)
- Requires explicit operator confirmation

### Editable Production Definitions

Characters and environment descriptions are editable in the Reference Manager:
- `PUT /characters/{id}` — edit display name and appearance
- `PUT /projects/{id}/environment-description` — edit environment description
- Edits trigger staleness propagation on GENERATED references (USER_UPLOAD refs unaffected)
- IDs, provenance, and shot bindings are preserved

### Enrichment Ordering (P4)

Character enrichment runs BEFORE shot planning so that `ShotSubject.name` snapshots contain enriched names. The enriched character list is used in-memory for shot planning; all entities are persisted in a single atomic transaction.

### Original Idea Preservation (P4)

- `director_context.original_idea`: exact operator input, captured at `POST /projects/from-idea` before WC processing
- `director_context.description`: WC-processed version (may include WC template text)
- Legacy projects (created before P4) may lack `original_idea`; UI labels these "Imported Description / legacy project"

### Character Name Resolution (P4)

Operator-facing current state resolves subject display names from `CharacterReference.name` by `character_id`, not from the stale `ShotSubject.name` snapshot. Historical Take provenance always uses immutable values from the `GenerationRequest` that produced the Take.

---

## 6. Reference Asset Architecture

### Kinds and Roles (Separate Concepts — PD-7)

**ReferenceKind** — what the managed asset IS / ownership semantics:

| Kind | Ownership | Purpose |
|------|-----------|---------|
| CHARACTER_FACE | character_id | Character face identity |
| CHARACTER_BODY | character_id | Character full-body identity (production-critical) |
| ENVIRONMENT | project-level (no char/shot) | Scene environment/location reference |
| STORYBOARD | shot_id | Per-shot storyboard frame |

Target scope (PD-7): Character, Location, Prop, Style, Storyboard references.

**AssetRole** — how an asset is USED in a generation/conditioning recipe:

18 values including CHARACTER_FACE_CLOSEUP, CHARACTER_BODY_FRONT/SIDE/BACK, ENVIRONMENT_MASTER/VIEW/PANORAMA_360/DEPTH/LAYOUT, PROP_REFERENCE/TURNAROUND, STYLE_REFERENCE, CONTINUITY_FRAME, MOTION_REFERENCE, CONTROL_VIDEO, AUDIO_REFERENCE.

AssetRole is used in `VisualAssetPack` bindings and `ConditioningRecipe` slot definitions. Provider-specific picture-slot semantics (e.g., H3 "Picture N") are NOT canonical — they belong below the adapter boundary.

### Lifecycle

`CANDIDATE → APPROVED / REJECTED / ARCHIVED`

Source state: `CURRENT / STALE` (independent from approval)

Eligibility: Only `APPROVED + CURRENT` assets are production-eligible.

### Staleness Propagation

- Editing character appearance: GENERATED refs for that character become STALE if their `source_fingerprint` (SHA-256 of appearance text) no longer matches
- Editing environment description: GENERATED environment refs become STALE if fingerprint mismatches
- USER_UPLOAD refs are never automatically staled by text changes

### Reference Selection

`ReferenceSelector.select()` is subject-scoped: selects one eligible ref per shot subject's `character_id`. Both preview and generation use the same per-subject selection logic.

### Reference Prompt Visibility (P4)

- `GET /characters/{id}/reference-prompt-preview`: returns default prompt + negative before generation
- `GET /projects/{id}/environment-reference-prompt-preview`: returns default env prompt + negative
- `GET /reference-generation-requests/{id}`: returns stored prompt/negative after generation
- Both character and environment generation accept `prompt_override` and `negative_prompt_override`

---

## 7. H3 Image-Pack Production Path

### Provider Boundary

Everything in this section is below the canonical product layer. H3-specific types (`H3PromptV1`, `H3ReferenceBinding`, `H3PromptBuilder`) are correctly isolated per ADR-005. Provider-specific workflow definitions, frame grids, and slot semantics do not define product limits.

**Known technical debt:** `routes.py` currently imports H3 types directly and contains hardcoded H3 workflow IDs and resolution values. This leakage is acknowledged (PD-9) and will be cleaned up when the Generation API is consolidated.

### Workflow Source of Truth

Real locally-verified ComfyUI workflows are copied into `workflows/source_reference/minimax_h3/` as byte-identical evidence fixtures. LFDirector's API-format templates in `workflows/h3/` are derived from these.

The external ComfyUI installation (`D:\ComfyUI\`) is READ-ONLY and must never be modified by LFDirector development.

### Image-Pack Conditioning Contract

**Workflow:** `h3_r2v_image_pack_v1` (4 materialized slots, nodes 200-203)

| Slot | Content | When |
|------|---------|------|
| Picture 1 | Primary visible character (CHARACTER_BODY) | Always |
| Picture 2 | Environment (ENVIRONMENT) | Always |
| Picture 3 | Predecessor continuity frame | Downstream shots only |
| Picture 4 | Second visible character | 2-character shots |

**Note:** The 4-slot limit is a property of this specific H3 workflow template, not a product constraint. H3's `MiniMaxH3ReferenceToVideo` node supports up to 9 image references. Workflow templates with more slots can be created.

### Workflow Selection

```
if approved ENVIRONMENT ref exists → h3_r2v_image_pack_v1
elif downstream (has predecessor) → h3_flf_v1 (legacy fallback)
else → h3_r2v_v1 (legacy fallback)
```

---

## 8. Durable Generation Lifecycle (P3)

### UI → Queue → Worker → ComfyUI → Take

```
Operator clicks Generate
  → POST /shots/{id}/generate (returns 202 + job_id)
  → QueueService.enqueue_shot() creates persistent QueueJob
  → Embedded QueueWorker (daemon thread) claims job
  → GenerationService.generate_take() submits to ComfyUI
  → Worker monitors via WebSocket
  → On completion: finalize_from_result() downloads, validates, persists Take
  → QueueJob atomically updated to succeeded with take_id
  → UI poll discovers completion, reloads Takes
```

### Timeout Semantics

The `FILM_COMFYUI_GENERATION_TIMEOUT` (default 1200s) is a monitoring liveness safeguard. When monitoring times out:
1. `generate_take()` marks the GenerationRequest as `failed`
2. QueueWorker catches the timeout and leaves the QueueJob as `claimed` (not `failed`)
3. On next poll cycle, `recover()` finds the claimed job
4. Recovery checks ComfyUI history for the prompt_id
5. If ComfyUI completed successfully, `finalize_from_result()` persists the Take
6. If ComfyUI is still running, job stays `claimed` for next cycle

**Correctness does not depend on H3 finishing within the timeout.**

### Recovery on Restart

The embedded worker calls `recover()` on startup, resolving all orphaned `claimed` jobs against ComfyUI history using the 12-state recovery matrix. Failed requests with a `comfyui_prompt_id` are also checked (State 12b: timeout recovery).

### Duplicate Protection

`QueueService.has_active_jobs(shot_id)` prevents concurrent generation for the same shot. The API returns 409 if pending/claimed jobs exist. The UI disables the Generate button during active generation.

---

## 9. Continuity System

Per-shot continuity chain within scenes. In the image-pack path, the predecessor's approved Take's last frame is uploaded as Picture 3. ContinuityState tracks upstream Take provenance. Replace-approved triggers downstream invalidation.

Current baseline: frame-level continuity only. Semantic continuity (character state, prop state, narrative state per ORIGINAL_SPEC §27) is a future capability.

Legacy FLF path (`h3_flf_v1`) remains available as fallback when no environment ref exists.

---

## 10. ComfyUI Integration

| Aspect | Detail |
|---|---|
| Connection | REST/WebSocket, synchronous monitoring |
| Timeout | 1200 seconds (configurable via `FILM_COMFYUI_GENERATION_TIMEOUT`) |
| Recovery | If monitoring times out but ComfyUI completes, recovery finalizes the output |
| Error propagation | HTTP errors extract `node_errors` with class_type and validation messages |
| Image upload | `POST /upload/image` before workflow submission |
| Media storage | Managed under `storage/` with path confinement |

---

## 11. Operator Console

Single-file HTML/JS application (`src/film_director/ui/static/index.html`):
- Project selection with localStorage persistence
- Shot selection with localStorage persistence
- Shot plan editor (add/delete/reorder/edit)
- Reference Manager (generate/upload/approve/reject/archive/pin)
- Editable character definitions and environment description
- Generation preview with per-subject reference binding
- Async generation with queue status polling and page-refresh discovery
- Duplicate generation protection (disabled button + 409)
- Activity event log
- Scene assembly

---

## 12. Key Technical Facts

| Fact | Value |
|---|---|
| Python | 3.14 |
| Framework | FastAPI |
| Database | SQLite (WAL mode, FK enforcement) |
| GPU | NVIDIA RTX 5090, 32GB VRAM |
| ComfyUI | REST+WebSocket |
| FPS | 24 (fixed, H3 model constraint) |
| Output | MP4 (H264 + AAC muxed) |
| Image-pack generation time | ~7-15 min typical on RTX 5090 |

---

## 13. Architecture Decisions (Frozen)

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Hybrid Wind Comic Sidecar Architecture | Frozen. Output-quality revisit condition demonstrated. |
| ADR-002 | Canonical Production Specification independent of Wind Comic | Frozen. Reinforced by PD-5 (LFDirector owns pre-production artifacts). |
| ADR-003 | ComfyUI runtime via REST/WebSocket API only | Frozen. |
| ADR-004 | ComfyUI MCP as development tool only | Frozen. |
| ADR-005 | Provider-specific generation artifacts separated from canonical model | Frozen. |
