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
| P3 | 2026-08-20 | 6-shot scene completion + durable async generation. 1923 tests. HUMAN PASS |

### P3 Scene Completion (2026-08-20)

**Production:** All 6 shots generated, approved, assembled. 48.741s scene. HUMAN PASS.

**Async durable generation:**
- UI generation moved from synchronous blocking to persistent queue lifecycle
- Embedded QueueWorker background thread in FastAPI app
- `POST /shots/{id}/generate` returns 202 immediately
- Timeout leaves job claimed for recovery, not permanently failed
- Recovery checks ComfyUI for failed requests with prompt_id (State 12b)
- UI polling, page-refresh discovery, duplicate protection
- Queue overrides for operator prompt/duration customization
- Shots 5 and 6 recovered from old-path timeout orphans without regeneration

**Reference/enrichment (prior session):**
- Reference Management UI, character/environment enrichment
- OpenRouter shot planning, environment description derivation
- H3 image-pack integration, continuity, slot pruning
- ComfyUI error propagation, timeout recovery, browser persistence

---

## Next Priorities

Choose from demonstrated production gaps:

### 1. H3 Prompt Compilation (highest demonstrated value)

Shot action text is used directly as the H3 video prompt with no optimization step. An intermediate "compile shot direction into optimal H3 prompt" could improve generation quality systematically. This is the most impactful demonstrated gap from P3 production.

### 2. Second Production Project

Run a second complete idea-to-scene pipeline to validate generalization beyond the first project. Would surface any project-specific assumptions in the current pipeline.

### 3. AI Reviewer (M8)

Automated quality assessment of generated Takes before human review. Would reduce operator burden for multi-take evaluation.

---

## Deferred

| Item | Status |
|---|---|
| LTX-2.3 fallback | DEFERRED — H3 image-pack works |
| Broad model routing | DEFERRED |
| Additional model adapters | DEFERRED |
| CapabilityRegistry expansion | FROZEN |
| Audio/dialogue control | OBSERVATION — H3 generates spontaneous dialogue |
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
| Shot 6 anomalous render duration (73.8 min) | Single observation, cause unknown | OBSERVATION |
