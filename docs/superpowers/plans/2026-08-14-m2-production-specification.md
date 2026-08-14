# M2 Production Specification Implementation Plan (Revised)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich Wind Comic's flat shot list into a hierarchical, model-agnostic production specification: Scene → Beat → CoverageDecision → ShotSpecificationV1 → GenerationPlan — with LLM-driven enrichment, history-preserving re-enrichment, and human editing via API.

**Architecture:** Enrichment layer sits between M1's WindComicAdapter/ImportService and M3's provider-specific generation. BeatEnricher and CoveragePlanner use the existing LLMProvider (Ollama) with M1-compatible JSON-object wrappers. ShotSpecBuilder deterministically assembles shot specifications. StrategySelector deterministically assigns a generic generation strategy using explicit structured inputs — ZERO provider-specific fields. All artifacts are model-agnostic (ADR-005), human-editable, and history-preserving (no physical deletion of M2 artifacts).

**Tech Stack:** Python 3.14.3, FastAPI, Pydantic v2, SQLite (stdlib `sqlite3`), existing LLMProvider/OllamaProvider, pytest

## Global Constraints

- Architecture FROZEN V1 — do not change ADR-001 through ADR-005
- Wind Comic is read-only sidecar — NEVER write to `qfmj.db`
- M2 entities are model-agnostic — ZERO provider-specific fields (ADR-005)
- GenerationPlan must NOT contain: `engine_family`, `workflow_profile`, H3, MiniMax, ComfyUI concepts. Those are M3.
- No H3PromptV1, no ComfyUI code, no WorkflowRegistry, no GenerationRequest, no Take (M3 scope)
- No frontend/UI (M9 scope)
- ShotSpecificationV1 describes WHAT to produce; GenerationPlan describes HOW (generic strategy only)
- LLM enrichment output is a SUGGESTION — human edits are NEVER silently overwritten
- Upstream source changes cascade OUTDATED status to dependent M2 entities
- M2 artifacts are NEVER physically deleted — re-enrichment marks old artifacts OUTDATED and creates new ones
- LLM structured-output contract: JSON object root (M1 `expect_json=True` returns `dict`). M2 wraps arrays in objects: `{"beats": [...]}`, `{"coverage": [...]}`
- Retry layers: transport retries owned by LLMProvider (max_retries=2 → 3 total); M2 domain repair: at most ONE additional prompt if JSON-object is valid but domain shape is invalid; after domain repair failure → `EnrichmentError`
- M1 LLM: Ollama only; do NOT add OpenRouter/LM Studio in M2
- All existing 182 M1 deterministic tests must remain green

## Authority Order

1. Accepted ADRs → 2. ARCHITECTURE_V1.md → 3. ROADMAP_V2.md → 4. DEVELOPMENT_STATE.md → 5. M0 findings → 6. Original specification

## M1 Inputs (existing interfaces consumed by M2)

| Component | Key Methods | Notes |
|-----------|------------|-------|
| `Scene` | `.id`, `.sequence_id`, `.name`, `.location`, `.description` | Parent for beats |
| `CharacterReference` | `.id`, `.project_id`, `.name`, `.description`, `.appearance`, `.turnaround_paths` | Resolved into shot subjects |
| `SceneRepository` | `get_scenes_by_sequence(seq_id, conn=)`, `get_scene(id, conn=)` | |
| `CharacterRepository` | `get_characters_by_project(proj_id, conn=)` | No `get_character(id)` — M2 adds this |
| `SequenceRepository` | `get_sequences_by_project(proj_id, conn=)` | |
| `ProjectRepository` | `get_project(id, conn=)` | |
| `WindComicAdapter` | `get_storyboard(proj_id)` → `list[WCStoryboardShot]` | Read-through for ShotSpecBuilder |
| `LLMProvider` | `chat(messages, expect_json=True)` → `LLMResponse` with `.parsed: dict` | BeatEnricher + CoveragePlanner |
| `Database` | `connection()` context manager | Atomic transactions |
| `ImportService` | `check_for_changes()`, `apply_detected_changes()` | M2 extends apply with cascade |

## M2 Canonical Entities

| Entity | Table | Parent FK | New in M2 | Purpose |
|--------|-------|----------|-----------|---------|
| Beat | `beats` | `scene_id → scenes.id` | YES | Dramatic unit within a scene |
| ShotSpecificationV1 | `shots` | `beat_id → beats.id` | YES | Model-agnostic shot description |
| GenerationPlan | `generation_plans` | `shot_id → shots.id` | YES | Generic strategy assignment (NO provider fields) |

**Coverage model:** CoveragePlanner produces transient `CoverageDecision` DTOs that flow into ShotSpecBuilder. Coverage is a planning STEP, not a persisted entity. Coverage decisions are embodied as the ShotSpecificationV1 entries created for each beat. There is no separate coverage table. Human "coverage editing" means editing the persisted ShotSpecificationV1 fields (camera, shot purpose, etc.) or re-planning coverage to generate new shots.

