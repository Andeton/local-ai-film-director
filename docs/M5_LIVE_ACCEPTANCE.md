# M5 Live Acceptance — Reference Management

**Date:** 2026-08-16
**Project:** proj_b8b1b8ab40b5
**Character:** char_df6a4feecb8d (陆砚)
**ComfyUI:** v0.33.1, RTX 5090 (32 GB VRAM)

---

## M5.H1 — Character Reference Generation

### First Candidate (REJECTED)

| Field | Value |
|---|---|
| Asset ID | `ref_974c9866793f` |
| Request ID | `rgreq_16e8fc23ed1d` |
| Execution ID | `rgexe_8e50a397c435` |
| Profile | z_image_turbo_v1 / 1.0.0 |
| Seed | 42 |
| Prompt | Auto-generated from character name (empty appearance) |
| Dimensions | 1024x1024 |
| SHA-256 | `a72b59c0d11feb918911405d86cbc51e6df5cec599e19db116f2a2b3c9a40160` |
| Technical result | PASS |
| Human verdict | **REJECTED** |
| Rejection reason | Generated an East Asian character; user wanted a white European/Caucasian-looking adult man in a classic dark suit |

**Preservation:** Asset status=REJECTED, file preserved, request/execution immutable and preserved.

### Prompt Override Correction

The auto-built prompt derived from `character.appearance` (which was empty from M4 WC limitation) and `character.name` (陆砚, Chinese name). The model inferred East Asian features from the name.

**Fix:** Commit `88b4a9a` added `prompt_override` and `negative_prompt_override` parameters to `ReferenceGenerationService.generate_character_reference()` and the API DTO. When supplied, the override replaces the auto-built prompt entirely. The exact override text is persisted in the immutable `ReferenceGenerationRequest.prompt`.

### Replacement Candidate (APPROVED)

| Field | Value |
|---|---|
| Asset ID | `ref_e11cf946d0cb` |
| Request ID | `rgreq_25f99e5e6a56` |
| Execution ID | `rgexe_ab528c6be27d` |
| ComfyUI prompt | `495ed170-e9dc-485a-a30b-3826f1a2d7cf` |
| Profile | z_image_turbo_v1 / 1.0.0 |
| Fingerprint | `0f2217c2798581cc5ad3eb6e41e987163208439c92a54592ef487aaa77db72ac` |
| Seed | 43 |
| Dimensions | 1024x1024 |
| SHA-256 | `734dff2744b98953dabb87424a783ffca6eb25a8c95364ea4518cbcaf10ed099` |
| Managed path | `references\proj_b8b1b8ab40b5\ref_e11cf946d0cb\original.png` |
| Source fingerprint | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Runtime | 6.0s |
| Technical result | PASS |
| Human verdict | **APPROVED** |

**Exact approved prompt:**

> Full-body character reference photograph of a white European man, approximately 40 years old, light skin, angular masculine face, straight nose, defined jawline, short neatly styled dark brown hair, subtle natural stubble, calm intelligent expression. He wears a well-fitted classic charcoal-black tailored suit, crisp white dress shirt, dark tie, polished black shoes. Standing upright in a relaxed neutral pose, both hands fully visible, both feet fully visible, entire body inside the frame. Clean neutral gray studio background, soft even studio lighting, realistic human anatomy, natural proportions, highly detailed face and clothing, cinematic photorealism, single person, no text, no labels. Do not infer East Asian facial features from the character's name.

**Exact negative prompt:**

> East Asian facial features, Asian man, cropped head, cropped feet, hidden hands, extra fingers, malformed hands, deformed anatomy, duplicate person, multiple people, character sheet, split panels, grid, text, labels, watermark, blurry, low quality, exaggerated fashion pose

**Lifecycle:** Approved and pinned. ReferenceSelector selects this asset for CHARACTER_BODY.

---

## M5.H2 — H3 Generation Using Approved Reference

### Selected Shot

| Field | Value |
|---|---|
| Shot ID | `shot60190e252ed4` |
| Beat | `beatca36042f11d7` |
| Scene | `scene_33e2f2c3aedd` (废弃地铁站 — abandoned subway station) |
| Order index | 0 |
| Action | Character 1 enters the subway station, eyes scanning the shadows |
| Camera | wide, slow_pan_left |
| Duration | 5.0s (reduced from original 10.0s for generation feasibility) |
| Subject | 陆砚 (`char_df6a4feecb8d`) — single subject |
| Plan | `gplance4c89e0a0a7` v1, REFERENCE_TO_VIDEO, seed=42 |

### H3 Generation Evidence

| Field | Value |
|---|---|
| GenerationRequest ID | `greq6c406f12464e` |
| Take ID | `take27efaeab6301` |
| ComfyUI prompt ID | `1f02fe4b-7ce8-49c9-85c5-dcc4ac17942c` |
| Workflow | `h3_r2v_v1` / `1.0.0` |
| Workflow fingerprint | `3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4` |
| Seed | 42 |
| Status | succeeded |

### Reference Snapshot (ordered)

```json
[
  {
    "reference_asset_id": "ref_e11cf946d0cb",
    "reference_kind": "character_body",
    "subject_index": 1,
    "character_id": "char_df6a4feecb8d",
    "character_name": "陆砚",
    "picture_index": 1,
    "content_sha256": "734dff2744b98953dabb87424a783ffca6eb25a8c95364ea4518cbcaf10ed099",
    "uploaded_filename": "ref_0_e99587a8.png"
  }
]
```

### Video Metadata

| Property | Value |
|---|---|
| Resolution | 1376x768 (16:9) |
| Frame rate | 24 fps |
| Duration | 5.167s |
| Frames | 124 |
| Video codec | H.264 |
| Audio codec | AAC 32kHz |
| File size | 947,823 bytes |
| Video path | `storage\takes\proj_b8b1b8ab40b5\shot60190e252ed4\take_1\a001e94c_00001_.mp4` |
| Last frame | `storage\takes\proj_b8b1b8ab40b5\shot60190e252ed4\take_1\last_frame.png` |

### Human Reference-Influence Verdict: **PASS**

The approved character reference clearly influenced the generated video. Character identity, face, dark suit, and overall silhouette were preserved sufficiently. Functional reference retention is successful.

### Quality Limitations (documented, not functional failures)

1. The 1024x1024 full-body input reference has limited fine facial detail.
2. The generated video resolution (1376x768) is not high enough for fine-detail evaluation.

These are documented quality observations. No upscaling, regeneration, or workflow changes were performed.

### Implementation Defect Discovered During Live Execution

`resolve_from_assets` failed to join relative `managed_path` with `storage_root` before path confinement check. Fixed in commit `958d826`. All deterministic tests pass.

### Historical Preservation

- Rejected `ref_974c9866793f`: file preserved, status=REJECTED, immutable request/execution preserved
- No assets were deleted or archived during M5.H
- All historical ReferenceGenerationRequests and Executions remain immutable
