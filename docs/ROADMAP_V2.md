# Roadmap V2 — Local AI Film Director

**Date:** 2026-08-14
**Architecture:** Hybrid Wind Comic Sidecar (ADR-001)
**Supersedes:** Original spec phases 0-33 / milestones M0-M20 (partially)

> **Note:** The original technical specification (`Техническое задание и roadmap`) remains the historical design context and source of truth for the product vision. M0 hands-on findings (M0, M0.3, M0.4) demonstrated that Wind Comic v12.320 already implements milestones M1-M8 of the original roadmap. This V2 roadmap restructures the implementation plan to avoid duplication while preserving all original exit criteria and product goals.

---

## Completed

| Milestone | Result | Date |
|---|---|---|
| M0 | Technical Discovery — environment, ComfyUI, H3, upstream projects | 2026-08-14 |
| M0.3 | ComfyUI MCP Assessment — development tooling validated | 2026-08-14 |
| M0.4 | Wind Comic Validation — local test, artifact inspection, 10 duplication warnings | 2026-08-14 |
| M0.5 | Architecture Freeze — hybrid sidecar, data model, rebased roadmap | 2026-08-14 |

---

## New Implementation Roadmap

### M1 — Integration Core

**Goal:** Establish the project scaffold, Wind Comic adapter, and canonical data model so that a Wind Comic project's artifacts can be read and normalized.

**Tasks:**
1. Python project scaffold (FastAPI backend, project structure)
2. SQLite database with canonical schema (ProductionProject, Sequence, Scene, Beat, Shot, Take, GenerationPlan, GenerationRequest, CharacterReference, ContinuityState, ReviewResult)
3. `WindComicAdapter` — SQLite read-only adapter for Wind Comic's `data/qfmj.db`
4. Import pipeline: WC project → ProductionProject + Scenes + Characters
5. Provenance tracking on all imported artifacts
6. Health check for Wind Comic + ComfyUI connectivity
7. LLMProvider abstraction (Ollama / LM Studio / OpenRouter)
8. Configuration via environment variables + `.env.example`

> **Note (ADR-005):** Schema includes GenerationPlan (model-agnostic strategy) but NOT provider-specific prompt tables. H3PromptV1 table is added in M3 when the H3 bridge is built.

**Deliverables:**
- Running FastAPI server
- WindComicAdapter with all interface methods
- Import of one WC project into our canonical schema
- Provenance tracking verified
- LLMProvider connecting to Ollama

**Dependencies:** Wind Comic installed and running with at least one project

**Exit Criteria:**
- `WindComicAdapter.get_project()` returns normalized project data
- `WindComicAdapter.get_storyboard()` returns storyboard shots
- Imported data persists across application restart
- Provenance hash detects source changes
- LLMProvider.chat() returns structured JSON from Ollama

**Out of Scope:** Beat enrichment, coverage planning, H3 generation, UI

---

### M2 — Production Specification

**Goal:** Enrich Wind Comic's flat shot list into our full hierarchical production specification with beats and coverage.

**Tasks:**
1. BeatEnricher — LLM-driven decomposition of WC scenes into beats
2. CoveragePlanner — LLM-driven shot type assignment per beat
3. ShotSpecificationV1 builder — assemble full shot specs from WC storyboard + enrichment
4. StrategySelector — deterministic generation strategy assignment
5. CharacterReference resolver — link WC characters to shot subjects
6. Human editing endpoints for beats, coverage, shot specs

**Deliverables:**
- Beat decomposition for imported scenes
- Coverage plan with shot types
- Complete ShotSpecificationV1 for each shot
- Generation strategy assigned per shot

**Dependencies:** M1 complete, LLM model capable of structured output (14B+ recommended)

**Exit Criteria:**
- For a test WC project: every scene has beats, every beat has coverage, every shot has a ShotSpecificationV1
- GenerationPlanBuilder assigns REFERENCE_TO_VIDEO for character shots and TEXT_TO_VIDEO for establishing shots
- Beat/coverage can be edited via API

**Out of Scope:** H3 prompt building, ComfyUI submission, UI

---

### M3 — H3 Bridge (Vertical Slice)

**Goal:** Complete the first end-to-end proof: one Wind Comic storyboard shot → H3 video via ComfyUI.

**Tasks:**
1. H3PromptV1 schema — provider-specific prompt artifact table (ADR-005)
2. H3PromptBuilder — ShotSpecV1 + CharacterRefs + GenerationPlan → H3PromptV1 (subject_definitions, retention_analysis, detailed_description, soundscape)
3. WorkflowRegistry — load and manage H3 workflow templates with parameter mappings
4. ComfyUIAdapter — health, upload, submit, monitor, retrieve
5. R2V workflow template — parameterized from M0 R2V analysis
6. GenerationRequest creation — immutable snapshot of all inputs
7. Take creation — store generated video as Take 1
8. Last frame extraction — FFmpeg extract for continuity