## Beat Model

```python
class Beat(BaseModel):
    id: str
    scene_id: str                    # FK → scenes.id
    dramatic_action: str             # What happens (LLM-enriched)
    character_intention: str         # Character goal (LLM-enriched)
    change: str                      # State change (LLM-enriched)
    characters: list[str]            # Character names involved
    order_index: int                 # Ordering within scene
    status: Literal["draft", "approved", "outdated"] = "draft"
    source: Literal["llm", "human"] = "llm"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
```

No provenance on Beat — generated by our enrichment, not imported from WC.

## ShotSpecificationV1

```python
class ShotSubject(BaseModel):
    """A character appearing in a shot."""
    character_id: str                # FK → character_references.id
    name: str
    ref_images: list[str] = Field(default_factory=list)  # available reference paths (non-lossy)

class CameraIntent(BaseModel):
    shot_size: Literal["extreme_wide", "wide", "medium_wide", "medium", "medium_close", "close_up", "extreme_close"]
    angle: str = ""                  # e.g. "low_angle", "eye_level", "high_angle", "dutch"
    movement: str = ""               # e.g. "static", "pan_left", "dolly_in", "tracking"

class ShotSpecificationV1(BaseModel):
    id: str
    beat_id: str                      # FK → beats.id
    wc_storyboard_id: str | None = None
    wc_shot_number: int | None = None
    dramatic_purpose: str             # Why this shot exists
    subjects: list[ShotSubject] = Field(default_factory=list)
    action: str                       # What happens in frame
    environment: dict                 # {location, description}
    camera: CameraIntent
    lighting: dict                    # {description, style}
    audio_intent: dict                # {ambient, sfx, dialogue, music}
    duration_sec: float = 5.0
    continuity_inputs: dict = Field(default_factory=dict)  # M7 fills
    storyboard_image_path: str | None = None
    order_index: int
    status: Literal["draft", "ready", "outdated"] = "draft"
    source: Literal["generated", "human"] = "generated"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
```

**Model-agnostic audit:** Zero H3/MiniMax/ComfyUI/workflow fields. `ShotSubject.ref_images` carries all available reference paths from `CharacterReference.turnaround_paths` — no lossy collapse to `[0]`, no `<Picture N>` tags.

## GenerationPlan — Model-Agnostic (NO provider fields)

```python
class ReferenceRequirements(BaseModel):
    character_refs: bool = False
    scene_ref: bool = False
    prev_frame: bool = False
    style_ref: bool = False

class GenerationPlan(BaseModel):
    id: str
    shot_id: str                      # FK → shots.id
    shot_version: int                 # Version of shot this targets
    strategy: Literal[
        "TEXT_TO_VIDEO", "IMAGE_TO_VIDEO",
        "REFERENCE_TO_VIDEO", "FIRST_LAST_FRAME", "MULTI_PANEL"
    ]
    reference_requirements: ReferenceRequirements
    duration_sec: float
    resolution_intent: dict           # {aspect, megapixels}
    seed_policy: Literal["random", "fixed", "vary_per_take"] = "random"
    seed: int | None = None
    continuity_mode: Literal["none", "last_frame", "first_last"] = "none"
    selection_reason: str = ""        # Why this strategy was chosen (human-readable)
    status: Literal["draft", "ready", "outdated"] = "draft"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
```

**Removed from M2 (M3 owns):** `engine_family`, `workflow_profile`. M3's WorkflowRegistry maps `strategy` + `reference_requirements` to provider-specific workflow/engine selections.

## StrategySelector — Deterministic Rules with Explicit Inputs

### StrategySelectionContext

```python
@dataclass(frozen=True)
class StrategySelectionContext:
    """Explicit structured inputs for deterministic strategy selection."""
    has_character_refs: bool       # Any subject has non-empty ref_images
    has_recurring_cast: bool       # >0 subjects in the shot
    has_storyboard_image: bool     # storyboard_image_path is not None
    has_prev_shot: bool            # continuity_inputs has prev_shot_id (M7; False in M2)
    shot_purpose: str              # dramatic_purpose category (from coverage)
    subject_count: int             # len(subjects)
```

### Precedence (highest wins)

| Priority | Condition | Strategy | Selection Reason |
|----------|-----------|----------|-----------------|
| 1 | `has_prev_shot` is True | `FIRST_LAST_FRAME` | "Continuation from previous shot requires frame-to-frame consistency" |
| 2 | `subject_count >= 3` and `has_storyboard_image` | `MULTI_PANEL` | "Multi-subject scene with storyboard composition" |
| 3 | `has_storyboard_image` and not `has_recurring_cast` | `IMAGE_TO_VIDEO` | "Locked storyboard composition without character references" |
| 4 | `has_character_refs` and `has_recurring_cast` | `REFERENCE_TO_VIDEO` | "Character references available for identity-consistent generation" |
| 5 | (default) | `TEXT_TO_VIDEO` | "No character references or storyboard — text-only generation" |

