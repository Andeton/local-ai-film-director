# Architecture V1 — Local AI Film Director

**Date:** 2026-08-14
**Status:** Frozen (pending M1 implementation)
**Architecture:** Hybrid Wind Comic Sidecar
**ADRs:** ADR-001 through ADR-005

---

## 1. System Boundary Matrix

| Capability | Wind Comic | Our Application | ComfyUI | MiniMax H3 | Claude Code + MCP | DaVinci Resolve |
|---|---|---|---|---|---|---|
| Project creation | OWNER | CONSUMER | — | — | — | — |
| Story / script | OWNER | CONSUMER | — | — | — | — |
| Director treatment | OWNER | CONSUMER | — | — | — | — |
| Character design | OWNER | CONSUMER | — | — | — | — |
| Character library | OWNER | CONSUMER | — | — | — | — |
| Style bible | OWNER | CONSUMER | — | — | — | — |
| Scene breakdown | OWNER | CONSUMER | — | — | — | — |
| Storyboard | OWNER | CONSUMER | — | — | — | — |
| Beat decomposition | — | OWNER | — | — | — | — |
| Coverage planning | — | OWNER | — | — | — | — |
| Shot specification | — | OWNER | — | — | — | — |
| Generation strategy | — | OWNER | — | — | — | — |
| H3 prompt building | — | OWNER | — | — | — | — |
| Workflow registry | — | OWNER | — | — | — | — |
| ComfyUI orchestration | — | OWNER | — | — | — | — |
| Workflow execution | — | CONSUMER | OWNER | — | — | — |
| Video generation | — | — | CONSUMER | OWNER | — | — |
| Take management | — | OWNER | — | — | — | — |
| Continuity chain | — | OWNER | — | — | — | — |
| AI review | — | OWNER | — | — | — | — |
| Human review | — | OWNER | — | — | — | — |
| Regeneration | — | OWNER | CONSUMER | CONSUMER | — | — |
| Timeline assembly | — | OWNER | — | — | — | — |
| Audio layers | — | OWNER | — | OPTIONAL | — | — |
| MP4 export | — | OWNER | — | — | — | — |
| NLE timeline export | OPTIONAL | OWNER | — | — | — | CONSUMER |
| Final film edit | — | — | — | — | — | OWNER |
| Workflow construction | — | — | — | — | OWNER (dev only) | — |
| Workflow validation | — | — | — | — | OWNER (dev only) | — |
| Failure diagnosis | — | OPTIONAL | — | — | OWNER (dev only) | — |
| Benchmarking | — | — | — | — | OWNER (dev only) | — |
| LLM (pre-production) | OWNER | — | — | — | — | — |
| LLM (enrichment) | — | OWNER | — | — | — | — |

---

## 2. Wind Comic Integration Boundary

### WindComicAdapter Interface

```
WindComicAdapter
  │
  ├── health() → {running: bool, version: str, db_path: str}
  │
  ├── list_projects() → ProjectSummary[]
  ├── get_project(project_id) → WCProject
  │
  ├── get_plan(project_id) → WCPlan
  │     {genre, style, characters[], scenes[], storyStructure}
  │
  ├── get_script(project_id) → WCScript
  │     {title, synopsis, shots[]{shotNumber, sceneDescription, characters[], dialogue, action, emotion}}
  │
  ├── get_characters(project_id) → WCCharacter[]
  │     {name, description, appearance, imageUrls[], profile}
  │
  ├── get_scenes(project_id) → WCScene[]
  │     {sceneId, name, description, location, imageUrl}
  │
  ├── get_storyboard(project_id) → WCStoryboardShot[]
  │     {shotNumber, description, duration, imageUrl}
  │
  ├── get_style(project_id) → WCStyle
  │     {styleId, styleBibleUrl}
  │
  ├── get_locked_characters(project_id) → WCLockedCharacter[]
  │     {name, role, imageUrl, cw}
  │
  ├── get_asset_version(asset_id) → {version: int, updatedAt: str}
  │
  └── get_character_references(project_id) → WCCharacterRef[]
        {name, turnaroundImageUrls[], faceRefUrl}
```

### MVP Transport: SQLite Read-Only

**Primary:** Direct SQLite read of `data/qfmj.db` (read-only connection)
**Fallback:** Internal HTTP API `/api/assets?projectId=...` with JWT auth

**Why SQLite primary:**
- Most reliable (no server dependency for reads)
- Fastest (no HTTP overhead)
- Full data access (no API filtering)
- Wind Comic can be stopped after pre-production completes
- Read-only = zero risk to Wind Comic data

