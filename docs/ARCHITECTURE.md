# Architecture — Local AI Film Director

**Status:** Active  
**ADRs:** ADR-001 through ADR-005 (frozen)

---

## 1. System Overview

Local AI Film Director (LFDirector) is an orchestration product that assembles a working AI film production pipeline from ready-made solutions:

- **Wind Comic** — pre-production sidecar (story, script, storyboard, characters)
- **ComfyUI** — execution runtime (REST/WebSocket API)
- **MiniMax H3** — video generation model (R2V, FLF workflows)
- **Z-Image Turbo / Krea 2 Turbo** — character reference image generation
- **Ollama** — LLM enrichment (beat decomposition, coverage planning)
- **LFDirector** — canonical orchestration, Takes, continuity, approval, assembly

LFDirector owns the production pipeline. It does not own pre-production (Wind Comic) or model execution (ComfyUI/H3).

---

## 2. System Boundary Matrix

| Capability | Wind Comic | LFDirector | ComfyUI | H3/Models | Ollama |
|---|---|---|---|---|---|
| Project/story/script/storyboard | OWNER | CONSUMER | — | — | — |
| Character design/library | OWNER | CONSUMER | — | — | — |
| Beat decomposition | — | OWNER | — | — | PROVIDER |
| Coverage planning | — | OWNER | — | — | PROVIDER |
| Shot specification | — | OWNER | — | — | — |
| Generation strategy | — | OWNER | — | — | — |
| Prompt building | — | OWNER | — | — | — |
| Workflow registry | — | OWNER | — | — | — |
| Workflow execution | — | CONSUMER | OWNER | — | — |
| Video generation | — | — | CONSUMER | OWNER | — |
| Reference generation | — | OWNER | CONSUMER | CONSUMER | — |
| Take management | — | OWNER | — | — | — |
| Continuity chain | — | OWNER | — | — | — |
| Queue/scheduling | — | OWNER | — | — | — |
| Human review | — | OWNER | — | — | — |

---

## 3. Canonical Production Hierarchy

```
ProductionProject (from Wind Comic)
  └── Sequence
        └── Scene
              └── Beat (LLM-enriched dramatic unit)
                    └── ShotSpecificationV1 (coverage shot)
                          ├── GenerationPlan (strategy, requirements)
                          ├── GenerationRequest (immutable execution snapshot)
                          └── Take (generated video + approval state)
```

All canonical entities are **provider/model agnostic** (ADR-005).

GenerationPlan contains `strategy` (TEXT_TO_VIDEO, IMAGE_TO_VIDEO, REFERENCE_TO_VIDEO, FIRST_LAST_FRAME, MULTI_PANEL) but NO provider, model, workflow, or node-specific fields.

---

## 4. Provider Boundary (ADR-005)

Provider-specific artifacts live in the generation layer, separated from canonical models:

| Canonical (provider-neutral) | Provider layer (H3-specific) |
|---|---|
| GenerationPlan.strategy | WorkflowDefinition (id, version, fingerprint) |
| ReferenceAsset | H3ReferenceBinding (picture_index, slot) |
| ContinuityState | ContinuityBinding (frame path, SHA) |
| AssetRole (semantic) | RecipeRoleRequirement (slot_index, modality) |

---

## 5. Wind Comic Integration

- **Protocol:** JWT authentication + SSE streaming for project creation
- **Data access:** Read-only SQLite adapter for `qfmj.db`
- **Import:** Atomic import with provenance tracking and change detection
- **Source facts:** ShotSourceFacts transport DTO preserves WC storyboard data (camera, lighting, duration, dialogue)
- **Stale propagation:** Source changes cascade through the canonical hierarchy

WC data mapping:
- WC Project → ProductionProject (with provenance)
- WC Scene → Scene (via Sequence)
- WC Character → CharacterReference
- WC Storyboard shot → ShotSourceFacts → ShotSpecificationV1

---

## 6. Reference Asset Architecture

ReferenceAsset lifecycle: CANDIDATE → APPROVED / REJECTED / ARCHIVED

Source state: CURRENT / STALE (independent from approval)

Eligibility: Only APPROVED + CURRENT assets are production-eligible. Pinning does not override lifecycle.

Sources: USER_UPLOAD, WIND_COMIC, GENERATED

Ownership: CHARACTER_FACE/BODY → character_id, STORYBOARD → shot_id

---

## 7. Continuity System

Per-shot continuity chain within scenes:

```
Shot 1 (chain head) → R2V generation
Shot 2 → FLF (first_frame = Shot 1 last frame)
Shot 3 → FLF (first_frame = Shot 2 last frame)
```

ContinuityState tracks upstream Take provenance. Replace-approved triggers deterministic downstream invalidation. Outdated states block generation until rebuilt.

FLF limitation: MiniMaxH3ImageToVideo has NO ref_images input. Character identity propagates through first_frame pixels + text prompt only.

---

## 8. Key Technical Facts

| Fact | Value |
|---|---|
| Python | 3.14 |
| Framework | FastAPI |
| Database | SQLite (WAL mode, FK enforcement) |
| GPU | NVIDIA RTX 5090, 32GB VRAM |
| ComfyUI | 0.33.1, REST+WebSocket |
| H3 R2V UNET | minimax_h3_ref2va_pruned_int8_convrot.safetensors (20GB) |
| H3 FLF UNET | minimax_h3_fl2va_pruned_int8_convrot.safetensors (20GB) |
| Text encoder | qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors (15GB) |
| Video VAE | minimax_h3_video_vae_fp16.safetensors (4.9GB) |
| Audio VAE | minimax_h3_audio_vae_fp32.safetensors (578MB) |
| FPS | 24 (fixed) |
| Frame grid | 17k+5 (trained 124-362 frames) |
| Output | MP4 (H264 video + AAC audio muxed) |
| Output resolution (16:9) | 1376x768 |
| Max image refs (R2V) | 9 |
| R2V generation time (5s) | ~4 min on RTX 5090 |

---

## 9. Persistence Conventions

- UPSERT via `INSERT ... ON CONFLICT(id) DO UPDATE SET`
- ID format: `{prefix}{uuid4().hex[:12]}` (e.g., `ref_a1b2c3d4e5f6`)
- Timestamps: ISO 8601 UTC
- Fingerprints: SHA-256 lowercase hex (64 chars)
- Migrations: Idempotent inline Python (PRAGMA table_info checks)
- All repositories accept optional `conn` parameter for caller-owned transactions

---

## 10. Architecture Decisions (Frozen)

| ADR | Decision |
|---|---|
| ADR-001 | Hybrid Wind Comic Sidecar Architecture |
| ADR-002 | Canonical Production Specification independent of Wind Comic |
| ADR-003 | ComfyUI runtime via REST/WebSocket API only |
| ADR-004 | ComfyUI MCP as development tool only |
| ADR-005 | Provider-specific generation artifacts separated from canonical model |