**No string/prose heuristics.** All inputs are structured booleans/integers from `StrategySelectionContext`. The selector is a pure function: `select_strategy(ctx: StrategySelectionContext, shot: ShotSpecificationV1, project_aspect: str) -> GenerationPlan`.

## Character Reference Resolution

```python
# M2 resolver: link CharacterReference to ShotSubject
# Carries ALL available ref_images — no lossy collapse
subjects = []
for char_name in beat.characters:
    char_ref = find_character_by_name(project_id, char_name)
    if char_ref:
        subjects.append(ShotSubject(
            character_id=char_ref.id,
            name=char_ref.name,
            ref_images=list(char_ref.turnaround_paths),  # full list, non-lossy
        ))
```

M5 owns reference selection/classification. M2 passes ALL available paths.

## LLM Enrichment Contract

### BeatEnricher — JSON Object Wrapper

**Input:** Scene description + script shots + character context
**Output:** M1-compatible JSON object (not raw array):

```json
{
  "beats": [
    {
      "dramatic_action": "Detective approaches the abandoned hospital",
      "character_intention": "Investigate the reported disturbance",
      "change": "Detective moves from safety to danger zone",
      "characters": ["Detective"]
    }
  ]
}
```

**Consumption:** `response = llm.chat(messages, expect_json=True)` → `response.parsed["beats"]` → validate each item.

### CoveragePlanner — JSON Object Wrapper

**Input:** Beat + scene context + characters
**Output:** M1-compatible JSON object:

```json
{
  "coverage": [
    {
      "shot_type": "establishing",
      "shot_size": "wide",
      "angle": "low_angle",
      "movement": "static",
      "purpose": "Establish location and mood",
      "duration_sec": 8
    }
  ]
}
```

**Consumption:** `response = llm.chat(messages, expect_json=True)` → `response.parsed["coverage"]` → validate each item.

### Retry / Failure Policy — Layered

| Layer | Owner | Behavior |
|-------|-------|----------|
| Transport retry | LLMProvider | max_retries=2 → 3 total attempts for HTTP/connection failures |
| JSON parse | LLMProvider | `parse_llm_json()` — already handles fences, leading text |
| Domain repair | BeatEnricher / CoveragePlanner | If `response.parsed` is a valid dict but missing expected key (`"beats"`/`"coverage"`) or items fail validation → ONE repair prompt → if repair also fails → `EnrichmentError` |

**Maximum enrichment attempts per operation:** 2 (initial + 1 repair). No unbounded retries.

## Error Semantics

| Error | HTTP | When | Originator |
|-------|------|------|-----------|
| `LLMStructuredOutputError` | 422 | Response was not valid JSON-object | LLMProvider |
| `EnrichmentError` | 422 | Valid JSON-object returned but Beat/Coverage domain contract invalid after one repair attempt | BeatEnricher / CoveragePlanner |
| `NormalizationError` | 500 | Unexpected INTERNAL transformation/model-construction failure | Service layer |
| `LLMUnavailableError` | 503 | Ollama unreachable after transport retries | LLMProvider |
| HTTP 404 | 404 | Beat/shot/scene/project not found | API routes |
| HTTP 409 | 409 | Re-enrich rejected (human-edited, no `?force=true`) | API routes |

`NormalizationError` is NOT used for expected bad LLM domain output — that is `EnrichmentError`.

## Human Editing Lifecycle

```
LLM ENRICHMENT
  → creates Beat/Shot with source="llm"/"generated", status="draft"

HUMAN EDIT (via API PUT)
  → updates fields, sets source="human", bumps version
  → dependent shots/plans → OUTDATED (stale propagation)
  → status remains "draft" (user can set "approved"/"ready")

RE-ENRICHMENT REQUEST (explicit API POST)
  → if current artifact has source="human" AND force=false → HTTP 409
  → if force=true OR source="llm":
    → mark ALL current artifacts for this scope OUTDATED (beats+shots+plans)
    → create NEW enriched artifacts (new IDs)
    → old artifacts remain persisted with status="outdated"
    → NEVER physically delete human-edited content

UPSTREAM CHANGE (scene/character modified in WC)
  → M2 entities become "outdated" via stale propagation
  → human edits PRESERVED (status changes, content stays)
  → user decides whether to re-enrich
```

## History-Preserving Re-enrichment

**CRITICAL:** No physical deletion of M2 artifacts during re-enrichment.

**Beat re-enrichment (`enrich_scene_beats`):**
1. If current beats for scene have source="human" and force=false → reject 409
2. Mark all current beats for this scene → status="outdated"
3. Mark their dependent shots → "outdated"
4. Mark dependent generation plans → "outdated"
5. Create NEW beat objects (new IDs) with source="llm", status="draft"
6. Old beats, shots, plans remain persisted

