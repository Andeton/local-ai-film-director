# M0 Open Questions

**Date:** 2026-08-14

---

## Unresolved Questions

### Q1. Can Wind Comic serve as the Director/pre-production layer?

| Property | Value |
|---|---|
| Evidence needed | Local installation, test with local LLM, artifact format inspection, ComfyUI integration test |
| Blocks M1? | **NO** — M1 is Project Core (schema + persistence), which is independent of Director layer |
| Blocks M2-M4? | **YES** — affects whether we build Writer/Director agents from scratch or integrate Wind Comic |
| Proposed experiment | Clone Wind Comic, install locally, connect to Ollama, generate a test story, inspect output JSON |
| Estimated effort | 2-4 hours |
| Decision point | If Wind Comic's artifacts are consumable and its LLM integration works reliably with local models, use it. Otherwise, build our own Director agents. |

### Q2. What is the minimum LLM model size for reliable structured output?

| Property | Value |
|---|---|
| Evidence needed | Test gemma4:e4b (9.6 GB) with Director-style prompts requiring structured JSON output |
| Blocks M1? | **NO** |
| Blocks M2? | **YES** — M2 is LLM Layer |
| Proposed experiment | Send 10 test prompts to gemma4 via Ollama requiring structured JSON (story, characters, scenes); measure success rate. If <90%, test qwen2.5:14b or qwen3:14b |
| Estimated effort | 1-2 hours |
| Fallback | Download a 14B-32B Qwen model, or use OpenRouter with a cloud model |

### Q3. What is the actual H3 generation time on this hardware?

| Property | Value |
|---|---|
| Evidence needed | Benchmark: time from workflow submission to video file, for 5s/10s/15s durations, R2V workflow |
| Blocks M1? | **NO** |
| Blocks M3? | **NO** — but informs queue design and concurrency settings |
| Proposed experiment | Submit 3 R2V workflows (5s, 10s, 15s) with the same prompt/refs, measure wall time and VRAM peak |
| Estimated effort | 1-2 hours (mostly generation wait time) |

### Q4. Can we construct API-submittable T2V and I2V workflows from native nodes?

| Property | Value |
|---|---|
| Evidence needed | Build a T2V workflow using MiniMaxH3ImageToVideo (no first/last frames) + sampler chain; verify it runs via API |
| Blocks M1? | **NO** |
| Blocks M3? | **PARTIALLY** — R2V works for most cases, but T2V needed for establishing shots without references |
| Proposed experiment | Construct a minimal T2V workflow JSON from native nodes (EmptyMiniMaxH3LatentAV, MiniMaxH3ImageToVideo, UNETLoader, CLIPLoader, VAELoader, KSamplerSelect, BasicScheduler, SamplerCustomAdvanced, BasicGuider, RandomNoise, VAEDecode, VAEDecodeAudio, CreateVideo, SaveVideo), submit via API |
| Estimated effort | 1-2 hours |

### Q5. How does the GAPStoryboardManager handle API-driven panel injection?

| Property | Value |
|---|---|
| Evidence needed | Test submitting a 3-panel storyboard workflow via the ComfyUI API with programmatically-generated panel data |
| Blocks M1? | **NO** |
| Blocks production pipeline? | **PARTIALLY** — determines whether we use StoryBoard for rapid prototyping |
| Proposed experiment | Construct a storyboard workflow JSON with 3 GAPStoryboardPanel + 1 GAPStoryboardManager, inject panel images and prompts, submit via API |
| Estimated effort | 2-3 hours |

### Q6. What is the optimal generation strategy per shot type?

| Property | Value |
|---|---|
| Evidence needed | Comparative quality assessment: R2V vs StoryBoard (FL2V) for the same 3-shot test sequence |
| Blocks M1? | **NO** |
| Blocks M10-M11? | **YES** — informs the Generation Strategy module |
| Proposed experiment | Generate the spec's test scenario (detective + hospital + girl) using both R2V and StoryBoard, compare character consistency and continuity |
| Estimated effort | 4-6 hours |

### Q7. How should the Continuity Chain extract the last frame?

