# Architecture — Local AI Film Director

**Status:** Active
**ADRs:** ADR-001 through ADR-005 (frozen)
**Last updated:** 2026-08-20

---

## 1. System Overview

Local AI Film Director (LFDirector) is an orchestration product that assembles a working AI film production pipeline from ready-made solutions:

- **Wind Comic** — pre-production sidecar (story, script, storyboard, characters). Known limitation: WC's local gemma4 model often produces generic placeholder content; the original project description is the primary creative context.
- **ComfyUI** — execution runtime (REST/WebSocket API). External installation at `D:\ComfyUI\`, treated as READ-ONLY by LFDirector development.
- **MiniMax H3** — video generation model (R2V image-pack, legacy R2V, legacy FLF workflows)
- **Z-Image Turbo / Krea 2 Turbo** — character and environment reference image generation
- **OpenRouter** — planning LLM (shot planning, character enrichment, environment description derivation)
- **Ollama** — local LLM (legacy beat/coverage enrichment chain)
- **LFDirector** — canonical orchestration, Takes, continuity, approval, assembly

LFDirector owns the production pipeline. It does not own pre-production (Wind Comic) or model execution (ComfyUI/H3).

---

## 2. System Boundary Matrix

| Capability | Wind Comic | LFDirector | ComfyUI | H3/Models | OpenRouter | Ollama |
|---|---|---|---|---|---|---|
| Project/story/script/storyboard | OWNER | CONSUMER | — | — | — | — |
| Character design/library | OWNER | CONSUMER | — | — | — | — |
| Shot planning (direct) | — | OWNER | — | — | PROVIDER | — |
| Beat/coverage (legacy) | — | OWNER | — | — | — | PROVIDER |
| Character enrichment | — | OWNER | — | — | PROVIDER | — |
| Environment derivation | — | OWNER | — | — | PROVIDER | — |
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

## 3. Canonical Production Hierarchy

```
ProductionProject (from Wind Comic)
  ├── director_context.description   (original idea / project description)
  ├── director_context.environment_description  (derived stable set/location)
  └── Sequence
        └── Scene
              └── Beat (LLM-enriched dramatic unit)
                    └── ShotSpecificationV1 (coverage shot)
                          ├── GenerationPlan (strategy, requirements)
                          ├── GenerationRequest (immutable execution snapshot)
                          └── Take (generated video + approval state)
```

All canonical entities are **provider/model agnostic** (ADR-005).

---

## 4. Enrichment Architecture

### Enrichment Semantics

"Enrich Missing Data" (`POST /projects/{id}/enrich`) is idempotent:
- Creates shots only if NO current shots exist for the project
- Enriches deficient characters (empty appearance or generic template names like 主角/伙伴)
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

---

## 5. Reference Asset Architecture

### Kinds

| Kind | Ownership | Purpose |
|------|-----------|---------|
| CHARACTER_FACE | character_id | Character face identity |
| CHARACTER_BODY | character_id | Character full-body identity (production-critical) |
| ENVIRONMENT | project-level (no char/shot) | Scene environment/location reference |
| STORYBOARD | shot_id | Per-shot storyboard frame (legacy) |

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

### Reference Generation

Character and environment references are generated via ComfyUI using Z-Image Turbo v1 (default profile). Environment prompts explicitly request empty-set images with no people/characters/action. Environment description is derived from the project idea by the OpenRouter LLM, stripping narrative events.

---

## 6. H3 Image-Pack Production Path

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

**Slot pruning:** Unused slots (LoadImage nodes + `ref_images.ref_image_N` connections) are removed from the workflow JSON before ComfyUI submission. This matches real ComfyUI workflow behavior where unused inputs have `link=null`.

**Overflow:** If required inputs exceed 4 slots, `ParameterResolutionError` is raised — never silently drops a character.

### Workflow Selection

```
if approved ENVIRONMENT ref exists → h3_r2v_image_pack_v1
elif downstream (has predecessor) → h3_flf_v1 (legacy fallback)
else → h3_r2v_v1 (legacy fallback)
```

Preview and execution use the same selection logic.

---

## 7. Continuity System

Per-shot continuity chain within scenes. In the image-pack path, the predecessor's approved Take's last frame is uploaded as Picture 3. ContinuityState tracks upstream Take provenance. Replace-approved triggers downstream invalidation.

Legacy FLF path (`h3_flf_v1`) remains available as fallback when no environment ref exists. FLF has NO ref_images input — identity propagates through first_frame pixels + text prompt only.

---

## 8. ComfyUI Integration

| Aspect | Detail |
|---|---|
| Connection | REST/WebSocket, synchronous monitoring |
| Timeout | 1200 seconds (configurable via `FILM_COMFYUI_GENERATION_TIMEOUT`) |
| Recovery | If monitoring times out but ComfyUI completes, `finalize_from_result()` can recover the output from ComfyUI history |
| Error propagation | HTTP errors extract `node_errors` with class_type and validation messages |
| Image upload | `POST /upload/image` before workflow submission |
| Media storage | Managed under `storage/` with path confinement |

---

## 9. Operator Console

Single-file HTML/JS application (`src/film_director/ui/static/index.html`):
- Project selection with localStorage persistence (`film_director.selected_project_id`)
- Shot selection with localStorage persistence (`film_director.selected_shot_id`)
- Shot plan editor (add/delete/reorder/edit)
- Reference Manager (generate/upload/approve/reject/archive/pin)
- Editable character definitions and environment description
- Generation preview with per-subject reference binding
- Activity event log
- Scene assembly

---

## 10. Key Technical Facts

| Fact | Value |
|---|---|
| Python | 3.14 |
| Framework | FastAPI |
| Database | SQLite (WAL mode, FK enforcement) |
| GPU | NVIDIA RTX 5090, 32GB VRAM |
| ComfyUI | REST+WebSocket |
| H3 R2V UNET | minimax_h3_ref2va_pruned_int8_convrot.safetensors |
| H3 FLF UNET | minimax_h3_fl2va_pruned_int8_convrot.safetensors |
| Text encoder | qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors |
| Video/Audio VAE | minimax_h3_video_vae_fp16 / audio_vae_fp32 |
| FPS | 24 (fixed) |
| Frame grid | 17k+5 |
| Output | MP4 (H264 + AAC muxed) |
| Output resolution (16:9) | 1376x768 |
| Image-pack generation time | ~10-15 min on RTX 5090 |

---

## 11. Architecture Decisions (Frozen)

| ADR | Decision |
|---|---|
| ADR-001 | Hybrid Wind Comic Sidecar Architecture |
| ADR-002 | Canonical Production Specification independent of Wind Comic |
| ADR-003 | ComfyUI runtime via REST/WebSocket API only |
| ADR-004 | ComfyUI MCP as development tool only |
| ADR-005 | Provider-specific generation artifacts separated from canonical model |