**Why HTTP fallback:**
- Works if Wind Comic runs on a different machine
- No SQLite file locking concerns
- Validated in M0.4

---

## 3. Canonical Data Model

### ProductionProject

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | Our internal ID |
| wc_project_id | TEXT | Wind Comic source project ID |
| title | TEXT | Project title |
| status | ENUM | draft / active / completed |
| aspect | TEXT | "16:9" / "9:16" |
| created_at | TEXT ISO | Creation timestamp |
| updated_at | TEXT ISO | Last modification |

### Sequence

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | sequence_001 |
| project_id | TEXT FK | Parent project |
| name | TEXT | Sequence name |
| order_index | INT | Ordering |

### Scene

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | scene_001 |
| sequence_id | TEXT FK | Parent sequence |
| wc_scene_id | TEXT | Wind Comic source scene asset ID |
| name | TEXT | Scene name |
| location | TEXT | Location description |
| time_of_day | TEXT | Time/weather |
| characters | TEXT JSON | Character IDs present |
| description | TEXT | Scene description |
| order_index | INT | Ordering |
| status | ENUM | draft / ready / approved / outdated |

### Beat

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | beat_001_01 |
| scene_id | TEXT FK | Parent scene |
| dramatic_action | TEXT | What happens |
| character_intention | TEXT | Character goal |
| change | TEXT | State change |
| order_index | INT | Ordering |

### Shot (ShotSpecificationV1) — Model-Agnostic

> **ADR-005:** ShotSpecificationV1 describes WHAT should be produced (narrative intent,
> camera, lighting, subjects). It contains zero provider/model-specific fields.
> Provider-specific artifacts (H3PromptV1, workflow selection) are separate derived entities.

| Field | Type | Purpose | Category |
|---|---|---|---|
| id | TEXT PK | shot_001_01_01 | ID |
| beat_id | TEXT FK | Parent beat | DIRECTOR |
| wc_storyboard_id | TEXT | Source WC storyboard asset | PROVENANCE |
| wc_shot_number | INT | Source WC shot number | PROVENANCE |
| dramatic_purpose | TEXT | Why this shot exists | DIRECTOR |
| subjects | TEXT JSON | [{id, name, ref_image}] | DIRECTOR |
| action | TEXT | What happens in frame | DIRECTOR |
| environment | TEXT JSON | {location, description, ref_image} | DIRECTOR |
| camera | TEXT JSON | {shot_size, angle, movement} | DIRECTOR |
| lighting | TEXT JSON | {description, style} | DIRECTOR |
| audio_intent | TEXT JSON | {ambient, sfx, dialogue, music} | DIRECTOR |
| duration_sec | REAL | Shot duration in seconds | PRODUCTION |
| continuity_inputs | TEXT JSON | {prev_shot_id, prev_last_frame, char_state} | PRODUCTION |
| storyboard_image_path | TEXT | Path to storyboard frame | REFERENCE |
| order_index | INT | Global shot ordering | PRODUCTION |
| status | ENUM | draft / ready / generating / review / approved / outdated / failed | STATUS |
| version | INT | Version counter | VERSION |
| created_at | TEXT ISO | Creation | VERSION |
| updated_at | TEXT ISO | Last modification | VERSION |

### GenerationPlan — Model-Agnostic

> Describes HOW a shot should be generated, using generic strategy concepts.
> Does not contain provider-specific prompt text or workflow IDs.

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | |
| shot_id | TEXT FK | Target shot |
| shot_version | INT | Shot version this plan targets |
| engine_family | TEXT | "minimax_h3" / "ltx" / "wan" (extensible) |
| strategy | ENUM | TEXT_TO_VIDEO / IMAGE_TO_VIDEO / REFERENCE_TO_VIDEO / FIRST_LAST_FRAME / MULTI_PANEL |
| reference_requirements | TEXT JSON | {character_refs: bool, scene_ref: bool, prev_frame: bool, style_ref: bool} |
| duration_sec | REAL | Planned duration |
| resolution_intent | TEXT JSON | {aspect: "16:9", megapixels: 0.98} |
| seed_policy | ENUM | random / fixed / vary_per_take |
| seed | INT | Fixed seed (null if random/vary) |
| continuity_mode | ENUM | none / last_frame / first_last |
| workflow_profile | TEXT | Workflow capability profile name (e.g. "r2v_character_consistent") |
| status | ENUM | draft / ready / outdated |
| version | INT | Version counter |
| created_at | TEXT ISO | |
| updated_at | TEXT ISO | |

