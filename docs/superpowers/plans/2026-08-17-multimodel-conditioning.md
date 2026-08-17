# Multi-Model Conditioning — Implementation Plan

> **Architecture:** `docs/MULTIMODEL_CONDITIONING_ARCHITECTURE.md`
> **Prerequisite:** M7.A–M7.E complete, M7.F identity fix committed, A/B preflight complete
> **M7 status:** OPEN — conditional acceptance, not closed

**Goal:** Extend the M7 continuity system with multi-reference conditioning recipes that preserve both temporal continuity and character identity across shots, using versioned asset packs and model-specific recipes.

---

## Implementation Sequence

### M7.G.A — Canonical VisualAssetPack and Asset-Role Definitions

Extend the existing ReferenceAsset architecture with typed roles.

- Define role enum (CHARACTER_FACE_CLOSEUP, CHARACTER_BODY_FRONT, ENVIRONMENT_VIEW, CONTINUITY_FRAME, PROP_REFERENCE, etc.)
- Extend ReferenceAsset or create VisualAssetRole binding
- No provider-specific fields in canonical models
- Preserve backward compatibility with M5–M7 data
- Deterministic selection by (project, character/shot, role, status=approved, state=current)

### M7.G.B — ConditioningRecipe and CapabilityRegistry

- Versioned, fingerprinted recipe manifests
- Runtime capability probing (installed models, nodes, VRAM)
- Workflow/source provenance
- Approved/deprecated lifecycle
- No automatic upstream updates

### M7.G.C — H3 Image-Only Multi-Reference Recipe

Use the already installed MiniMaxH3 R2V contract with image references only.

**Recipe: `h3_r2v_image_pack_v1`**
- Picture 1: character identity (approved ReferenceAsset)
- Picture 2: shot-specific environment view
- Picture 3: predecessor final frame
- Picture 4: important prop (when required)
- No reference video
- No reference audio
- Uses `ref2va` UNET (proven, ~4-7 min per shot)

**Requires:** New versioned workflow template `r2v_image_pack_v1.json` (does NOT modify r2v_v1/v2/flf_v1).

One live acceptance run after deterministic implementation is complete.

### M7.G.D — LTX-2.3 Ingredients Integration

Use the official Lightricks reference-sheet IC-LoRA and official ComfyUI workflow.

- Import and parameterize the official workflow (do not design from scratch)
- Trained bucket: 768×448, 121 frames, 24 fps
- Reference sheet: face/body turnaround, props, clean location panel, black background, no text
- LTX-2.3 Ingredients LoRA is NOT compatible with LTX-2.5

### M7.G.E — Ready-Model Adapters and Technology Radar

**Local/open candidates:**
1. SkyReels V3 — official local inference, 1-4 ref images, character/object/background
2. Wan VACE — official ComfyUI workflow, structural/motion guidance
3. SCAIL-2 — character replacement, front/back/close-up references
4. MV-Adapter — multi-view image generation for character/prop view packs

**Optional remote Partner Node adapters:**
5. Seedance 2.0/2.5 — subject + scene reference, large multimodal sets
6. Kling 3.0 — subject consistency, multi-shot workflows

### M7.G.F — Environment Packs and 360/3D Sources

- 360 panorama or 3D world as durable source asset
- Rendered shot-specific 16:9 environment views for video workflows
- HunyuanWorld 1.0 and HY-World 2.0 as candidates
- Do not integrate 360/3D generation until a shot requires free camera traversal

### M7.G.G — Minimal Acceptance Policy

For each ready official workflow:
1. Contract validation
2. One technical smoke run
3. One human visual acceptance
4. Fingerprint and runtime recording
5. Promote to APPROVED only after passing

Do not run broad multi-seed experiments unless the first workflow fails a specific criterion.

---

## Scope Boundaries

- M7 remains OPEN until at least M7.G.C proves acceptable identity+continuity
- M8 (AI review) NOT STARTED
- No automatic deletion of existing Takes/media
- No modification of r2v_v1/r2v_v2/flf_v1 fingerprints
- Existing immutable GenerationRequests preserved