**Deliverables:**
- H3 prompt generated from ShotSpecV1
- R2V workflow submitted to ComfyUI via REST API
- Generated video retrieved and stored as a Take
- Last frame extracted for future continuity

**Dependencies:** M2 complete, ComfyUI running with H3 models

**Exit Criteria:**
- One WC storyboard shot → ShotSpec → H3 prompt → R2V workflow → ComfyUI → video file → Take record
- End-to-end path works without manual intervention
- Video is watchable and roughly matches the shot specification

**Out of Scope:** Multiple takes, continuity chain, review, UI

---

### M4 — Wind Comic Production Handoff

> **Replaces:** Former M4 "Beat + Coverage Enrichment Polish". Enrichment quality improvements happen organically within source-fact precedence work; they no longer need a standalone milestone.

**Goal:** Close the gap between user idea and canonical production data. Programmatically trigger Wind Comic pre-production, observe completion via SSE, import richer WC outputs (script dialogue/action/emotion, storyboard camera/lighting, Director context), and make enrichment consume source facts instead of regenerating them.

**Tasks:**
1. WC HTTP SSE client with JWT auth for `/api/create-stream`
2. Source-neutral DialogueIntent and ShotSourceFacts canonical models
3. Rich WC import: script shots, storyboard correlation, Director context, storyboard parsing
4. Deterministic source-fact precedence in ShotSpecBuilder (WC facts > LLM inference)
5. PreproductionService: idea → WC SSE → import → source-aware enrichment
6. Reimport/stale propagation for script/storyboard changes
7. Synchronous POST /projects/from-idea API

**Dependencies:** M3 complete, Wind Comic running with capable LLM

**Exit Criteria:**
- User idea triggers WC pre-production programmatically
- Script dialogue/action/emotion preserved in canonical production data
- Storyboard camera/lighting extracted where parseable
- Source facts override LLM re-inference; LLM fills gaps only
- Re-import propagates stale for changed script/storyboard
- No direct LFDirector writes to WC SQLite
- One real idea → WC → canonical import works end-to-end

**Out of Scope:** Character image generation, reference ranking, multi-reference H3, UI, TTS/lip-sync

**Implementation plan:** `docs/superpowers/plans/2026-08-15-m4-wind-comic-production-handoff.md`

---

### M5 — Reference Management

> **Rescoped after M4:** Original M5 assumed WC reliably provides character images. M4 proved they may be absent. M5 now covers reference entity model, user/WC/generated ingestion, approval lifecycle, deterministic selection, and empirically-proven multi-reference H3 support.

**Goal:** Manage image reference assets for canonical characters — ingestion (user/WC/ComfyUI-generated), provenance + review lifecycle with source freshness tracking, deterministic selection, provider-specific H3 multi-reference binding, and real character-reference generation with human approval.

**Tasks:**
1. ReferenceAsset entity with kind/source separation, managed storage, SHA-256 provenance
2. User-provided and WC media reference ingestion with image validation
3. ComfyUI character reference generation (versioned/selectable workflow profiles, multiple models supported)
4. Approval/pinning lifecycle independent from source freshness/staleness
5. Deterministic ReferenceSelector (pinned+approved+current priority)
6. H3 multi-reference binding evolution + empirically proven r2v_v2 workflow
7. Reference staleness propagation on character appearance changes
8. Reference management backend API

**Dependencies:** M4 complete (canonical characters with appearance data), ComfyUI with installed image model

**Exit Criteria:**
- Multiple managed ReferenceAssets per character with SHA-256 provenance
- User-provided and generated references supported with approval lifecycle
- CANDIDATE never auto-enters production; STALE never selected
- Deterministic selection drives one authoritative binding list for H3
- H3 R2V v2 workflow empirically consumes 2 real picture inputs
- Real M4 character generates reference via ComfyUI, human approves, real H3 generation uses it
- M0-M4 regressions green

**Out of Scope:** UI, previous-frame continuity (M7), multi-take queue (M6), AI visual reviewer (M8), scene/style references, prompt enhancer

**Implementation plan:** `docs/superpowers/plans/2026-08-16-m5-reference-management.md`

---

### M6 — Take Management

**Goal:** Generate multiple takes per shot, allowing user selection.

**Tasks:**
1. Configurable takes_per_shot (default 3)
2. Seed variance per take
3. Take status tracking (pending, generating, succeeded, failed, approved, rejected)
4. Favorite / approve / reject actions
5. Generation queue with concurrency control (default: 1)
6. Batch generation for all shots in a scene

**Dependencies:** M5 complete

**Exit Criteria:**
- 3 takes generated for one shot with different seeds
- User can approve one take
- Queue manages 20 shots without losing state
- Restart recovers queue state

**Out of Scope:** Continuity chain, AI review, UI beyond API

---

### M7 — Continuity

**Goal:** Automatic continuity chain between sequential shots.

**Tasks:**
1. ContinuityState tracker — character/environment/prop/narrative state per shot
2. Last frame chain — approved take's last frame → next shot's reference
3. Continuity reference injection into H3 prompt
4. First/Last frame workflow support
5. Continuity validation — detect when upstream changes invalidate downstream