### H3PromptV1 — Provider-Specific Derived Artifact

> Generated by H3PromptBuilder from ShotSpecificationV1 + CharacterReferences + GenerationPlan.
> This is a MiniMax H3-specific artifact. Other engines would have their own prompt artifact types.

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | |
| shot_id | TEXT FK | Source shot |
| generation_plan_id | TEXT FK | Source generation plan |
| source_shot_version | INT | Shot version at prompt build time |
| subject_definitions | TEXT | H3 `<Subject N>` definitions |
| summary | TEXT | H3 summary section |
| retention_analysis | TEXT | H3 retention analysis |
| detailed_description | TEXT | H3 shot descriptions with timestamps |
| overall_soundscape | TEXT | H3 soundscape section |
| non_diegetic_music | TEXT | H3 music section |
| rendered_prompt_text | TEXT | Full assembled H3 prompt ready for injection |
| version | INT | Version counter |
| created_at | TEXT ISO | |

### Take

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | take_001_01_01_01 |
| shot_id | TEXT FK | Parent shot |
| generation_request_id | TEXT FK | ComfyUI job reference |
| seed | INT | Actual seed used |
| video_path | TEXT | Path to generated video |
| audio_path | TEXT | Path to audio (if separate) |
| last_frame_path | TEXT | Extracted last frame for continuity |
| status | ENUM | pending / generating / succeeded / failed / approved / rejected |
| review_id | TEXT FK | Linked review |
| created_at | TEXT ISO | Creation |

### CharacterReference — Model-Agnostic

> Contains provider-neutral character identity and visual reference data only.
> H3-specific subject definitions are derived by H3PromptBuilder at prompt build time.

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | char_ref_001 |
| project_id | TEXT FK | Parent project |
| wc_character_id | TEXT | Wind Comic source |
| name | TEXT | Character name |
| description | TEXT | Full description (narrative) |
| appearance | TEXT | Visual appearance description |
| face_ref_path | TEXT | Face reference image |
| turnaround_paths | TEXT JSON | [front, 3/4, side, back] |
| visual_anchors | TEXT JSON | Key visual identifiers |

### ContinuityState

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | |
| shot_id | TEXT FK | After which shot |
| character_states | TEXT JSON | {char_id: {clothes, props, position, emotion}} |
| environment_state | TEXT JSON | {location, time, weather, lighting} |
| narrative_state | TEXT JSON | {what_happened, who_knows_what, current_objective} |
| last_frame_path | TEXT | Last frame of approved take |

### GenerationRequest — Immutable Snapshot

> Captures everything needed to reproduce a generation exactly.
> A Take preserves its GenerationRequest permanently — even if the upstream
> Shot, GenerationPlan, or H3Prompt are later modified or invalidated.

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | |
| shot_id | TEXT FK | Target shot |
| shot_version | INT | Shot version at submission time |
| generation_plan_id | TEXT FK | GenerationPlan used |
| prompt_artifact_id | TEXT FK | H3PromptV1 (or other provider prompt) used |
| prompt_artifact_version | INT | Prompt version at submission time |
| workflow_definition_id | TEXT | WorkflowRegistry entry used |
| workflow_definition_version | TEXT | Workflow template version |
| take_number | INT | Which take for this shot |
| parameters_snapshot | TEXT JSON | Full injected parameters (prompt text, image filenames, duration, resolution, steps, sampler) |
| reference_snapshot | TEXT JSON | [{type, name, path}] — exact reference images used |
| seed | INT | Actual seed submitted |
| comfyui_prompt_id | TEXT | ComfyUI job ID |
| status | ENUM | pending / queued / running / succeeded / failed / cancelled |
| submitted_at | TEXT ISO | |
| completed_at | TEXT ISO | |
| error | TEXT | Error details if failed |

### ReviewResult

| Field | Type | Purpose |
|---|---|---|
| id | TEXT PK | |
| take_id | TEXT FK | Reviewed take |
| reviewer | ENUM | ai / human |
| verdict | ENUM | pass / warning / fail |
| scores | TEXT JSON | {character_consistency, composition, prompt_adherence, motion, continuity} |
| warnings | TEXT JSON | [warning strings] |
| notes | TEXT | Reviewer notes |
| created_at | TEXT ISO | |

### Provenance (on every imported artifact)

| Field | Type | Purpose |
|---|---|---|
| source_system | TEXT | "wind_comic" |
| source_project_id | TEXT | WC project ID |
| source_asset_id | TEXT | WC asset ID |
| source_asset_version | INT | WC asset version at import time |
| imported_at | TEXT ISO | When imported |
| source_hash | TEXT | SHA256 of source data JSON |

