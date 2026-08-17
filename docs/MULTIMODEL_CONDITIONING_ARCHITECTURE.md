# Multi-Model Conditioning Architecture

**Date:** 2026-08-17
**Status:** DRAFT — architectural decision, not yet implemented
**Supersedes:** None (new)
**Related:** ADR-005 (provider-specific artifacts), M7 continuity plan

---

## 1. Motivation

M7.F live acceptance proved that:

1. FLF (MiniMaxH3ImageToVideo) provides exact temporal boundary continuity via `first_frame` but has NO `ref_images` input — character identity relies entirely on the predecessor frame pixels + text prompt.
2. Text-only identity prompting produced conditionally acceptable results but is NOT a universal identity solution, especially when the predecessor frame hides the face.
3. H3 R2V (MiniMaxH3ReferenceToVideo) remains an active strategy with proven image-based identity anchoring.
4. The specific full-video hybrid configuration (124-frame ref_video + ref_image + audio + 5s 1MP output) exceeded 120 minutes on RTX 5090 and was interrupted — that specific configuration is impractical.
5. H3 R2V with image references only (no ref_video, no ref_audio) remains viable and untested for continuity.
6. Multiple other models (LTX, SkyReels, Wan VACE, SCAIL-2) offer different reference capabilities that may solve identity+continuity more effectively.

---

## 2. Principles

### A. Canonical Layer Remains Model-Agnostic

No H3-, LTX-, SkyReels-, Wan-, Seedance-, Kling-, or other provider-specific fields in canonical Shot, CharacterReference, or GenerationPlan models. Provider-specific artifacts remain separated per ADR-005.

### B. Three Architectural Concepts

#### 1. VisualAssetPack

A project-level collection of durable, versioned visual assets with provenance and SHA-256.

**Proposed roles:**

| Role | Purpose |
|---|---|
| CHARACTER_FACE_CLOSEUP | Face identity anchor |
| CHARACTER_BODY_FRONT | Full-body front reference |
| CHARACTER_BODY_SIDE | Side profile reference |
| CHARACTER_BODY_BACK | Back view reference |
| CHARACTER_TURNAROUND | Multi-view turnaround sheet |
| CHARACTER_WARDROBE | Clothing/costume reference |
| ENVIRONMENT_MASTER | Primary environment reference |
| ENVIRONMENT_VIEW | Shot-specific environment view |
| ENVIRONMENT_PANORAMA_360 | 360° panorama source |
| ENVIRONMENT_DEPTH | Depth map for environment |
| ENVIRONMENT_LAYOUT | Spatial layout reference |
| PROP_REFERENCE | Important prop image |
| PROP_TURNAROUND | Prop multi-view |
| STYLE_REFERENCE | Visual style anchor |
| CONTINUITY_FRAME | Predecessor final frame |
| MOTION_REFERENCE | Motion/action reference clip |
| CONTROL_VIDEO | Structural control video |
| AUDIO_REFERENCE | Audio reference clip |

Builds on existing ReferenceAsset provenance, status, pinning, managed storage, deterministic selection, and immutable GenerationRequest snapshots.

#### 2. ConditioningRecipe

A versioned model-specific mapping from canonical asset roles to workflow inputs.

**Example: `h3_r2v_image_pack_v1`**
```
character identity      → Picture 1 (ref_image_0)
environment view        → Picture 2 (ref_image_1)
predecessor final frame → Picture 3 (ref_image_2)
important prop          → Picture 4 (ref_image_3)  [optional]
full reference video    → disabled
reference audio         → disabled
```

Each recipe defines: supported task types, required/optional asset roles, slot ordering, maximum references, image/video/audio support, resolution/frame constraints, prompt-tag convention, fallback behavior, expected VRAM/runtime class, workflow definition ID/version/fingerprint.

#### 3. CapabilityRegistry

Each generator/provider profile declares: provider and model family, local or remote execution, supported strategies, supported reference modalities, reference limits, Windows/ComfyUI compatibility, required models/nodes, official workflow/source URL, license/access restrictions, measured VRAM/runtime, installation state, verification state, approval state.

**Lifecycle:** `DISCOVERED → AVAILABLE → INSTALLED → VERIFIED → APPROVED → DEPRECATED`

New solutions must not automatically replace approved production workflows.

---

## 3. Strategy Routing

