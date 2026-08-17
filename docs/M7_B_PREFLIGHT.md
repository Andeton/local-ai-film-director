# M7.B Technical Preflight — FLF Continuity Workflow

**Date:** 2026-08-17
**Prompt ID:** `e8f2ba02-1127-4217-8a7d-eb889cbdaf2c`
**ComfyUI:** v0.33.1, http://127.0.0.1:8188, RTX 5090

---

## Live H3 Node Contracts

### MiniMaxH3ImageToVideo (FLF)

| Input | Type | Required | Notes |
|---|---|---|---|
| clip | CLIP | YES | |
| vae | VAE | YES | Video VAE only — NO audio_vae |
| prompt | STRING | YES | Multiline |
| width | INT | YES | Default 1344, step 32 |
| height | INT | YES | Default 768, step 32 |
| length | INT | YES | Frame count, 17k+5 grid, trained 124-362 |
| first_frame | IMAGE | NO | Optional start/continuity frame |
| last_frame | IMAGE | NO | Optional end frame |

**Outputs:** CONDITIONING (positive), LATENT

**Critical limitation:** NO `ref_images` input. Character identity must propagate through the first_frame. FLF and R2V character references are mutually exclusive node paths.

### MiniMaxH3ReferenceToVideo (R2V) — unchanged

| Input | Type | Required | Notes |
|---|---|---|---|
| clip | CLIP | YES | |
| vae | VAE | YES | Video VAE |
| audio_vae | VAE | YES | Audio VAE — required for R2V, not for FLF |
| prompt | STRING | YES | |
| width, height, length | INT | YES | Same grid |
| ref_image_size | COMBO | YES | "match" or "max" |
| ref_images | COMFY_AUTOGROW_V3 | NO | 0-9 ref images |
| ref_videos | COMFY_AUTOGROW_V3 | NO | 0-3 ref videos |

### Supporting Nodes

- **EmptyMiniMaxH3LatentAV:** width, height, length → LATENT
- **MiniMaxH3SigmaShift:** model, shift_video, shift_audio → MODEL

---

## Model Filenames

| Model | File |
|---|---|
| FLF UNET | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| R2V UNET | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| CLIP | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| Video VAE | `minimax_h3_video_vae_fp16.safetensors` |
| Audio VAE | `minimax_h3_audio_vae_fp32.safetensors` |

---

## Audio Finding

FLF (`MiniMaxH3ImageToVideo`) does NOT take `audio_vae` as input at the conditioning stage. However, the fl2va model produces joint video+audio latents. The `VAEDecodeAudio` node (23) successfully extracts AAC audio from the sampler output using the audio VAE loaded separately. Audio handling is structurally identical to R2V at the decoder stage — only the conditioning node differs.

---

## Source Media

| Item | Path (M6 worktree) | SHA-256 |
|---|---|---|
| Approved Take 2 last frame | `storage_m6_live/takes/proj_b8b1b8ab40b5/shot60190e252ed4/take_2/last_frame.png` | `ab9b269943cbc906a6a23152e3c681733b46198a3dfae80cf5c87bdac3fc42ab` |
| Approved Take 2 video | `storage_m6_live/takes/proj_b8b1b8ab40b5/shot60190e252ed4/take_2/0b4d7824_00001_.mp4` | `494cd1219accdca322489b6998831861518bf003de6823f34a1211b2d5d36244` |
| Character reference | `storage_m6_live/references/proj_b8b1b8ab40b5/ref_e11cf946d0cb/original.png` | `734dff2744b98953dabb87424a783ffca6eb25a8c95364ea4518cbcaf10ed099` |

Copies placed in `.preflight/m7b/` (gitignored). Copy SHA-256 verified identical to source.

---

## Experimental API Node Bindings

| Parameter | Node ID | Class | Field | Value |
|---|---|---|---|---|
| Conditioning | 104 | MiniMaxH3ImageToVideo | prompt, first_frame, clip, vae, width, height, length | See workflow |
| First frame | 300 | LoadImage | image | `m7b_first_frame.png` |
| UNET | 6 | UNETLoader | unet_name | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| CLIP | 13 | CLIPLoader | clip_name | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| Video VAE | 11 | VAELoader | vae_name | `minimax_h3_video_vae_fp16.safetensors` |
| Audio VAE | 24 | VAELoader | vae_name | `minimax_h3_audio_vae_fp32.safetensors` |
| Seed | 15 | RandomNoise | noise_seed | 700700 |
| Duration | 111 | PrimitiveFloat | value | 5.0 |
| Frame grid | 107 | ComfyMathExpression | expression | `max(5, round(a * 24)) + (5 - ...)` |
| Aspect | 115 | ResolutionSelector | aspect_ratio | `16:9 (Widescreen)` |
| Sampler | 14 | SamplerCustomAdvanced | latent_image from node 104 output 1 |
| Video decode | 10 | VAEDecode | samples from 14, vae from 11 |
| Audio decode | 23 | VAEDecodeAudio | samples from 14, vae from 24 |
| Mux | 91 | CreateVideo | images from 10, audio from 23, fps 24 |
| Output | 92 | SaveVideo | video from 91 |

---

## Execution Evidence

| Field | Value |
|---|---|
| Prompt ID | `e8f2ba02-1127-4217-8a7d-eb889cbdaf2c` |
| Seed | 700700 |
| Runtime | ~225s |
| Resolution | 1376x768 |
| FPS | 24 |
| Frames | 124 |
| Duration | 5.167s |
| Video codec | H.264 |
| Audio codec | AAC (stereo, 32kHz) |
| File size | 785,945 bytes |
| Output SHA-256 | `52f9d629818b3d336ee6ab04adc6c1e9ac3f72dec821d95c1262f54a65b3bab2` |

### Extracted Frames

| Frame | Dimensions | SHA-256 |
|---|---|---|
| Output first frame | 1376x768 | `58a096ae2ac6cd76530cbb8f8ebfe6fc2052f2b1d18e92a62cc02c2bb350ee8d` |
| Output last frame | 1376x768 | `a62ee14399e7fa843f0e9afcc9aa386eb7f01caacd41806fba3b32ce107b7213` |

---

## Non-Mutation Evidence

| Check | Result |
|---|---|
| r2v_v1 SHA-256 | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` ✓ |
| r2v_v2 SHA-256 | `b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9` ✓ |
| Production DB gen_requests | 0 rows |
| Production DB takes | 0 rows |
| Git tracked status | Clean |
| M5 worktree | Clean (untracked data only) |
| M6 worktree | Clean (untracked data only) |

---

## Human Visual Verdict

| Check | Result |
|---|---|
| Video begins from source composition | **PASS** |
| Same character identity | **PASS** |
| Face, suit, proportions, lighting, environment preserved | **PASS** |
| No visible transition jump or scene replacement | **PASS** |
| Motion natural | **PASS** |
| Identity stable through final frame | **PASS** |
| No unwanted speech | **PASS** |
| **Overall M7.B preflight** | **HUMAN PASS** |

---

## Evidence Paths (gitignored .preflight/)

| File | Path |
|---|---|
| Source last frame (copy) | `.preflight/m7b/take2_last_frame.png` |
| Character ref (copy) | `.preflight/m7b/char_ref.png` |
| Experimental workflow | `.preflight/m7b/flf_preflight.json` |
| Generated video | `.preflight/m7b/m7b_flf_output.mp4` |
| Output first frame | `.preflight/m7b/output_first_frame.png` |
| Output last frame | `.preflight/m7b/output_last_frame.png` |