**Invalidation behavior:** When `WindComicAdapter.get_asset_version()` returns a version higher than `source_asset_version`, our artifact is marked `status=outdated`. This cascades: outdated Scene → outdated Beats → outdated Shots → outdated GenerationPlans → stale H3PromptV1. Generated Takes are NEVER automatically deleted — a Take permanently preserves its GenerationRequest snapshot. The user decides whether to regenerate.

---

## 4. Enrichment Layer

```
Wind Comic Scene/Shot
        ↓
    BeatEnricher          [LLM-generated, human-editable]
        ↓
    CoveragePlanner       [LLM-generated, human-editable]
        ↓
    ShotSpecBuilder       [deterministic transform + LLM enrichment]
        ↓
    StrategySelector      [deterministic rules + LLM override]
```

| Step | Method | Input | Output |
|---|---|---|---|
| BeatEnricher | LLM-generated | WC scene description + script shots | Beat[] per scene (dramatic_action, intention, change) |
| CoveragePlanner | LLM-generated | Beats + director treatment | Shot types per beat (master, medium, close-up, POV, reaction, insert) |
| ShotSpecBuilder | Deterministic + LLM | Coverage plan + WC storyboard + characters + scenes | ShotSpecificationV1[] |
| GenerationPlanBuilder | Deterministic rules + LLM override | Shot spec (subjects, refs, continuity) | GenerationPlan per shot |

Strategy selection rules (within GenerationPlan):
- No recurring character + no visual continuity + establishing → **TEXT_TO_VIDEO**
- Storyboard frame exists + composition locked → **IMAGE_TO_VIDEO**
- Character refs available + recurring cast → **REFERENCE_TO_VIDEO** (default)
- Continuation from previous shot → **FIRST_LAST_FRAME**
- Multi-panel sequence → **MULTI_PANEL**

All steps are human-editable. LLM output is a suggestion, not automatic.

---

## 5. Provider-Specific Production Layer (H3)

> **ADR-005:** Provider-specific artifacts are derived from the model-agnostic
> ShotSpecificationV1 + GenerationPlan. They are never stored on canonical entities.

### Data Flow

```
ShotSpecificationV1 (model-agnostic)
  + CharacterReferences (model-agnostic)
  + GenerationPlan (model-agnostic)
        ↓
    H3PromptBuilder (provider-specific)
        ↓
    H3PromptV1 (provider-specific derived artifact, persisted separately)
        ↓
    WorkflowRegistry selects workflow by GenerationPlan.workflow_profile
        ↓
    ComfyUIAdapter injects H3PromptV1 + references into workflow template
        ↓
    GenerationRequest (immutable snapshot of everything submitted)
        ↓
    ComfyUI execution → Take
```

### H3PromptBuilder

```
Input:  ShotSpecificationV1 + CharacterReference[] + GenerationPlan
Output: H3PromptV1
```

The builder derives H3-specific `<Subject N>` / `<Picture N>` tags from
model-agnostic CharacterReference data at build time. If CharacterReference
or ShotSpecification changes, the H3PromptV1 is rebuilt (old version preserved).

### H3PromptV1 Sections

| Section | Derived From | Required |
|---|---|---|
| subject_definitions | CharacterReference[].{name, description, appearance} → `<Subject N>` + `<Picture N>` | YES for REFERENCE_TO_VIDEO |
| summary | Shot.action + Shot.dramatic_purpose | YES |
| retention_analysis | Characters × shots appearance tracking | YES for REFERENCE_TO_VIDEO |
| detailed_description | Shot.camera + action + lighting per [Shot N] with timestamps | YES |
| overall_soundscape | Shot.audio_intent.ambient | OPTIONAL |
| non_diegetic_music | Shot.audio_intent.music | OPTIONAL |

### H3 Workflow Mapping (engine_family = "minimax_h3")

| GenerationPlan.strategy | H3 Model | H3 Workflow | Workflow Profile |
|---|---|---|---|
| TEXT_TO_VIDEO | fl2va | MiniMaxH3ImageToVideo (no frames) | h3_t2v |
| IMAGE_TO_VIDEO | fl2va | MiniMaxH3ImageToVideo + first_frame | h3_i2v |
| REFERENCE_TO_VIDEO | ref2va | MiniMaxH3ReferenceToVideo + ref_images | h3_r2v |
| FIRST_LAST_FRAME | fl2va | MiniMaxH3ImageToVideo + first_frame + last_frame | h3_fl |
| MULTI_PANEL | fl2va | GAPStoryboardManager + panels | h3_storyboard |

