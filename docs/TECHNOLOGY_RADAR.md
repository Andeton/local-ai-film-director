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

**P3 full production acceptance (2026-08-20):**
- Shot 1: 2/4 inputs (char + env). APPROVED
- Shot 2: 3/4 inputs (char + env + continuity). APPROVED
- Shot 3: 3/4 inputs (different char + env + continuity). APPROVED
- Shot 4: 4/4 inputs (char1 + env + continuity + char2). APPROVED
- Shot 5: 3/4 inputs (char + env + continuity). APPROVED
- Shot 6: 4/4 inputs (char1 + env + continuity + char2). APPROVED

Assembled scene: 48.741s, 1376x768, 6 shots. HUMAN PASS.

### Generation Duration Evidence (RTX 5090)

| Shot | Inputs | Video Duration | ComfyUI Render Time | Source |
|------|--------|---------------|---------------------|--------|
| 1 | 2 | 7.0s | ~7 min | DB timestamps (submit→complete) |
| 2 | 3 | 6.0s | ~6 min | DB timestamps |
| 3 | 3 | 8.0s | ~7-19 min | DB timestamps (timed out at 600s, recovered) |
| 4 | 4 | 7.0s | ~8 min | DB timestamps |
| 5 | 3 | 9.0s | 12.2 min | ComfyUI execution timestamps |
| 6 | 4 | 10.0s | **73.8 min** | ComfyUI execution timestamps |

Shot 6 anomaly: 73.8 min actual render — ~6x longer than typical. ComfyUI completed successfully with no execution error. This is a single observation. No cause established. The render quality was visually acceptable and approved.

Typical render range (excluding Shot 6): **6-13 minutes** on RTX 5090.

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
- Spontaneous dialogue audio observed in multiple shots without explicit prompt control
- `audio_intent.ambient` maps to `[Overall Soundscape]` prompt section (P4: now operator-editable)
- `audio_intent.music` maps to `[Non-Diegetic Music]` prompt section (P4: now operator-editable)
- Dialogue/audio controllability is NOT established — observation only

### Reference Generation Prompts (P4)

- Character prompt: `"A character reference photo of {name}. {appearance}. {pose}, neutral studio lighting..."`
- Environment prompt: `"A single continuous cinematic production design reference photograph of: {description}..."`
- Default negatives are generic (no project-specific terms — P3 contamination removed in P4.2a)
- Both prompt and negative_prompt are operator-editable before generation
- Both are persisted in `ReferenceGenerationRequest` and inspectable afterward
- Environment references now tracked via proper `ReferenceGenerationRequest` (real `rgreq_` ID)

---

## Candidate Solutions

| Solution | Status |
|---|---|
| LTX-2.3 Ingredients | DEFERRED FALLBACK |
| SkyReels V3 | CANDIDATE |
| Wan VACE | CANDIDATE |
| SCAIL-2 | CANDIDATE |
| Audio/dialogue control | NEEDS INVESTIGATION |
| H3 prompt compilation | DEMONSTRATED GAP — highest priority |

---

## Key Compatibility Notes

- H3 R2V and FLF use different UNETs (ref2va vs fl2va) — cannot be mixed
- H3 frame grid: 17k+5 (valid: 124, 141, ..., 362)
- H3 aspect: `"16:9 (Widescreen)"`, `"9:16 (Portrait Widescreen)"`, etc.
- Generation timeout: 1200s default (monitoring liveness safeguard only)
- Timeout does not determine correctness — recovery finalizes completed renders
- Completed renders recoverable via embedded worker recovery or `finalize_from_result()`

---

## H3 Node Contracts

### MiniMaxH3ReferenceToVideo (R2V)
- Inputs: clip, vae, audio_vae, prompt, ref_images (autogrow), width, height, length, ref_image_size
- Max 9 image refs, 3 video refs, 3 audio refs
- Prompt tags: `<Picture i>`, `<Video k>`, `<Audio j>`

### MiniMaxH3ImageToVideo (FLF)
- Inputs: clip, vae (NO audio_vae), prompt, width, height, length, first_frame, last_frame
- NO ref_images input — mutually exclusive with R2V path
