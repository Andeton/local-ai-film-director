# M3.A Preflight Report -- H3 R2V Runtime Verification

**Date:** 2026-08-14
**Status:** PASS (with M0 corrections)
**Template:** `workflows/h3/r2v_v1.json`
**SHA-256:** `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4`

---

## 1. Runtime Health

| Property        | Value                                                |
|-----------------|------------------------------------------------------|
| ComfyUI version | 0.33.1                                               |
| Frontend        | 1.48.7                                               |
| Python          | 3.13.12 (MSC v.1944 64-bit AMD64)                   |
| PyTorch         | 2.12.1+cu130                                         |
| GPU             | NVIDIA GeForce RTX 5090                              |
| VRAM total      | 34,190,458,880 (31.8 GiB)                           |
| OS              | win32                                                |
| Deploy env      | local-desktop2-standalone                            |
| Flags           | --highvram --use-sage-attention --fast fp16_accumulation cublas_ops --enable-triton-backend |
| Input dir       | D:\ComfyUI\input                                     |
| Output dir      | D:\ComfyUI\output                                    |

## 2. H3 Node Inventory

All H3-related node classes found via `/object_info`:

| Class Name                                | Category                         | Module                             |
|-------------------------------------------|----------------------------------|------------------------------------|
| MiniMaxH3ReferenceToVideo                 | model/conditioning/minimax       | comfy_extras.nodes_minimax_h3      |
| MiniMaxH3ImageToVideo                     | model/conditioning/minimax       | comfy_extras.nodes_minimax_h3      |
| MiniMaxH3SigmaShift                       | model/conditioning/minimax       | comfy_extras.nodes_minimax_h3      |
| MiniMaxH3MemoryEfficientSageAttentionPatch| model/conditioning/minimax       | comfy_extras.nodes_minimax_h3      |
| EmptyMiniMaxH3LatentAV                    | model/conditioning/minimax       | comfy_extras.nodes_minimax_h3      |
| CRT_MinimaxLength                         | (utility)                        | (custom node)                      |

Legacy/API nodes also present: MinimaxHailuo03*, MinimaxHailuoVideoNode, MinimaxImageToVideoNode, MinimaxTextToVideoNode.

## 3. H3 Models Available

| Type   | Filename                                            |
|--------|-----------------------------------------------------|
| UNET (I2V) | minimax_h3_fl2va_pruned_int8_convrot.safetensors |
| UNET (R2V) | minimax_h3_ref2va_pruned_int8_convrot.safetensors |
| CLIP       | qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors     |
| Video VAE  | minimax_h3_video_vae_fp16.safetensors             |
| Audio VAE  | minimax_h3_audio_vae_fp32.safetensors             |

## 4. MiniMaxH3ReferenceToVideo -- Verified Node Schema

### Required Inputs

| Field           | Type   | Default | Range/Options                        | Notes                                    |
|-----------------|--------|---------|--------------------------------------|------------------------------------------|
| clip            | CLIP   | --      | (link)                               | Must be loaded with type="minimax"       |
| vae             | VAE    | --      | (link)                               | Video VAE                                |
| audio_vae       | VAE    | --      | (link)                               | Audio VAE                                |
| prompt          | STRING | ""      | multiline, dynamicPrompts            | Uses `<Picture i>` / `<Video k>` tags    |
| width           | INT    | 1344    | 32..16384, step 32                   |                                          |
| height          | INT    | 768     | 32..16384, step 32                   |                                          |
| length          | INT    | 124     | 5..3600, step 17                     | Frame count at 24 fps. Grid: 17k+5      |
| ref_image_size  | COMBO  | "match" | ["match", "max"]                     | "max" = 2048px short edge, slower        |

### Optional Inputs (COMFY_AUTOGROW_V3)

| Group           | Prefix          | Min | Max | Type  | Notes                               |
|-----------------|-----------------|-----|-----|-------|--------------------------------------|
| ref_images      | ref_image_      | 0   | 9   | IMAGE | Reference images (up to 9!)          |
| ref_videos      | ref_video_      | 0   | 3   | IMAGE | Reference video frames at 24fps      |
| ref_video_audios| ref_video_audio_| 0   | 3   | AUDIO | Soundtrack per ref video             |
| ref_audios      | ref_audio_      | 0   | 3   | AUDIO | Standalone reference audio           |