### Invalidation Chain

```
Wind Comic artifact changes
  ↓ WindComicAdapter detects version mismatch
ShotSpecificationV1.status → OUTDATED
  ↓
GenerationPlan.status → OUTDATED
  ↓
H3PromptV1 → stale (source_shot_version < shot.version)
  ↓
Existing Takes are NEVER deleted.
Take preserves its GenerationRequest permanently.
User decides whether to regenerate.
```

---

## 6. ComfyUI Adapter

### ComfyUIAdapter Interface

```
ComfyUIAdapter
  ├── health() → {connected, version, gpu, vram_free, queue_depth}
  ├── load_workflow(workflow_id) → WorkflowTemplate
  ├── inject_parameters(template, params) → WorkflowJSON
  ├── upload_image(path) → input_filename
  ├── submit(workflow_json, client_id) → prompt_id
  ├── monitor(prompt_id, callback) → void  [WebSocket]
  ├── get_status(prompt_id) → JobStatus
  ├── get_result(prompt_id) → GenerationResult
  ├── cancel(prompt_id) → void
  └── retry(prompt_id) → new_prompt_id
```

### WorkflowRegistry Entry

```
WorkflowDefinition:
  id: "h3_r2v_v1"
  model_family: "minimax_h3"
  strategy: "R2V"
  template_path: "workflows/h3/r2v_v1.json"
  parameter_mappings:
    prompt: {node_id: "138", field: "value"}
    ref_image_0: {node_id: "139", field: "image"}
    ref_image_1: {node_id: "137", field: "image"}
    ref_image_2: {node_id: "141", field: "image"}
    duration: {node_id: "132", field: "value"}
    seed: {node_id: "129", field: "noise_seed"}
    aspect: {node_id: "115", field: "aspect_ratio"}
    output_prefix: {node_id: "92", field: "filename_prefix"}
  required_models:
    - minimax_h3_ref2va_pruned_int8_convrot.safetensors
    - qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
    - minimax_h3_video_vae_fp16.safetensors
    - minimax_h3_audio_vae_fp32.safetensors
  required_nodes:
    - MiniMaxH3ReferenceToVideo
    - SamplerCustomAdvanced
    - SaveVideo
  capabilities: [video, audio, character_ref]
  constraints:
    fps: 24
    frame_grid: "17k+5"
    max_pixels: 1032192
    trained_range: [124, 362]
```

---

## 7. LLM Policy

| Purpose | Owner | Provider |
|---|---|---|
| Pre-production (story, director, characters, scenes, storyboard) | Wind Comic | Wind Comic's own LLM config (Ollama/OpenRouter/etc.) |
| Enrichment (beats, coverage, shot spec) | Our application | Our LLMProvider abstraction |
| AI Review (take quality scoring) | Our application | Our LLMProvider abstraction |

Our LLMProvider:
```
LLMProvider (abstract)
  ├── OllamaProvider    → http://127.0.0.1:11434/v1
  ├── LMStudioProvider  → http://127.0.0.1:1234/v1
  └── OpenRouterProvider → https://openrouter.ai/api/v1
```

OpenRouter credentials: `OPENROUTER_API_KEY` env var only. Never in source code.

---

## 8. Persistence Ownership

### Wind Comic Owns (we read via adapter)

- Projects, scripts, director plans
- Characters, character library
- Styles, scenes, storyboard
- Pipeline jobs, quality scores
- Users, teams, billing

### Our Application Owns

| Entity | Storage | Notes |
|---|---|---|
| ProductionProject (our link) | SQLite | Links to WC project via wc_project_id |
| Sequence / Scene / Beat | SQLite | Enrichment layer output |
| ShotSpecificationV1 | SQLite | Our enriched shot data |
| Take | SQLite + filesystem | Generated video files |
| ContinuityState | SQLite | Per-shot continuity tracking |
| GenerationRequest | SQLite | ComfyUI job tracking |
| ReviewResult | SQLite | AI + human review data |
| WorkflowDefinition | Filesystem JSON | Workflow templates |
| CharacterReference | SQLite + filesystem | Resolved character refs |
| ApplicationSettings | SQLite or config file | Provider config, defaults |

Media (videos, frames, references) → filesystem under `storage/` directory.

Database: SQLite (`data/production.db`) + filesystem. Not Wind Comic's database.