| Property | Value |
|---|---|
| Evidence needed | Determine the best method to extract the last frame from a generated H3 video for use as first_frame in the next shot |
| Blocks M1? | **NO** |
| Blocks M13? | **YES** — core of Continuity Manager |
| Proposed experiment | Generate a 5s H3 video, extract last frame via FFmpeg (`ffmpeg -sseof -0.04 -i video.mp4 -frames:v 1 last_frame.png`), use as first_frame input to next MiniMaxH3ImageToVideo |
| Estimated effort | 1 hour |
| Alternative | GAPStoryboardManager handles this automatically; could delegate continuity to the StoryBoard node |

### Q8. What is the H3 model license for commercial use?

| Property | Value |
|---|---|
| Evidence needed | Review MiniMax H3 model license terms on HuggingFace |
| Blocks M1? | **NO** |
| Blocks release? | **YES** — Phase 31 (Licensing Audit) |
| Proposed experiment | Read the license file from the HuggingFace model page |
| Estimated effort | 30 minutes |

### Q9. Should we use SQLite or a JSON file store for MVP persistence?

| Property | Value |
|---|---|
| Evidence needed | Determine if the dependency graph + versioning requirements warrant SQLite over plain JSON files |
| Blocks M1? | **YES** — M1 is Project Core with persistence |
| Proposed experiment | Prototype both approaches: (a) SQLite with project/scene/beat/shot tables, (b) JSON files with an index file. Test CRUD operations, dependency queries, and restart recovery |
| Estimated effort | 2-3 hours |
| Recommendation | The spec says "SQLite + filesystem". Use SQLite for structured data (project graph, metadata, state) and filesystem for media assets. This is the right split. |

### Q10. Python 3.14 compatibility