**Coverage re-plan (`plan_beat_coverage`):**
1. If current shots for beat have source="human" and force=false → reject 409
2. Mark current shots for this beat → "outdated"
3. Mark dependent generation plans → "outdated"
4. Create NEW shot specifications (new IDs)
5. Old shots and plans remain persisted

**Strategy reassignment (`assign_strategies`):**
1. For each current shot, mark its current generation plan → "outdated" (if exists)
2. Create new GenerationPlan targeting current shot version
3. Old plans remain persisted

### Current vs Historical Lookup

Repositories provide:
- `get_current_beats_by_scene(scene_id)` — returns beats where `status != "outdated"`
- `get_beats_by_scene(scene_id)` — returns ALL beats (current + historical)
- Same pattern for shots and plans

## Stale / Outdated Propagation

```
Scene becomes OUTDATED (M1 change detection via apply-changes)
  ↓
  All current Beats for that scene → OUTDATED
    ↓
    All current Shots for those beats → OUTDATED
      ↓
      All current GenerationPlans for those shots → OUTDATED

CharacterReference becomes OUTDATED
  ↓
  All current Shots that reference that character in subjects → OUTDATED
    ↓
    Their current GenerationPlans → OUTDATED

Beat human-edited (PUT /beats/{id})
  ↓
  Current Shots for that beat → OUTDATED
    ↓
    Their current GenerationPlans → OUTDATED

Shot human-edited (PUT /shots/{id})
  ↓
  Current GenerationPlan for that shot → OUTDATED

Project modified/deleted upstream
  ↓
  All current Beats across all scenes → OUTDATED
    ↓
    cascade continues through shots → plans
```

### M1 apply-changes Integration

`POST /projects/{id}/apply-changes` must:
1. Call M1 `ImportService.apply_detected_changes()` — marks M1 entities OUTDATED
2. Call M2 `StalePropagator` for each affected entity — cascades through beats → shots → plans
3. Both operations in ONE transaction

This requires a small M1 extension: the `apply-changes` route handler must accept the M2 `StalePropagator` as a dependency and invoke it after M1 apply completes.

## Persistence Schema (M2 tables only)

```sql
CREATE TABLE IF NOT EXISTS beats (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL,
    dramatic_action TEXT NOT NULL,
    character_intention TEXT NOT NULL DEFAULT '',
    change TEXT NOT NULL DEFAULT '',
    characters TEXT NOT NULL DEFAULT '[]',  -- JSON array
    order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'llm',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (scene_id) REFERENCES scenes(id)
);

CREATE TABLE IF NOT EXISTS shots (
    id TEXT PRIMARY KEY,
    beat_id TEXT NOT NULL,
    wc_storyboard_id TEXT,
    wc_shot_number INTEGER,
    dramatic_purpose TEXT NOT NULL DEFAULT '',
    subjects TEXT NOT NULL DEFAULT '[]',       -- JSON (list of ShotSubject)
    action TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '{}',    -- JSON
    camera TEXT NOT NULL DEFAULT '{}',         -- JSON (CameraIntent)
    lighting TEXT NOT NULL DEFAULT '{}',       -- JSON
    audio_intent TEXT NOT NULL DEFAULT '{}',   -- JSON
    duration_sec REAL NOT NULL DEFAULT 5.0,
    continuity_inputs TEXT NOT NULL DEFAULT '{}',
    storyboard_image_path TEXT,
    order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'generated',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (beat_id) REFERENCES beats(id)
);

CREATE TABLE IF NOT EXISTS generation_plans (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL,
    shot_version INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    reference_requirements TEXT NOT NULL DEFAULT '{}',
    duration_sec REAL NOT NULL,
    resolution_intent TEXT NOT NULL DEFAULT '{}',
    seed_policy TEXT NOT NULL DEFAULT 'random',
    seed INTEGER,
    continuity_mode TEXT NOT NULL DEFAULT 'none',
    selection_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (shot_id) REFERENCES shots(id)
);
```

**No `UNIQUE(shot_id)` on generation_plans** — multiple plans may exist for the same shot (current + historical/outdated). Current plan is found by `status != 'outdated'` AND matching `shot_version`.

**No `engine_family` or `workflow_profile` columns** — those are M3 provider-specific concerns.

## API Surface (M2 additions)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/projects/{id}/enrich` | Full pipeline: scenes → beats → coverage → shots → plans |
| GET | `/projects/{id}/beats` | List all current beats for a project |
| GET | `/scenes/{scene_id}/beats` | List current beats for a scene |
| PUT | `/beats/{beat_id}` | Human edit a beat → propagates stale to shots/plans |
| POST | `/scenes/{scene_id}/enrich-beats?force=false` | Re-enrich beats (history-preserving) |
| GET | `/projects/{id}/shots` | List all current shots for a project |
| GET | `/beats/{beat_id}/shots` | List current shots for a beat |
| PUT | `/shots/{shot_id}` | Human edit a shot → propagates stale to plan |
| POST | `/beats/{beat_id}/plan-coverage?force=false` | Re-plan coverage (history-preserving) |
| GET | `/shots/{shot_id}/generation-plan` | Get current generation plan |
| POST | `/projects/{id}/assign-strategies` | Run StrategySelector for all current shots |

