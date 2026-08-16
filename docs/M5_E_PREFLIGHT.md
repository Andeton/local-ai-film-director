# M5.E.1 — Two-Reference H3 R2V Preflight Evidence

**Date:** 2026-08-16
**Status:** COMPLETE — Technical PASS + Human Visual PASS

---

## 1. Runtime Environment

| Item | Value |
|---|---|
| ComfyUI | 0.33.1 |
| ComfyUI path | D:\ComfyUI\TD_1\ComfyUI |
| API | http://127.0.0.1:8188 |
| GPU | NVIDIA GeForce RTX 5090 |
| VRAM | 34.2 GB total |
| Peak Torch VRAM | 23.2 / 28.0 GB |

---

## 2. Live Node Contract

**MiniMaxH3ReferenceToVideo** `ref_images` input:

```
COMFY_AUTOGROW_V3
  prefix: ref_image_
  min: 0
  max: 9
  template input: ref_image (IMAGE type)
```

Each reference image is provided via a LoadImage node wired through autogrow dot notation on the H3 node.

---

## 3. Exact Two-Reference API JSON Representation

On H3 node 104:

```json
"ref_images.ref_image_0": ["200", 0],
"ref_images.ref_image_1": ["201", 0]
```

Node 200: `LoadImage` → `m5e_ref_man.png`
Node 201: `LoadImage` → `m5e_ref_woman.png`

---

## 4. Exact Submitted Prompt

```
<Subject 1> is an adult man wearing a vivid bright red jacket and a black knit beanie hat, with dark brown hair and a short beard, lean athletic build in <Picture 1>
<Subject 2> is an adult woman wearing a vivid turquoise winter coat with a short silver bob haircut, fair skin, distinctive feminine features in <Picture 2>

Summary: Both adults stand side-by-side in a well-lit neutral studio. The man from <Picture 1> is on screen-left and raises his right hand in greeting. The woman from <Picture 2> is on screen-right, turns toward him, then looks toward the camera. Both remain visible together for the full shot.

Retention Analysis:
<Subject 1> fully_preserved — vivid red jacket, black beanie, dark beard, athletic build, screen-left position
<Subject 2> fully_preserved — turquoise coat, silver bob, fair skin, feminine build, screen-right position

Detailed Description:
[Shot 1, 0.0-5.0s] A well-lit neutral studio. <Subject 1> stands at screen-left wearing his bright red jacket and black beanie. <Subject 2> stands at screen-right in her turquoise coat with silver bob haircut. The man raises his right hand in a casual wave. The woman turns her head toward him then looks directly at the camera. Both remain on screen the entire time. Stationary camera, wide shot, even studio lighting.

Overall Soundscape: quiet studio ambience
```

---

## 5. Expected Diagnostic Action

- Both adults visible together for the full shot
- Man from Picture 1 on screen-left raises his right hand in greeting
- Woman from Picture 2 on screen-right turns toward him and then looks toward the camera
- No cuts, additional people, costume changes, or identity swapping

---

## 6. Input References

### Picture 1 (man)

| Item | Value |
|---|---|
| Source path | D:\ComfyUI\output\m5e_preflight\man_00001_.png |
| Dimensions | 1024x1024 |
| SHA-256 | 21f662730cd28f74 *(preflight-only, not production asset)* |
| Uploaded filename | m5e_ref_man.png |
| Description | Adult man, vivid red jacket, black knit beanie, dark brown hair and beard |

### Picture 2 (woman)

| Item | Value |
|---|---|
| Source path | D:\ComfyUI\output\m5e_preflight\woman_00001_.png |
| Dimensions | 1024x1024 |
| SHA-256 | 47e58b5fc53de650 *(preflight-only, not production asset)* |
| Uploaded filename | m5e_ref_woman.png |
| Description | Adult woman, vivid turquoise coat, short silver bob haircut |

---

## 7. Execution Results

| Item | Value |
|---|---|
| Prompt ID | beb26ded-affd-45f3-8c01-05d2fdc09cf1 |
| Runtime | 267.5s |
| Output path | D:\ComfyUI\output\m5e_preflight\two_ref_test_00001_.mp4 |
| Resolution | 1376x768 |
| Frame rate | 24 fps |
| Frame count | 124 |
| Duration | 5.167s |
| Video codec | h264 |
| Audio codec | aac |
| File size | 591,410 bytes |

---

## 8. Temporary Workflow

The workflow was constructed in-memory from a deep copy of `r2v_v1.json` with one added LoadImage node (201) and the second `ref_images.ref_image_1` wiring. It was NOT persisted as a tracked file.

Temporary workflow SHA-256: `65997759dff80bb0...`

---

## 9. Original r2v_v1 Integrity

| Checkpoint | SHA-256 |
|---|---|
| Before preflight | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` |
| After preflight | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` |

**UNCHANGED** ✓

---

## 10. Production Database

No ReferenceAsset, ReferenceGenerationRequest, ReferenceGenerationExecution, or any other production database record was created during this preflight.

---

## 11. Human Visual Verdict

**HUMAN VISUAL VERDICT: PASS**

- Both reference characters appeared in the output video.
- Picture 1 identity (man): red jacket, black beanie, and screen-left placement matched.
- Picture 2 identity (woman): turquoise coat, silver bob, and screen-right placement matched.
- Expected diagnostic actions occurred (greeting, head turn, camera look).
- No identity mixing or face swapping was observed.
- Both references meaningfully influenced the generated video.

---

## Conclusion

MiniMax H3 ReferenceToVideo successfully accepts and uses two independent reference images via the `ref_images` autogrow contract. The exact API JSON representation (`ref_images.ref_image_0`, `ref_images.ref_image_1`) is confirmed. M5.E implementation may proceed with a versioned r2v_v2 workflow supporting 2 materialized reference slots.