| Scenario | Recommended Approach |
|---|---|
| Continuous moment / exact temporal handoff | FLF with predecessor final frame |
| New angle in the same environment | Character + shot-specific environment view + predecessor final frame (R2V image-only) |
| New scene or location | Character pack + new environment pack + relevant props |
| Exact driven motion | VACE or SCAIL-2 style control workflow |
| Complex recurring characters/props/location | LTX Ingredients or SkyReels multi-reference |
| Persistent free camera movement | Derive shot-specific environment views from panorama or persistent 3D world |
| Dialogue/audio | Use explicit audio-capable recipe only when audio is requested |

---

## 4. M7.F A/B Experiment Results

### Approach A — FLF + Strengthened Identity Text

| Test | Runtime | Result |
|---|---|---|
| A-Shot2 (face-visible predecessor) | 434s (~7 min) | Complete |
| A-Shot4 (back-facing predecessor) | 240s (~4 min) | Complete |

**Human verdict:** Conditional acceptance ("условно ок, двигаемся дальше").

### Approach B — R2V Full-Video Hybrid

| Test | Runtime | Result |
|---|---|---|
| B-Shot2 (ref_video + ref_image + audio) | >120 min | Interrupted (impractical) |
| B-Shot4 | Cancelled (blocked) | N/A |

**Finding:** The specific configuration with full 124-frame predecessor video + audio as ref_video is computationally prohibitive on RTX 5090. This does NOT invalidate:
- H3 R2V with image references only
- H3 R2V with reduced-frame video reference
- Other multi-reference strategies

### A/B Prompt IDs

| Test | Prompt ID |
|---|---|
| A-Shot2 | `061efe0a-8a5b-462f-b752-297719bac169` |
| B-Shot2 | `86204083-8b40-48bf-9690-c6385e40d2d8` (interrupted) |
| A-Shot4 | `629124e9-4427-434c-92fc-4c35bef4f390` |
| B-Shot4 | `c205e261-b8da-42cc-af7c-5b222e8adcdd` (cancelled) |

### Evidence Paths (gitignored)

- `.preflight/m7f_ab/outputs/` — A-Shot2.mp4, A-Shot4.mp4
- `.preflight/m7f_ab/frames/` — extracted diagnostic frames
- `.preflight/m7f_ab/inputs/` — copied source media
- `.preflight/m7f/` — original M7.F five-shot evidence

---

## 5. Next H3 Recipe (Proposed)

**`h3_r2v_image_pack_v1`** — image references only, no video, no audio:

- Picture 1: character identity (approved ReferenceAsset)
- Picture 2: shot-specific environment view
- Picture 3: predecessor final frame
- Picture 4: important prop (when required)
- No full predecessor video
- No predecessor audio

This stays within the proven R2V timing profile (~4-7 min per shot) while adding environment and identity anchoring that FLF cannot provide.

---

## 6. Official Sources

### Local / Official Open-Source

| Solution | Source | Status |
|---|---|---|
| SkyReels V3 | https://github.com/SkyworkAI/SkyReels-V3 | Official local inference, 1-4 ref images |
| LTX-2.3 Ingredients | https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients | Official IC-LoRA, 768×448, 121 frames |
| LTX ComfyUI | https://github.com/Lightricks/ComfyUI-LTXVideo | Official ComfyUI integration |
| LTX-2/2.5 | https://github.com/Lightricks/LTX-2 | Official model repo |
| Wan VACE | https://comfy.org/workflows/video_wan_vace_14B_ref2v-83ff3768d42b/ | Official ComfyUI workflow |
| SCAIL-2 | https://github.com/zai-org/SCAIL-2 | Character replacement, ComfyUI integration |
| MV-Adapter | https://github.com/huanngzh/MV-Adapter | Multi-view image generation |
| HunyuanWorld 1.0 | https://github.com/Tencent-Hunyuan/HunyuanWorld-1.0 | 360°/3D world generation |
| HY-World 2.0 | https://github.com/Tencent-Hunyuan/HY-World-2.0 | No stable ComfyUI integration yet |

### Remote Partner Nodes (Optional, Paid)

| Solution | Source | Notes |
|---|---|---|
| Seedance 2.0 | https://docs.comfy.org/tutorials/partner-nodes/bytedance/seedance-2-0 | Subject + scene reference |
| Seedance 2.5 | https://docs.comfy.org/tutorials/partner-nodes/bytedance/seedance-2-5 | Large multimodal reference sets |
| Kling 3.0 | https://docs.comfy.org/tutorials/partner-nodes/kling/kling-3-0 | Subject consistency, multi-shot |

### Key Compatibility Notes

- LTX-2.3 Ingredients LoRA is NOT automatically compatible with LTX-2.5
- HY-World 2.0 lacks stable official ComfyUI integration — do not block implementation on it
- Remote Partner Nodes require Comfy account/API credits — never required by local core