### Outputs

| Index | Name     | Type          |
|-------|----------|---------------|
| 0     | positive | CONDITIONING  |
| 1     | LATENT   | LATENT        |

### API Format for Autogrow Inputs

Autogrow slots use dot notation in the API JSON:
```json
"ref_images.ref_image_0": ["200", 0],
"ref_images.ref_image_1": ["201", 0]
```
Where `"200"` is the node ID of a LoadImage node. Verified from the `ComfyMathExpression` autogrow pattern (`values.a`).

## 5. M0 Provisional Mapping Verification

### CRITICAL: M0 mappings are INVALID

The M0 provisional mappings assumed a UI-format workflow with fixed LoadImage nodes for references. The actual R2V node uses a fundamentally different architecture:

| M0 Parameter    | M0 Node/Field                         | ACTUAL                                          | Status      |
|-----------------|---------------------------------------|-------------------------------------------------|-------------|
| prompt          | 138 / PrimitiveStringMultiline.value  | Direct on R2V node (104.prompt)                 | **WRONG**   |
| ref_image_0     | 139 / LoadImage.image                 | Autogrow: ref_images.ref_image_0 -> LoadImage   | **WRONG**   |
| ref_image_1     | 137 / LoadImage.image                 | Autogrow: ref_images.ref_image_1 -> LoadImage   | **WRONG**   |
| ref_image_2     | 141 / LoadImage.image                 | Autogrow: ref_images.ref_image_2 -> LoadImage   | **WRONG**   |
| duration        | 132 / PrimitiveFloat.value            | 111 / PrimitiveFloat.value (seconds -> math -> frames) | **WRONG ID** |
| seed            | 129 / RandomNoise.noise_seed          | 15 / RandomNoise.noise_seed                     | **WRONG ID** |
| aspect          | 115 / ResolutionSelector.aspect_ratio | 115 / ResolutionSelector.aspect_ratio           | **CORRECT** |
| output_prefix   | 92 / SaveVideo.filename_prefix        | 92 / SaveVideo.filename_prefix                  | **CORRECT** |

**Root cause:** M0 analyzed what appears to have been a LiteGraph UI-format workflow, not an API-format workflow. The UI format uses different node IDs (with `105:` prefixed compound IDs from group nodes). The R2V node itself did not exist in the history -- only I2V was found.

### Corrected Mapping Table (r2v_v1.json)

| Parameter       | Node ID | Class                       | Field            | Type    |
|-----------------|---------|-----------------------------|--------------------|---------|
| prompt          | 104     | MiniMaxH3ReferenceToVideo   | prompt             | STRING  |
| ref_image_N     | 200+N   | LoadImage                   | image              | COMBO   |
| ref_images wire | 104     | MiniMaxH3ReferenceToVideo   | ref_images.ref_image_N | link |
| duration_sec    | 111     | PrimitiveFloat              | value              | FLOAT   |
| frame_calc      | 107     | ComfyMathExpression         | expression         | STRING  |
| seed            | 15      | RandomNoise                 | noise_seed         | INT     |
| aspect          | 115     | ResolutionSelector          | aspect_ratio       | COMBO   |
| output_prefix   | 92      | SaveVideo                   | filename_prefix    | STRING  |
| ref_image_size  | 104     | MiniMaxH3ReferenceToVideo   | ref_image_size     | COMBO   |

## 6. Reference Slot Behavior

- **Min refs:** 0 (all optional via COMFY_AUTOGROW_V3)
- **Max image refs:** 9
- **Max video refs:** 3 (+ matching audio tracks)
- **Max audio refs:** 3 (standalone)
- References are IMAGE type -- LoadImage outputs connect via autogrow dot notation
- Prompt must use `<Picture i>` tags to reference images (per node description)
- `ref_image_size`: "match" scales refs down to generation pixel area; "max" uses 2048px short edge (slower but better identity)

## 7. Duration / Frame Semantics

- **FPS:** 24 (fixed, hardcoded in node tooltip and CreateVideo)
- **Length field:** Frame count, NOT seconds
- **Frame grid:** 17k+5 (valid: 5, 22, 39, 56, 73, 90, 107, 124, 141, ..., 362)
- **Trained range:** 124-362 frames (5.17s - 15.08s)
- **Min:** 5 frames (0.21s) -- out of distribution
- **Max:** 3600 frames (150s) on R2V node; CRT_MinimaxLength caps at 362
- **Duration-to-frames formula:** `max(5, round(seconds * 24)) + (5 - (max(5, round(seconds * 24)) % 17)) % 17`
  - This snaps up to the nearest 17k+5 grid point