Existing M1 routes unchanged. `POST /projects/{id}/apply-changes` extended to cascade M2 stale.

## Testing Strategy

### Unit Tests
- Beat model validation (valid/invalid status/source, characters list)
- ShotSpecificationV1 validation (ShotSubject, CameraIntent Literal validation, all JSON fields)
- GenerationPlan validation (strategy enum, seed_policy — NO engine_family/workflow_profile)
- StrategySelector: all 5 strategies via StrategySelectionContext, deterministic precedence (priority 1 beats 4), no string heuristics
- BeatEnricher: object-wrapped response `{"beats":[...]}`, missing key → domain repair, malformed JSON → LLMStructuredOutputError, valid object but bad domain → EnrichmentError after repair
- CoveragePlanner: object-wrapped response `{"coverage":[...]}`, invalid shot_size → domain repair
- Character resolver (name matching, non-lossy ref_images, missing characters)
- Stale propagation (scene→beat→shot→plan, character→shot→plan, beat edit→shot→plan, shot edit→plan)
- Human edit preservation (source="human" blocks re-enrich without force)

### Integration Tests
- Full pipeline: scene → beats → shots → plans (mocked LLM)
- History preservation: re-enrich does NOT delete old beats, old shots remain outdated
- Force re-enrich preserves human beats as outdated, creates new LLM beats
- Re-plan coverage preserves old shots as outdated
- Strategy reassignment preserves old plans as outdated
- Stale cascade via apply-changes: M1 scene change → M2 beats+shots+plans outdated
- Character change cascade: character modified → affected shots+plans outdated
- Human edit stale propagation: edit beat → shots/plans outdated; edit shot → plan outdated
- API endpoints: all routes correct data/errors/409
- Restart persistence for M2 entities
- Idempotent enrichment (enrich twice unchanged source → no duplicate current beats)
- Model-agnostic scan: no `minimax_h3`, `h3_r2v`, `workflow_profile`, `engine_family` in production code

### Live Ollama Semantic Smoke
- BeatEnricher with real gemma4:e4b: one scene → `{"beats":[...]}` → structurally valid
- CoveragePlanner with real gemma4:e4b: one beat → `{"coverage":[...]}` → structurally valid
- **BLOCKING:** If gemma4:e4b cannot produce structurally valid object-wrapped enrichment output after domain repair, M2 is BLOCKED

## M3 Exclusions (explicit)

NOT in M2:
- `H3PromptV1` table or builder
- `ComfyUIAdapter` or REST submission
- `WorkflowRegistry`
- `GenerationRequest` (immutable snapshot)
- `Take` or video generation
- WebSocket monitoring
- Last frame extraction
- Provider-specific prompt text anywhere
- `engine_family` field on GenerationPlan
- `workflow_profile` field on GenerationPlan
- Any `h3_` prefixed values

## Live Ollama Semantic Acceptance

**Layer A — Deterministic (all tests):** Mocked LLM responses. Validates schemas, persistence, orchestration, stale propagation, human edits, history preservation. Must pass 100%.

**Layer B — Live Semantic Smoke:** Real Ollama with gemma4:e4b. Validates object-wrapped response format. If gemma4:e4b fails: M2 is BLOCKED — report, don't downgrade.

## M1 Extensions Required

| Extension | Reason | Scope |
|-----------|--------|-------|
| `CharacterRepository.get_character(id, conn=)` | M2 character resolver needs lookup by ID | Add one method + test |
| `Database.init_schema()` extended DDL | M2 tables added | Append to SCHEMA_SQL |
| `api/routes.py` extended | M2 routes + apply-changes M2 cascade | New route functions, modify apply-changes handler |
| `main.py` wiring | M2 services injected | Constructor additions |
| `errors.py` | Add `EnrichmentError` | One class |

No M1 behavior changes. All additions are backward-compatible.

---

## File Structure (M2 additions)