| Property | Value |
|---|---|
| Evidence needed | Verify that FastAPI, Pydantic, SQLAlchemy, and the OpenAI Python SDK work on Python 3.14 |
| Blocks M1? | **POTENTIALLY** — if key packages don't support 3.14 yet |
| Proposed experiment | Create a venv with Python 3.14, pip install fastapi pydantic sqlalchemy openai httpx |
| Estimated effort | 30 minutes |
| Fallback | Use Python 3.13 (available via ComfyUI's venv) or install 3.12 |

---

## Priority Order

| Priority | Question | Blocks |
|---|---|---|
| 1 | Q10 — Python 3.14 compatibility | M1 start |
| 2 | Q9 — SQLite vs JSON persistence | M1 design |
| 3 | Q2 — LLM model size for structured output | M2 |
| 4 | Q4 — T2V/I2V API workflows | M3 (partially) |
| 5 | Q1 — Wind Comic integration | M2-M4 |
| 6 | Q3 — H3 generation time benchmark | Queue design |
| 7 | Q7 — Last frame extraction | M13 |
| 8 | Q5 — StoryBoard API submission | Prototyping |
| 9 | Q6 — Generation strategy comparison | M10-M11 |
| 10 | Q8 — H3 license | Release |

---

## Phase → Milestone Canonical Mapping

The specification defines both **Phases 0-33** (sections 11-46) and **Milestones M0-M20** (sections 48-57). These are NOT the same numbering system. Below is the proposed canonical mapping.

**Recommendation:** Use **Milestones (M0-M20)** as the primary tracking system going forward, since they represent testable deliverables with exit criteria. Phases are implementation details within milestones.

| Milestone | Spec Sections | Phases Covered | Deliverable | Exit Criteria |
|---|---|---|---|---|
| **M0** | §11 (Phase 0) | Phase 0 | Discovery validated | Wind/ComfyUI/H3 tested; architecture questions answered |
| **M1** | §12 (Phase 1), §49 | Phase 1 | Project Core | Project/Scene/Shot/Take model; SQLite persistence; survives restart |
| **M2** | §13 (Phase 2) | Phase 2 | LLM Layer | Local Qwen via Ollama works; structured JSON output 95% reliable |
| **M3** | §14 (Phase 3) | Phase 3 | Writer Agent | Idea → Story with characters, beginning/middle/end |
| **M4** | §15 (Phase 4), §50 | Phase 4 | Director Agent | Story → Director Treatment (visual language, cinematography, pacing) |
| **M5** | §16-17 (Phases 5-6) | Phases 5-6 | Character + Style Bible | Character cards with reference images; Style Bible inherited by shots |
| **M6** | §18 (Phase 7) | Phase 7 | Scene/Beat Breakdown | Story → Sequence → Scene → Beat with locations and dramatic purpose |
| **M7** | §19 (Phase 8) | Phase 8 | Coverage Planner | Each scene has coverage.json with shot types per beat |
| **M8** | §20 (Phase 9-10) | Phases 9-10 | Shot Spec + Storyboard | Machine-readable shot specs; visual storyboard with approve/reject |
| **M9** | §21-22 (Phase 11) | Phase 11 | Reference Manager | Auto-collect character/scene/style/prev-frame references per shot |
| **M10** | §23 (Phase 12) | Phase 12 | H3 Prompt Builder | Shot Spec → H3 prompt using discovered format |
| **M11** | §24-25 (Phases 13-14) | Phases 13-14 | ComfyUI Generation | Shot → workflow → ComfyUI → video; queue with 20 shots |
| **M12** | §26 (Phase 15) | Phase 15 | Take Manager | Multiple takes per shot; favorite/approve/reject |
| **M13** | §27-28 (Phases 16-17) | Phases 16-17 | Continuity | State tracking; last-frame chain; 5 connected shots pass |
| **M14** | §29-30 (Phases 18-19) | Phases 18-19 | Review System | AI review scores + human approve/reject UI |
| **M15** | §31-32 (Phases 20-21) | Phases 20-21 | Regeneration + Smart Retry | Modify prompt/refs/camera/duration per shot; targeted retry |
| **M16** | §33 (Phase 22) | Phase 22 | Timeline | Internal timeline with clips, durations, transitions |
| **M17** | §35 (Phase 24) | Phase 24 | Audio | Dialogue/SFX/music layers; H3 audio as default with external override |
| **M18** | §34 (Phase 23) | Phase 23 | Export | MP4 + EDL/JSON + OTIO; Resolve-ready output |
| **M19** | §36-42 (Phases 25-30) | Phases 25-30 | Hardening | Benchmark, scheduler, error handling, recovery, logging, UI, settings |
| **M20** | §43-47 (Phases 31-33), §54-57 | Phases 31-33 | Beta | 3 test films, 100+ shots, 300+ takes, licensing audit, security |

### Notes on the Mapping

1. **Phases 25-30 are consolidated into M19** because they are all hardening/polish activities
2. **Phases 31-33 are consolidated into M20** because they are pre-release requirements
3. The spec's roadmap table (§72) uses a different M-numbering that doesn't match the milestone sections. The table above reconciles both.
4. **M17 and M18 are swapped** relative to the phase order because the spec has Audio (Phase 24) before Export (Phase 23), but the milestones have Export before Audio in the development order (§58). I follow the development order.

### Spec §72 Roadmap Reconciliation

The spec's table in §72 lists M0-M20 with different labels than the milestone sections §48-§57. Here's the reconciliation:

| §72 Label | §72 Result | Maps to Above | Match? |
|---|---|---|---|
| M0 | Technical Spike | M0 | YES |
| M1 | Project Core | M1 | YES |
| M2 | LLM Layer | M2 | YES |
| M3 | Writer | M3 | YES |
| M4 | Director | M4 | YES |
| M5 | Character/Style | M5 | YES |
| M6 | Scene/Beat | M6 | YES |
| M7 | Coverage | M7 | YES |
| M8 | Storyboard | M8 | YES (includes Shot Spec) |
| M9 | Shot Spec | M8 (merged) | MERGED with storyboard |
| M10 | H3 Prompt | M10 | YES |
| M11 | ComfyUI | M11 | YES |
| M12 | Takes | M12 | YES |
| M13 | Continuity | M13 | YES |
| M14 | Review | M14 | YES |
| M15 | Re-film | M15 | YES |
| M16 | Timeline | M16 | YES |
| M17 | Audio | M17 | YES |
| M18 | Export | M18 | YES |
| M19 | Hardening | M19 | YES |
| M20 | Beta | M20 | YES |

The §72 table is the canonical reference. Use M0-M20 from §72 going forward.