- Our template uses PrimitiveFloat (seconds) -> ComfyMathExpression -> R2V length

## 8. Aspect Ratio Format

ResolutionSelector accepts these string values:
- `"1:1 (Square)"`
- `"2:3 (Portrait Photo)"`
- `"3:2 (Photo)"`
- `"3:4 (Portrait Standard)"`
- `"4:3 (Standard)"`
- `"9:16 (Portrait Widescreen)"`
- `"16:9 (Widescreen)"`
- `"21:9 (Ultrawide)"`

Combined with `megapixels` (default 1.0) and `multiple` (default 8, we use 32 for H3).
Outputs: width (index 0), height (index 1).

## 9. Seed Behavior

- Field: `noise_seed` on `RandomNoise` node
- Type: INT, range 0..18446744073709551615 (uint64)
- `control_after_generate: true` (UI auto-increments)
- Set to 0 in template; injected at runtime

## 10. Sampler Configuration

| Parameter  | Value         | Notes                          |
|------------|---------------|--------------------------------|
| sampler    | res_multistep | Via KSamplerSelect             |
| scheduler  | simple        | Via BasicScheduler             |
| steps      | 20            | Fixed                          |
| denoise    | 1.0           | Full denoise (no img2img)      |

## 11. Model Loading

- **UNET:** `minimax_h3_ref2va_pruned_int8_convrot.safetensors` (R2V-specific)
  - I2V uses `minimax_h3_fl2va_pruned_int8_convrot.safetensors` (different model!)
- **CLIP:** `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`, type=`minimax`
- **Video VAE:** `minimax_h3_video_vae_fp16.safetensors`
- **Audio VAE:** `minimax_h3_audio_vae_fp32.safetensors`

## 12. Output / History Schema

From verified `/history` response for completed job:

```json
{
  "outputs": {
    "92": {
      "images": [
        {
          "filename": "MiniMax_H3_00042_.mp4",
          "subfolder": "video",
          "type": "output"
        }
      ],
      "animated": [true]
    }
  },
  "status": {
    "status_str": "success",
    "completed": true,
    "messages": [
      ["execution_start", {"prompt_id": "...", "timestamp": 1786750563481}],
      ["execution_cached", {"nodes": [], "prompt_id": "...", "timestamp": 1786750563483}],
      ["execution_success", {"prompt_id": "...", "timestamp": 1786750869283}]
    ]
  }
}
```

**Key facts:**
- Output node is `"92"` (SaveVideo)
- Videos listed under `outputs["92"]["images"]` (key is "images" even for video)
- Each entry: `{filename, subfolder, type}`
- `animated: [true]` flag present for video outputs

## 13. Binary Retrieval

**Verified working:** `GET /view?filename={filename}&subfolder={subfolder}&type={type}`

Example: `GET /view?filename=MiniMax_H3_00042_.mp4&subfolder=video&type=output`
- Returns HTTP 200, Content-Type: `video/mp4`
- Direct binary stream (1.7 MB for test file)

## 14. WebSocket Contract

Connection: `ws://127.0.0.1:8188/ws?clientId={uuid}`

Initial message on connect:
```json
{
  "type": "status",
  "data": {
    "status": {
      "exec_info": {
        "queue_remaining": 0
      }
    },
    "sid": "897fa2bd-..."
  }
}
```

Known event types (from ComfyUI documentation and observation):
- `status` -- queue status
- `execution_start` -- job begins
- `executing` -- per-node execution (node ID in data)
- `executed` -- node output ready
- `execution_success` -- job complete
- `execution_error` -- job failed
- `progress` -- sampling progress (step/total)

## 15. POST /prompt Contract

Submit workflow via:
```
POST /prompt
Content-Type: application/json

{
  "prompt": { ...workflow JSON... },
  "client_id": "uuid"
}
```

Response: `{"prompt_id": "uuid", "number": N, "node_errors": {}}`

## 16. Upload for References

Images must exist in the input directory or be uploaded via:
```
POST /upload/image
Content-Type: multipart/form-data
- image: (file)
- subfolder: (optional)
- overwrite: (optional, "true"/"false")
```