```
src/film_director/
  models/
    canonical.py           # MODIFY: add Beat, ShotSubject, CameraIntent, ShotSpecificationV1,
                           #         ReferenceRequirements, GenerationPlan
  enrichment/
    __init__.py
    beat_enricher.py       # BeatEnricher (LLM object-wrapper contract)
    coverage_planner.py    # CoveragePlanner (LLM object-wrapper contract)
    shot_spec_builder.py   # ShotSpecBuilder (deterministic)
    strategy_selector.py   # StrategySelector (deterministic, StrategySelectionContext)
    prompts.py             # LLM prompt templates
    stale_propagator.py    # Cascade OUTDATED through dependency graph
  persistence/
    database.py            # MODIFY: add M2 tables to SCHEMA_SQL
    repositories.py        # MODIFY: add BeatRepository, ShotRepository, GenerationPlanRepository
  services/
    enrichment_service.py  # Orchestrates full enrichment pipeline
  api/
    routes.py              # MODIFY: add M2 routes, extend apply-changes
  errors.py                # MODIFY: add EnrichmentError
  main.py                  # MODIFY: wire M2 services
tests/
  unit/
    test_beat_model.py
    test_shot_spec_model.py
    test_generation_plan_model.py
    test_strategy_selector.py
    test_beat_enricher.py
    test_coverage_planner.py
    test_stale_propagation.py
    test_m2_repositories.py
  integration/
    test_enrichment_pipeline.py
    test_m2_api.py
    test_m2_human_edits.py
    test_m2_history.py             # re-enrichment does not delete
    test_ollama_enrichment_live.py # REQUIRED live semantic smoke
```

---

## Task Dependency Table

| Task | Requires | Produces |
|------|----------|----------|
| 1. M2 Schema + Models | M1 canonical.py, database.py, errors.py | Beat, ShotSubject, CameraIntent, ShotSpecificationV1, ReferenceRequirements, GenerationPlan, StrategySelectionContext, EnrichmentError, M2 DB tables |
| 2. M2 Repositories | Task 1 models, M1 repositories.py | BeatRepository, ShotRepository, GenerationPlanRepository, CharacterRepository.get_character |
| 3. BeatEnricher | Task 1 Beat, M1 LLMProvider | beat_enricher.py, prompts.py |
| 4. CoveragePlanner + ShotSpecBuilder | Task 1 models, M1 WC adapter | coverage_planner.py, shot_spec_builder.py |
| 5. StrategySelector | Task 1 GenerationPlan + StrategySelectionContext | strategy_selector.py |
| 6. Stale Propagation | Tasks 1-2 repos | stale_propagator.py |
| 7. Enrichment Service + apply-changes integration | Tasks 2-6, M1 ImportService | enrichment_service.py |
| 8. API + Verification | Task 7, M1 routes | M2 routes, exit criteria, live Ollama smoke |

**Forward dependencies: ZERO.**

---

### Task 1: M2 Schema + Canonical Models (M2.A)

**Files:**
- Modify: `src/film_director/models/canonical.py`
- Modify: `src/film_director/persistence/database.py`
- Modify: `src/film_director/errors.py`
- Create: `tests/unit/test_beat_model.py`
- Create: `tests/unit/test_shot_spec_model.py`
- Create: `tests/unit/test_generation_plan_model.py`

**Interfaces:**
- Consumes: existing `canonical.py`, `database.py` schema, `errors.py`
- Produces: `Beat`, `ShotSubject`, `CameraIntent`, `ShotSpecificationV1`, `ReferenceRequirements`, `GenerationPlan`, `StrategySelectionContext`, `EnrichmentError`, M2 DB tables

- [ ] **Step 1: Write Beat model tests** (test_beat_model.py — valid, invalid status, valid statuses, source values, characters list)
- [ ] **Step 2: Write ShotSpecificationV1 tests** (test_shot_spec_model.py — ShotSubject validation, CameraIntent shot_size Literal, valid/invalid statuses, subjects with ref_images non-lossy)
- [ ] **Step 3: Write GenerationPlan tests** (test_generation_plan_model.py — NO engine_family, NO workflow_profile, all 5 strategies, invalid strategy rejected, seed_policy, ReferenceRequirements, selection_reason)
- [ ] **Step 4: Run tests (RED), implement all models + StrategySelectionContext + EnrichmentError + DB tables, run tests (GREEN), commit**

```bash
git commit -m "M2.A: Beat, ShotSpecificationV1, GenerationPlan — model-agnostic, no provider fields"
```

---

### Task 2: M2 Repositories (M2.B)

**Files:**
- Modify: `src/film_director/persistence/repositories.py`
- Create: `tests/unit/test_m2_repositories.py`

**Interfaces:**
- Consumes: Task 1 models, M1 Database
- Produces:
  - `BeatRepository(db)`: `save_beat`, `get_beat`, `get_beats_by_scene` (all), `get_current_beats_by_scene` (non-outdated), `mark_outdated`, `mark_beats_outdated_by_scene`
  - `ShotRepository(db)`: `save_shot`, `get_shot`, `get_shots_by_beat` (all), `get_current_shots_by_beat` (non-outdated), `get_current_shots_by_project`, `mark_outdated`, `mark_shots_outdated_by_beat`
  - `GenerationPlanRepository(db)`: `save_plan`, `get_current_plan_by_shot` (non-outdated + matching shot_version), `mark_outdated`, `mark_plan_outdated_by_shot`
  - `CharacterRepository.get_character(id, conn=)` added

**NO `delete_*` methods.** History preserved via mark_outdated + creating new records.