**Dependencies:** M6 complete (takes with approval)

**Exit Criteria:**
- 5 sequential shots pass through continuity chain automatically
- Last frame of shot N appears as reference in shot N+1
- Changing shot 2 marks shots 3-5 as outdated (but does NOT delete takes)

**Out of Scope:** AI review, UI

---

### M8 — Review / Regeneration

**Goal:** AI-assisted and human review of generated takes, with targeted regeneration.

**Tasks:**
1. AI reviewer — score takes on character consistency, composition, prompt adherence, motion, continuity
2. Human review API — approve / reject / regenerate per take
3. Targeted regeneration — modify prompt/refs/camera/duration/seed for re-generation
4. Smart retry suggestions based on AI review findings
5. Review history

**Dependencies:** M7 complete

**Exit Criteria:**
- AI review produces useful report for 90% of test shots
- Human can reject and regenerate with modified parameters
- Regenerated take links to original shot with modified parameters
- Bad shot can be re-done without affecting other shots

**Out of Scope:** Full UI

---

### M9 — Production UI

**Goal:** Web-based dashboard for the full production workflow.

**Tasks:**
1. Next.js/React frontend scaffold
2. Project dashboard — import from Wind Comic, show progress
3. Storyboard view — shot cards with storyboard images and status
4. Generation monitor — queue status, progress, results
5. Review interface — video playback, AI scores, approve/reject/regenerate
6. Timeline view — ordered approved takes
7. Settings — provider config, defaults

**Dependencies:** M8 complete

**Exit Criteria:**
- New user can import a WC project, review enrichment, launch generation, review takes, and approve shots through the web UI

**Out of Scope:** Audio, export, hardening

---

### M10 — Export / Hardening

**Goal:** Production-ready output and system reliability.

**Tasks:**
1. MP4 export — concatenate approved takes with FFmpeg
2. Timeline metadata — EDL/JSON export
3. OTIO export if feasible
4. Error handling — all error classes from spec §38
5. Recovery / resume — survive restart, crash recovery
6. Logging — all operations logged (no secrets)
7. Settings persistence
8. Performance scheduler (video_concurrency = 1, configurable)
9. Benchmark project (3 scenes, 20 shots, 60 takes)

**Dependencies:** M9 complete

**Exit Criteria:**
- System exports a finished short episode as MP4 + timeline metadata
- Survives application restart without losing state
- Queue recovers from ComfyUI restart
- Benchmark report generated

**Out of Scope:** Distributed rendering, mobile, cloud

---

## Mapping to Original Specification

| Original Phase/Milestone | V2 Status | Notes |
|---|---|---|
| M0 Discovery | COMPLETE | M0 + M0.3 + M0.4 |
| M1 Project Core | REPLACED by M1 Integration Core | Wind Comic provides project model |
| M2 LLM Layer | SPLIT — WC owns pre-production LLM; our M1 adds enrichment LLM |
| M3 Writer | DELEGATED to Wind Comic | |
| M4 Director | DELEGATED to Wind Comic | |
| M5 Character/Style | DELEGATED to Wind Comic | Our M5 manages references |
| M6 Scene/Beat | SPLIT — WC scenes; our M2 adds beats |
| M7 Coverage | Our M2 + source-fact precedence in M4 | Source-aware enrichment |
| M8 Storyboard | DELEGATED to Wind Comic | |
| M9 Shot Spec | Our M2 | ShotSpecificationV1 |
| M10 H3 Prompt | Our M3 | H3PromptBuilder |
| M11 ComfyUI | Our M3 | ComfyUIAdapter |
| M12 Takes | Our M6 | Take Manager |
| M13 Continuity | Our M7 | Continuity Manager |
| M14 Review | Our M8 | Review System |
| M15 Re-film | Our M8 | Regeneration |
| M16 Timeline | Our M10 | Timeline in export phase |
| M17 Audio | Our M10 (basic) | H3 native audio as default |
| M18 Export | Our M10 | MP4 + EDL |
| M19 Hardening | Our M10 | Recovery, benchmark, settings |
| M20 Beta | Post-M10 | Full production test |

---

## First Vertical Slice (within M3)

```
Existing Wind Comic project (with storyboard)
  ↓ WindComicAdapter
Import 1 storyboard shot + character references
  ↓ ShotSpecBuilder
Normalize to ShotSpecificationV1
  ↓ H3PromptBuilder
Build H3 R2V prompt (subject_definitions, detailed_description, soundscape)
  ↓ ComfyUIAdapter
Upload reference images to ComfyUI input dir
  ↓
Parameterize R2V workflow template
  ↓
Submit via POST /prompt
  ↓
Monitor via WebSocket
  ↓
Retrieve generated video
  ↓
Extract last frame via FFmpeg
  ↓
Store as Take 1 linked to Shot
```

No UI required. No continuity chain. No AI review. Just the data path.