Response: `{"name": "filename.png", "subfolder": "", "type": "input"}`

## 17. Pipeline Flow (R2V)

```
CLIPLoader(minimax) --> clip
VAELoader(video)    --> vae  
VAELoader(audio)    --> audio_vae
LoadImage(s)        --> ref_images.ref_image_N

ResolutionSelector  --> width, height
PrimitiveFloat(sec) --> ComfyMathExpression --> length (frames)

MiniMaxH3ReferenceToVideo(prompt, w, h, length, ref_image_size, clip, vae, audio_vae, refs)
  --> [0] positive (CONDITIONING)
  --> [1] LATENT

UNETLoader(ref2va)  --> model
BasicScheduler(model, simple, 20, 1.0)  --> sigmas
RandomNoise(seed)   --> noise
BasicGuider(model, positive) --> guider
KSamplerSelect(res_multistep) --> sampler

SamplerCustomAdvanced(noise, guider, sampler, sigmas, latent) --> samples

VAEDecode(samples, video_vae)      --> images
VAEDecodeAudio(samples, audio_vae) --> audio

CreateVideo(images, audio, 24fps)  --> VIDEO
SaveVideo(video, prefix)           --> output files
```

## 18. Injection Points for M3.B+

The following fields must be injected at runtime by the bridge:

| Injection         | Node ID | Field                         | Example Value                   |
|-------------------|---------|-------------------------------|---------------------------------|
| prompt            | 104     | prompt                        | "A cat walks <Picture 1>..."    |
| ref image upload  | 200+N   | image                         | "uploaded_ref_0.png"            |
| ref wiring        | 104     | ref_images.ref_image_N        | ["200", 0]                      |
| duration (sec)    | 111     | value                         | 5.0                             |
| seed              | 15      | noise_seed                    | 123456789                       |
| aspect            | 115     | aspect_ratio                  | "16:9 (Widescreen)"            |
| output prefix     | 92      | filename_prefix               | "video/shot_001"                |
| ref_image_size    | 104     | ref_image_size                | "match" or "max"                |

**Dynamic ref count:** To add N reference images, add LoadImage nodes with IDs 200..200+N-1, and add corresponding `ref_images.ref_image_0` through `ref_images.ref_image_{N-1}` entries on node 104.

## 19. Known Differences from M0

1. **Node IDs completely different** -- M0 used IDs from a UI-format group node (105:104, 105:111, etc.)
2. **Prompt is NOT on PrimitiveStringMultiline** -- it's a direct input on the R2V node
3. **References use COMFY_AUTOGROW_V3** -- NOT fixed LoadImage nodes with hardcoded IDs
4. **Up to 9 image refs** (M0 assumed exactly 3)
5. **Also supports video refs (up to 3) and audio refs (up to 3)**
6. **Duration is in seconds via PrimitiveFloat -> math expression** -- not direct frames
7. **R2V has its own UNET model** (`ref2va` vs `fl2va` for I2V)
8. **R2V requires audio_vae** (additional required input vs I2V)
9. **R2V has ref_image_size option** ("match" vs "max")

## 20. Real R2V Validation Run

**Date:** 2026-08-16
**ComfyUI:** 0.33.1
**Template SHA-256:** 3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4

| Parameter | Value |
|-----------|-------|
| Reference file | example.png (768x768 RGB PNG, uploaded as m3a_ref.png via POST /upload/image) |
| Prompt | Minimal R2V with `<Subject 1>` / `<Picture 1>` tags, 6 sections |
| Duration input | 5.0 seconds (PrimitiveFloat node 111) |
| Seed | 42 (RandomNoise node 15, field noise_seed) |
| Aspect | "16:9 (Widescreen)" (ResolutionSelector node 115) |
| Output prefix | "preflight/m3a_validation" (SaveVideo node 92) |
| client_id | 4cc8fadb-3f05-4287-ac92-53b7d4ac85e2 |
| prompt_id | fea453b7-ccb6-4f0b-8bee-05225c4df2bd |

**Submission:** POST /prompt HTTP 200 — accepted on first attempt.

**Execution:**
- execution_start event received
- execution_cached for nodes: 11, 24, 17, 13 (model loaders cached)
- Generation took ~230 seconds (~3.8 minutes)
- execution_success event received