Tests: UPSERT, FK, restart persistence, mark_outdated, JSON round-trip (ShotSubject, CameraIntent, ReferenceRequirements), current vs historical queries.

- [ ] **Steps: TDD cycle, commit**

```bash
git commit -m "M2.B: history-preserving repositories — no physical deletion"
```

---

### Task 3: BeatEnricher (M2.C)

**Files:**
- Create: `src/film_director/enrichment/__init__.py`
- Create: `src/film_director/enrichment/prompts.py`
- Create: `src/film_director/enrichment/beat_enricher.py`
- Create: `tests/unit/test_beat_enricher.py`

**Interfaces:**
- Consumes: `Scene`, `LLMProvider.chat(messages, expect_json=True)` → `.parsed: dict`
- Produces: `BeatEnricher(llm: LLMProvider)` with `enrich_scene(scene, script_context=None) -> list[Beat]`

**LLM contract:** Calls `chat(messages, expect_json=True)`, reads `response.parsed["beats"]`. If key missing or items invalid → one domain repair prompt → `EnrichmentError` on second failure.

Tests (mocked LLM): valid `{"beats":[...]}`, missing `"beats"` key → repair, malformed JSON → LLMStructuredOutputError (from provider), empty beats array → EnrichmentError, missing required fields → repair → EnrichmentError.

- [ ] **Steps: TDD cycle, commit**

```bash
git commit -m "M2.C: BeatEnricher — object-wrapped LLM contract with domain repair"
```

---

### Task 4: CoveragePlanner + ShotSpecBuilder (M2.D)

**Files:**
- Create: `src/film_director/enrichment/coverage_planner.py`
- Create: `src/film_director/enrichment/shot_spec_builder.py`
- Create: `tests/unit/test_coverage_planner.py`
- Create: `tests/unit/test_shot_spec_builder.py`

**Interfaces:**
- `CoveragePlanner(llm)` with `plan_coverage(beat, scene) -> list[CoverageDecision]` (transient DTOs)
- `ShotSpecBuilder()` with `build_shots(beat, coverage, storyboard_shots, characters, scene, order_start) -> list[ShotSpecificationV1]`
- ShotSpecBuilder resolves characters to `ShotSubject` with full `ref_images` (non-lossy)

Tests: CoveragePlanner with mocked LLM using `{"coverage":[...]}` wrapper. ShotSpecBuilder deterministic — character resolution, CameraIntent construction, environment from scene, non-lossy ref_images.

- [ ] **Steps: TDD cycle, commit**

```bash
git commit -m "M2.D: CoveragePlanner + ShotSpecBuilder — non-lossy character refs"
```

---

### Task 5: StrategySelector (M2.E)

**Files:**
- Create: `src/film_director/enrichment/strategy_selector.py`
- Create: `tests/unit/test_strategy_selector.py`

**Interfaces:**
- `StrategySelector()` with `select_strategy(ctx: StrategySelectionContext, shot: ShotSpecificationV1, project_aspect: str) -> GenerationPlan`
- `build_selection_context(shot: ShotSpecificationV1) -> StrategySelectionContext`
- Pure function, no LLM, no DB, no provider concepts

Tests: all 5 strategies with explicit StrategySelectionContext inputs, deterministic precedence (priority 1 beats 4 when both match), no `engine_family`/`workflow_profile` in output, `selection_reason` populated.

- [ ] **Steps: TDD cycle, commit**

```bash
git commit -m "M2.E: deterministic StrategySelector — explicit inputs, no provider fields"
```

---

### Task 6: Stale Propagation (M2.F)

**Files:**
- Create: `src/film_director/enrichment/stale_propagator.py`
- Create: `tests/unit/test_stale_propagation.py`

**Interfaces:**
- `StalePropagator(beat_repo, shot_repo, plan_repo)` with:
  - `propagate_scene_stale(scene_id, conn=) -> int`
  - `propagate_character_stale(character_id, project_id, conn=) -> int`
  - `propagate_beat_stale(beat_id, conn=) -> int`
  - `propagate_shot_stale(shot_id, conn=) -> int`
  - `propagate_project_stale(project_id, conn=) -> int`

Tests: each cascade path, entities outside cascade unchanged, already-outdated entities not double-processed.

- [ ] **Steps: TDD cycle, commit**

```bash
git commit -m "M2.F: stale propagation — scene/character/beat/shot/project cascades"
```

---

### Task 7: Enrichment Service + apply-changes Integration (M2.G)

**Files:**
- Create: `src/film_director/services/enrichment_service.py`
- Create: `tests/integration/test_enrichment_pipeline.py`
- Create: `tests/integration/test_m2_history.py`

