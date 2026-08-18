# Roadmap — Local AI Film Director

**Principle:** Build the working product first. Use ready-made solutions. Write only the minimum glue required. Generalize only after a real workflow proves the need.

---

## Completed

| Milestone | Date | Outcome |
|---|---|---|
| M0 | 2026-08-14 | Discovery: RTX 5090, ComfyUI 0.33.1, H3 models, WC v12.320, architecture frozen |
| M1 | 2026-08-15 | Integration core: FastAPI, WC adapter, canonical models, import pipeline. 185 tests |
| M2 | 2026-08-16 | Production specification: beats, shots, plans, strategy selection, enrichment. 418 tests |
| M3 | 2026-08-16 | H3 bridge: Shot→R2V→ComfyUI→Take. First real generation. 673 tests |
| M4 | 2026-08-16 | WC production handoff: idea→WC→canonical import→enrichment. 884 tests |
| M5 | 2026-08-16 | Reference management: ingest, lifecycle, Z-Image/Krea2 generators, r2v_v2. 1148 tests |
| M6 | 2026-08-17 | Take management: approve/reject, persistent queue, worker recovery. 1320 tests |
| M7.A-E | 2026-08-17 | Continuity: chain state, FLF workflow, frame validation, replace-approved, invalidation, rebuild. 1493 tests |
| M7.F | 2026-08-17 | Live acceptance: FLF identity conditional acceptance. A/B preflight. 1513 tests |
| M7.G.A | 2026-08-17 | VisualAssetPack, AssetRole (18 roles), AssetRoleBinding. 1580 tests |
| M7.G.B | 2026-08-17 | ConditioningRecipe, CapabilityRegistry (FROZEN). 1692 tests |
| M7.G.C | 2026-08-18 | H3 image-pack multi-reference: HUMAN PASS. 1716 tests |

---

## Active Priorities

### P1 — Prove continuity/generation engine

**P1A: H3 image-only multi-reference** (M7.G.C)

Minimal recipe using proven H3 R2V with at least 3 simultaneous references:
- Picture 1: character identity
- Picture 2: environment view
- Picture 3: predecessor continuity frame
- Picture 4: important prop (optional)
- No ref_video, no ref_audio

Create smallest workflow derived from proven R2V template. One live acceptance run.

**P1B: LTX-2.3 Ingredients** — DEFERRED FALLBACK

Not needed while H3 image-pack works. Evaluate only if H3 later fails a concrete production requirement.

### P2 — One complete real multi-shot scene

Full pipeline: idea → Wind Comic → canonical shots → references → generation → Takes → approval → continuity → complete scene.

No new abstraction unless this scene proves it necessary.

### P3 — Minimal operator UI

Projects, shots, generate, Takes, approve/reject, regenerate, sequence status.

### P4 — Assembly/export

Approved Takes → ordered scene → MP4/timeline output.

### P5 — Deferred (after useful product exists)

- AI reviewer (M8)
- Broad model routing
- Additional model adapters (SkyReels, Wan VACE, SCAIL-2, Seedance, Kling)
- CapabilityRegistry expansion (currently frozen)
- 360/3D environment systems
- Distributed rendering
- Generalized infrastructure

---

## Known Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| FLF has no ref_images | Back-facing predecessor loses face identity | P1A (R2V multi-ref) |
| No T2V/I2V workflow template | Only R2V/FLF are API-ready | Construct via MCP when needed |
| H3 Turbo LoRA not installed | Generation at 20 steps (~4 min) | Install when speed matters |
| WC Writer quality (qwen3:14b) | Dialogue/action fields may be empty | Evaluate with higher-quality models |
