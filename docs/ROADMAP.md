# Roadmap — Local AI Film Director

**Principle:** Build the working product first. Use ready-made solutions. Write only the minimum glue required. Generalize only after a real workflow proves the need.

---

## Completed

| Milestone | Date | Outcome |
|---|---|---|
| M0 | 2026-08-14 | Discovery: RTX 5090, ComfyUI, H3 models, WC v12.320, architecture frozen |
| M1 | 2026-08-15 | Integration core: FastAPI, WC adapter, canonical models, import pipeline. 185 tests |
| M2 | 2026-08-16 | Production specification: beats, shots, plans, strategy selection, enrichment. 418 tests |
| M3 | 2026-08-16 | H3 bridge: Shot→R2V→ComfyUI→Take. First real generation. 673 tests |
| M4 | 2026-08-16 | WC production handoff: idea→WC→canonical import→enrichment. 884 tests |
| M5 | 2026-08-16 | Reference management: ingest, lifecycle, Z-Image/Krea2 generators, r2v_v2. 1148 tests |
| M6 | 2026-08-17 | Take management: approve/reject, persistent queue, worker recovery. 1320 tests |
| M7 | 2026-08-18 | Continuity: chain state, FLF, image-pack multi-reference. 1716 tests |
| P2 | 2026-08-18 | First complete 5-shot scene: generated, approved, assembled. HUMAN PASS |
| P3-refs | 2026-08-20 | Reference management UI, character/environment enrichment, image-pack integration. 1907 tests |

### P3 Reference/Enrichment Session (2026-08-20)

18 commits covering:
- Reference Management UI (generate/upload/approve/reject/archive/pin)
- Editable character name/appearance and environment description
- OpenRouter shot planning and character enrichment
- Environment description derivation from narrative
- Environment reference generation
- Enrichment idempotency and explicit replan
- ENVIRONMENT reference kind
- Real ComfyUI workflow source-reference adoption
- H3 image-pack integration into generate_take (all shots)
- Downstream continuity via image-pack Picture 3
- Subject-scoped preview binding
- Unused image-slot pruning
- ComfyUI error propagation, timeout increase, render recovery
- Browser localStorage persistence

**Production validation:** Shots 1-4 generated and inspected. Shots 1-3 approved. Shot 4 (4/4 inputs) visually good, awaiting approval.

---

## In Progress

### P3 Scene Completion

Remaining work for the current production project (`proj_cfb89b04f3c8`):

1. Human approve/reject Shot 4
2. Generate Shot 5 (single character + environment + continuity)
3. Inspect and approve/reject Shot 5
4. Generate Shot 6 (two characters + environment + continuity)
5. Inspect and approve/reject Shot 6
6. Build scene assembly
7. Inspect assembled scene

---

## Deferred

| Item | Status |
|---|---|
| LTX-2.3 fallback | DEFERRED — H3 image-pack works |
| AI reviewer (M8) | DEFERRED |
| Broad model routing | DEFERRED |
| Additional model adapters | DEFERRED |
| CapabilityRegistry expansion | FROZEN |
| Audio/dialogue control | OBSERVATION — H3 generates spontaneous dialogue |
| Batch generation queue | DEFERRED |
| Modern frontend | DEFERRED |

---

## Known Limitations

| Limitation | Impact | Status |
|---|---|---|
| WC produces generic placeholder content | Project description compensates | KNOWN |
| Primary-subject slot ordering | Open design question, no demonstrated failure | OPEN |
| No H3 prompt compilation | Shot text used directly | DESIGN_GAP |
| FLF has no ref_images | Legacy fallback only | MITIGATED by image-pack |
| Windows backslashes in Take paths | Server normalizes | LOW |