**Interfaces:**
- `EnrichmentService(...)` with:
  - `enrich_project(project_id) -> EnrichmentResult`
  - `enrich_scene_beats(scene_id, force=False) -> list[Beat]`
  - `plan_beat_coverage(beat_id, force=False) -> list[ShotSpecificationV1]`
  - `assign_strategies(project_id) -> int`
  - `apply_stale_cascade(project_id, changes: list[ChangeDetection], conn=)` — called after M1 apply

Tests: full pipeline (mocked LLM), idempotent enrichment, human edit protection (409 without force), force override preserves old as outdated, re-enrichment after stale, history preservation tests (old beats/shots/plans exist with status=outdated after re-enrich), M1 apply-changes + M2 cascade integration.

- [ ] **Steps: TDD cycle, commit**

```bash
git commit -m "M2.G: EnrichmentService — history-preserving, M1 cascade integration"
```

---

### Task 8: API + Final Verification (M2.H)

**Files:**
- Modify: `src/film_director/api/routes.py`
- Modify: `src/film_director/main.py`
- Create: `tests/integration/test_m2_api.py`
- Create: `tests/integration/test_m2_human_edits.py`
- Create: `tests/integration/test_ollama_enrichment_live.py`

**Interfaces:** All M2 API routes, apply-changes extended with M2 cascade, exit criteria verification

Tests: all API routes, human edit lifecycle (edit → stale propagation → re-enrich rejected → force), restart persistence, M1 apply-changes cascades M2, model-agnostic scan, live Ollama with object-wrapped prompts.

- [ ] **Steps: TDD cycle, exit criteria verification, architecture scan, commit**

```bash
git commit -m "M2.H: API routes, human editing, M1 cascade, exit criteria verified"
```

---

## M2 Exit Criteria

1. For a test WC project: every scene has current beats
2. Every beat has coverage (expressed as current ShotSpecificationV1 entries)
3. Every shot has a complete model-agnostic ShotSpecificationV1
4. StrategySelector deterministically assigns REFERENCE_TO_VIDEO for character shots and TEXT_TO_VIDEO for establishing shots — using explicit StrategySelectionContext, not string heuristics
5. Beat/coverage/shot can be edited via API
6. Human edits survive restart and are not silently overwritten; re-enrich without force → 409
7. Upstream source changes via apply-changes cascade OUTDATED through M2 beats → shots → plans
8. All M2 data survives application restart
9. Re-enrichment preserves old artifacts as OUTDATED (no physical deletion)
10. All M1 tests remain green (182 deterministic + 3 live)
11. ZERO provider-specific fields in GenerationPlan (no engine_family, workflow_profile, h3_*)
12. Live Ollama enrichment smoke passes with object-wrapped contract, or M2 is BLOCKED

## Task Dependency Audit

| Task | All imports exist? | Fixtures exist? | No forward refs? | No M3? | No delete? | Verdict |
|------|-------------------|----------------|-----------------|--------|-----------|---------|
| 1. Schema+Models | M1 canonical ✓ | N/A | YES | YES | YES | PASS |
| 2. Repositories | Task 1 ✓ | N/A | YES | YES | YES (mark_outdated only) | PASS |
| 3. BeatEnricher | Task 1 ✓, M1 LLM ✓ | N/A | YES | YES | YES | PASS |
| 4. Coverage+ShotSpec | Tasks 1,3 ✓ | N/A | YES | YES | YES | PASS |
| 5. StrategySelector | Task 1 ✓ | N/A | YES | YES | YES | PASS |
| 6. StalePropagator | Tasks 1,2 ✓ | N/A | YES | YES | YES | PASS |
| 7. EnrichmentService | Tasks 2-6 ✓ | FixtureDB ✓ | YES | YES | YES | PASS |
| 8. API+Verification | Task 7 ✓ | All ✓ | YES | YES | YES | PASS |

**Forward dependencies: ZERO.**

## Architecture Self-Review

| Check | Result |
|-------|--------|
| ADR-001: WC sidecar, not modified | PASS |
| ADR-002: Canonical independent of WC | PASS |
| ADR-003: ComfyUI REST only | PASS — no ComfyUI in M2 |
| ADR-004: MCP dev only | PASS |
| ADR-005: Provider-specific separated | PASS — GenerationPlan has NO engine_family, workflow_profile, h3_* |
| ShotSpecificationV1 model-agnostic | PASS — zero provider fields |
| GenerationPlan model-agnostic | PASS — strategy enum + reference_requirements only |
| No M3 scope | PASS |
| No physical deletion | PASS — history-preserving mark_outdated + new records |
| Human edits preserved | PASS — source field, force param, version, 409 rejection |
| Stale propagation integrated with M1 apply | PASS |
| LLM contract M1-compatible | PASS — object root via expect_json=True |
| Character refs non-lossy | PASS — ShotSubject.ref_images carries full list |
| Strategy inputs explicit | PASS — StrategySelectionContext with booleans/integers |
| Strategy precedence defined | PASS — priority 1-5 table |
| Error semantics clear | PASS — LLMStructuredOutputError vs EnrichmentError vs NormalizationError |
