# Technology Radar — Local AI Film Director

Verified workflows, model capabilities, and candidate solutions.

---

## Production Verified

### H3 R2V v1 (1-reference)

| Property | Value |
|---|---|
| Workflow ID | h3_r2v_v1 |
| Version | 1.0.0 |
| Fingerprint | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` |
| Strategy | REFERENCE_TO_VIDEO |
| Node class | MiniMaxH3ReferenceToVideo |
| UNET | minimax_h3_ref2va_pruned_int8_convrot.safetensors |
| Materialized ref slots | 1 |
| Max image refs | 9 |
| Prompt tags | `<Picture N>`, `<Subject N>` |
| Live acceptance | M3 — prompt `d4e450d0`, 1376x768 H264+AAC, ~4 min |

### H3 R2V v2 (2-reference)

| Property | Value |
|---|---|
| Workflow ID | h3_r2v_v2 |
| Version | 2.0.0 |
| Fingerprint | `b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9` |
| Strategy | REFERENCE_TO_VIDEO |
| Materialized ref slots | 2 |
| Live acceptance | M5 — character reference influence verified |

### H3 FLF v1 (first-frame continuity)

| Property | Value |
|---|---|
| Workflow ID | h3_flf_v1 |
| Version | 1.0.0 |
| Fingerprint | `47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6` |
| Strategy | FIRST_LAST_FRAME |
| Node class | MiniMaxH3ImageToVideo |
| UNET | minimax_h3_fl2va_pruned_int8_convrot.safetensors |
| Ref slots | 0 (NO ref_images input) |
| Continuity | first_frame input for predecessor last frame |
| Live acceptance | M7.F — conditional acceptance |

**Critical limitation:** FLF has NO ref_images input. Character identity relies entirely on first_frame pixels + text prompt. Text-only identity is NOT a universal solution — back-facing predecessor frames lose face identity.

**Audio finding:** fl2va produces joint video+audio latents. audio_vae needed only at decoder stage (VAEDecodeAudio node 23).

### Z-Image Turbo v1 (reference generation)

| Property | Value |
|---|---|
| Purpose | Character reference image generation |
| Model | Z-Image Turbo LoRA |
| Live acceptance | M5 — reference influence confirmed |

### Krea 2 Turbo v1 (reference generation)

| Property | Value |
|---|---|
| Purpose | Character reference image generation |
| Model | Krea 2 Turbo |
| Live acceptance | M5 — reference influence confirmed |

### H3 R2V Image Pack v1 (3-4 reference, identity+environment+continuity)

| Property | Value |
|---|---|
| Workflow ID | h3_r2v_image_pack_v1 |
| Version | 1.0.0 |
| Fingerprint | `32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351` |
| Strategy | REFERENCE_TO_VIDEO |
| UNET | minimax_h3_ref2va_pruned_int8_convrot.safetensors |
| Materialized ref slots | 4 |
| Picture 1 | Character identity (required) |
| Picture 2 | Environment view (required) |
| Picture 3 | Predecessor continuity frame (required) |
| Picture 4 | Prop reference (optional) |
| ref_video | None |
| ref_audio | None |
| Live acceptance | M7.G.C — prompt `1f05a478`, 287s, HUMAN PASS |
| Output | 1376x768, 124 frames, 5.167s, H264+AAC |

**Selected current production continuity strategy.** Solves M7.F identity/environment drift by providing explicit visual anchors via Pictures 1-3 while maintaining the proven R2V execution profile (~4-7 min per shot).

---

## Technically Verified (Not Selected for Production)

### H3 R2V Full-Video Hybrid

Full-video reference (124-frame ref_video + ref_image + audio) exceeded 120 minutes on RTX 5090. **Impractical in this configuration.** Image-only R2V remains viable.

---

## Candidate Solutions

| Solution | Source | Capability | Status |
|---|---|---|---|
| **LTX-2.3 Ingredients** | Lightricks official IC-LoRA | Multi-reference sheet, 768x448, 121 frames | DEFERRED FALLBACK |
| **SkyReels V3** | SkyworkAI (official, local) | 1-4 ref images, character/object/background | CANDIDATE |
| **Wan VACE** | Official ComfyUI workflow | Structural/motion guidance | CANDIDATE |
| **SCAIL-2** | zai-org (official, local) | Character replacement, front/back/close-up refs | CANDIDATE |
| **MV-Adapter** | huanngzh (official) | Multi-view image generation | CANDIDATE |
| **HunyuanWorld 1.0** | Tencent (official) | 360/3D world generation | CANDIDATE |

### Remote Partner Nodes (Optional, Paid)

| Solution | Notes |
|---|---|
| Seedance 2.0/2.5 | Subject + scene reference, large multimodal sets |
| Kling 3.0 | Subject consistency, multi-shot workflows |

---

## Deferred

| Solution | Reason |
|---|---|
| HY-World 2.0 | No stable ComfyUI integration |
| H3 Turbo LoRA | Not installed; install when generation speed matters |
| LTX-2.5 | LTX-2.3 Ingredients LoRA NOT compatible with 2.5 |

---

## Key Compatibility Notes

- LTX-2.3 Ingredients LoRA is NOT automatically compatible with LTX-2.5
- Remote Partner Nodes require Comfy account/API credits — never required by local core
- H3 R2V and FLF use different UNETs (ref2va vs fl2va) — they cannot be mixed in one workflow
- H3 frame grid: 17k+5 (valid: 124, 141, ..., 362). Duration formula: `max(5, round(s*24)) + (5 - (max(5, round(s*24)) % 17)) % 17`
- H3 aspect values: `"16:9 (Widescreen)"`, `"9:16 (Portrait Widescreen)"`, etc.

---

## H3 Node Contracts

### MiniMaxH3ReferenceToVideo (R2V)
- Inputs: clip, vae, audio_vae, prompt, ref_images (autogrow), width, height, length, ref_image_size
- Max 9 image refs, 3 video refs, 3 audio refs
- Prompt tags: `<Picture i>`, `<Video k>`, `<Audio j>`

### MiniMaxH3ImageToVideo (FLF)
- Inputs: clip, vae (NO audio_vae), prompt, width, height, length, first_frame, last_frame
- NO ref_images input — mutually exclusive with R2V path
