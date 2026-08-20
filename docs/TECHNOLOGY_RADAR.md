# Technology Radar — Local AI Film Director

Verified workflows, model capabilities, and candidate solutions.

**Last updated:** 2026-08-20

---

## Production Verified

### H3 R2V Image Pack v1 — SELECTED PRODUCTION PATH

| Property | Value |
|---|---|
| Workflow ID | h3_r2v_image_pack_v1 |
| Version | 1.0.0 |
| Fingerprint | `32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351` |
| Strategy | REFERENCE_TO_VIDEO |
| UNET | minimax_h3_ref2va_pruned_int8_convrot.safetensors |
| Materialized ref slots | 4 |
| Picture 1 | Primary visible character (CHARACTER_BODY) |
| Picture 2 | Environment (ENVIRONMENT) |
| Picture 3 | Predecessor continuity frame (downstream) |
| Picture 4 | Second visible character (when required) |
| Unused slots | Pruned from workflow JSON before submission |
| Generation time | ~10-15 min per shot on RTX 5090 |

**P3 live acceptance (2026-08-20):**
- Shot 1: 2/4 inputs (char + env). APPROVED
- Shot 2: 3/4 inputs (char + env + continuity). APPROVED
- Shot 3: 3/4 inputs (different char + env + continuity). APPROVED
- Shot 4: 4/4 inputs (char1 + env + continuity + char2). Visually good

**Slot pruning:** Unused LoadImage nodes and `ref_images.ref_image_N` connections are removed before ComfyUI submission. Matches real workflow behavior where unused inputs have `link=null`.

### Real Workflow Source Reference

Real locally-verified ComfyUI user workflows are copied into `workflows/source_reference/minimax_h3/` as byte-identical evidence fixtures. The external ComfyUI installation (`D:\ComfyUI\`) is READ-ONLY.

- `video_minimax_h3_r2v.json` — SHA-256: `b6224a53...`
- `video_minimax_h3_i2v.json` — SHA-256: `b9f11d82...`

### OpenRouter — Planning/Enrichment Provider

| Property | Value |
|---|---|
| Purpose | Shot planning, character enrichment, environment derivation |
| Default model | google/gemini-2.5-flash |
| Config | `OPENROUTER_API_KEY` (bare env var, no FILM_ prefix) |
| Model configurable | `FILM_OPENROUTER_MODEL` |
| Live validation | All planning for proj_cfb89b04f3c8 |

### Ollama — Local LLM (Legacy)

| Property | Value |
|---|---|
| Purpose | Legacy beat/coverage enrichment chain |
| Config | `FILM_LLM_PROVIDER=ollama`, `FILM_LLM_MODEL=qwen3:14b` |
| Status | Available as fallback when OpenRouter is not configured |

### Z-Image Turbo v1

| Property | Value |
|---|---|
| Purpose | Character and environment reference image generation |
| Model | z_image_turbo_bf16.safetensors |
| Resolution | 1024x1024 (fixed in frozen template) |
| Live acceptance | Character refs and environment ref for proj_cfb89b04f3c8 |

### H3 R2V v1 / v2 (Legacy)

| Workflow | Slots | Status |
|---|---|---|
| h3_r2v_v1 | 1 | Legacy fallback (no environment ref) |
| h3_r2v_v2 | 2 | Legacy fallback |

### H3 FLF v1 (Legacy)

| Property | Value |
|---|---|
| Status | Legacy fallback (no environment ref available) |
| Limitation | NO ref_images input — identity via first_frame pixels only |
| UNET | minimax_h3_fl2va_pruned_int8_convrot.safetensors |

### H3 Audio Observations

- H3 generates joint video+audio in a single forward pass
- Stereo audio (voice, SFX, music) is native, not layered
- Shot 4: H3 spontaneously generated simple dialogue audio without explicit prompt control
- Dialogue/audio controllability is NOT established — observation only

---

## Candidate Solutions

| Solution | Status |
|---|---|
| LTX-2.3 Ingredients | DEFERRED FALLBACK |
| SkyReels V3 | CANDIDATE |
| Wan VACE | CANDIDATE |
| SCAIL-2 | CANDIDATE |
| Audio/dialogue control | NEEDS INVESTIGATION |

---

## Key Compatibility Notes

- H3 R2V and FLF use different UNETs (ref2va vs fl2va) — cannot be mixed
- H3 frame grid: 17k+5 (valid: 124, 141, ..., 362)
- H3 aspect: `"16:9 (Widescreen)"`, `"9:16 (Portrait Widescreen)"`, etc.
- Generation timeout: 1200s default (configurable via `FILM_COMFYUI_GENERATION_TIMEOUT`)
- Completed renders recoverable via `finalize_from_result()` if monitoring times out

---

## H3 Node Contracts

### MiniMaxH3ReferenceToVideo (R2V)
- Inputs: clip, vae, audio_vae, prompt, ref_images (autogrow), width, height, length, ref_image_size
- Max 9 image refs, 3 video refs, 3 audio refs
- Prompt tags: `<Picture i>`, `<Video k>`, `<Audio j>`

### MiniMaxH3ImageToVideo (FLF)
- Inputs: clip, vae (NO audio_vae), prompt, width, height, length, first_frame, last_frame
- NO ref_images input — mutually exclusive with R2V path