**History output (exact):**
```json
outputs["92"]["images"][0] = {
    "filename": "m3a_validation_00001_.mp4",
    "subfolder": "preflight",
    "type": "output"
}
```

**Binary retrieval:** GET /view?filename=m3a_validation_00001_.mp4&subfolder=preflight&type=output → HTTP 200, Content-Type: video/mp4, 532,938 bytes.

**FFprobe analysis:**
| Property | Value |
|----------|-------|
| Container | mov/mp4 |
| Video codec | h264 |
| Resolution | 1376x768 (16:9) |
| Frame rate | 24 fps |
| Frame count | 124 |
| Audio codec | AAC |
| Audio rate | 32,000 Hz stereo |
| Duration | 5.167 seconds |
| Audio | MUXED into video (no separate file) |

**Duration verification:** input 5.0s → 124 frames. Formula: max(5, round(5.0 × 24)) + (5 - (max(5, round(5.0 × 24)) % 17)) % 17 = 120 + 4 = 124. Grid 17×7+5=124. **CONFIRMED.**

**Reference slot behavior:** 1 reference image used, unused autogrow slots absent from workflow. **Job succeeded — confirms unused slots can be omitted.**

**FFmpeg last frame extraction:** `ffmpeg -sseof -0.04 -i video.mp4 -frames:v 1 lastframe.png` → exit 0, valid PNG produced. **CONFIRMED.**

**Blockers resolved:** NONE remaining. All implementation contracts verified by real R2V execution.

## 21. Frozen Implementation Facts

| Fact                           | Value                                                     |
|--------------------------------|-----------------------------------------------------------|
| ComfyUI version               | 0.33.1                                                    |
| R2V node class                 | MiniMaxH3ReferenceToVideo                                 |
| R2V UNET model                | minimax_h3_ref2va_pruned_int8_convrot.safetensors         |
| CLIP model                    | qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors             |
| CLIP type                     | minimax                                                   |
| Video VAE                     | minimax_h3_video_vae_fp16.safetensors                     |
| Audio VAE                     | minimax_h3_audio_vae_fp32.safetensors                     |
| FPS                           | 24                                                        |
| Frame grid                    | 17k+5 (124 default, trained 124-362)                     |
| Duration->frames formula      | max(5,round(s*24))+(5-(max(5,round(s*24))%17))%17         |
| Max image refs                | 9                                                         |
| Max video refs                | 3                                                         |
| Max audio refs                | 3                                                         |
| Ref wiring format             | ref_images.ref_image_N (autogrow dot notation)           |
| Prompt ref tags               | `<Picture i>`, `<Video k>`, `<Audio j>`                  |
| Sampler                       | res_multistep                                             |
| Scheduler                     | simple                                                    |
| Steps                         | 20                                                        |
| Aspect values                 | "1:1 (Square)", "16:9 (Widescreen)", "9:16 (...)", etc.  |
| Output key                    | outputs["92"]["images"][i].{filename,subfolder,type}     |
| Video retrieval               | GET /view?filename=X&subfolder=Y&type=Z                  |
| Image upload                  | POST /upload/image (multipart)                           |
| Job submit                    | POST /prompt {prompt: workflow, client_id: uuid}         |
| WS connect                    | ws://127.0.0.1:8188/ws?clientId={uuid}                   |
| Seed node                     | 15 (RandomNoise), field: noise_seed → SamplerCustomAdvanced 14   |
| Prompt node                   | 104 (MiniMaxH3ReferenceToVideo), field: prompt                   |
| Ref image node                | 200 (LoadImage) → R2V 104 ref_images.ref_image_0                |
| Duration node                 | 111 (PrimitiveFloat) → 107 (ComfyMathExpression) → R2V 104      |
| Minimum proven refs           | 1 (validated by real R2V execution)                              |
| Audio                         | MUXED into .mp4 (no separate audio output)                      |
| Output container              | .mp4 (h264 video + AAC audio)                                   |
| Output resolution (16:9)      | 1376x768                                                         |
| R2V generation time (5s/124f) | ~230 seconds (~3.8 minutes) on RTX 5090                         |
| Template SHA-256              | 3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4 |
| Template validated by R2V run | YES — prompt_id fea453b7-ccb6-4f0b-8bee-05225c4df2bd            |
