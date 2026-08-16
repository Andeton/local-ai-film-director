# M4 Wind Comic Production Handoff — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between user idea and canonical production data by programmatically triggering Wind Comic pre-production, observing completion, importing richer WC outputs (script dialogue/action/emotion, storyboard camera/lighting, Director context), and making enrichment consume source facts instead of regenerating them.

**Architecture:** M4 adds the WC pre-production orchestration layer ABOVE the existing M1 import boundary. Wind Comic remains the read-only pre-production sidecar (ADR-001). LFDirector calls WC's HTTP API to trigger pre-production; WC writes its own database; LFDirector reads the completed result through the existing read-only WindComicAdapter. A source-neutral `ShotSourceFacts` transport DTO carries normalized WC facts into the enrichment pipeline where deterministic field-by-field precedence ensures upstream facts override LLM inference.

**Tech Stack:** Python 3.14.3, FastAPI, Pydantic v2, SQLite, httpx (WC HTTP + SSE), pytest

## Global Constraints

- Architecture FROZEN V1 — ADR-001 through ADR-005 remain unchanged
- ADR-001: Wind Comic is read-only sidecar. LFDirector does NOT write to WC SQLite.
- LFDirector MAY call WC's own HTTP API to trigger WC's own pipeline — WC owns its writes.
- SSE events are for orchestration/progress/completion — NOT a canonical import source.
- WC SQLite via read-only WindComicAdapter remains the persisted import source of truth.
- ShotSourceFacts is a TRANSPORT DTO — consumed during enrichment, not persisted separately.
- Canonical fields populated from ShotSourceFacts ARE persisted on existing canonical models.
- Dialogue is preserved verbatim. Speaker identity resolved ONLY when deterministically provable.
- Source precedence is deterministic field-by-field merge, NOT prompt wording.
- M4 execution is SYNCHRONOUS — no persistent job/queue entity.
- Existing M0-M3 tests (673 deterministic + 6 live) must remain green.
- No M5 reference management scope (no character image generation, reference ranking, multi-ref H3).
- No UI/launcher. No TTS/lip-sync. No timeline/export. No direct WC DB writes.

## Frozen Canonical Additions

### DialogueIntent (Pydantic model, NOT persisted separately)

```python
class DialogueIntent(BaseModel):
    text: str                           # verbatim dialogue string from source
    speaker_character_id: str | None    # canonical CharacterReference.id IF proven
    speaker_name: str | None            # source speaker name IF proven (None if ambiguous)
    emotion: str = ""                   # emotional tone from source
```

Stored as serialized dict inside `ShotSpecificationV1.audio_intent["dialogue"]`. For M4: at most ONE DialogueIntent per shot (matching WC's single dialogue string per shot).

### Speaker Resolution Rule

1. Dialogue non-empty AND WC characters[] has exactly ONE entry AND that name resolves uniquely (casefold+strip) to one canonical CharacterReference in the current project → `speaker_character_id = CR.id`, `speaker_name = WC source name`
2. Otherwise → `speaker_character_id = None`, `speaker_name = None`
3. NEVER parse dialogue text for speaker markers
4. NEVER preserve unresolved WC name in speaker_name — if identity is not proven, speaker_name is None

### ShotSourceFacts (frozen Pydantic transport DTO)

```python
class ShotSourceFacts(BaseModel, frozen=True):
    source_project_id: str
    source_script_shot_number: int | None = None
    source_storyboard_asset_id: str | None = None
    action: str | None = None           # script.shots[].action (if non-template)
    emotion: str | None = None          # script.shots[].emotion (if non-template)
    dialogue_text: str | None = None    # script.shots[].dialogue (verbatim)
    characters: list[str] = []          # script.shots[].characters (source names)
    duration_sec: float | None = None   # storyboard.data.duration
    camera_angle: str | None = None     # parsed from storyboard description
    camera_movement: str | None = None  # parsed from storyboard description
    lighting: str | None = None         # parsed from storyboard description
    storyboard_description: str = ""    # verbatim storyboard.data.description
    storyboard_image_path: str | None = None
```

### ProductionProject.director_context

New DB column: `director_context TEXT NOT NULL DEFAULT '{}'`

```python
# On ProductionProject model:
director_context: dict = Field(default_factory=dict)
```

Structure: `{"genre": "...", "style": "...", "story_structure": {"acts": N, "totalShots": N}}`

### Storyboard Original Text

Persisted in: `ShotSpecificationV1.environment["storyboard_description"]`

Already a JSON dict column — no schema migration needed for this field.

## Source Precedence Table

| ShotSpec Field | Source Fact | Component | Fallback |
|---|---|---|---|
| action | ShotSourceFacts.action | ShotSpecBuilder | beat.dramatic_action |
| duration_sec | ShotSourceFacts.duration_sec | ShotSpecBuilder | coverage default / 5.0 |
| camera | ShotSourceFacts.camera_angle/movement | ShotSpecBuilder | CoveragePlanner decision |
| lighting | ShotSourceFacts.lighting | ShotSpecBuilder | empty {} |
| dramatic_purpose | ShotSourceFacts.emotion | ShotSpecBuilder | coverage.purpose |
| audio_intent.dialogue | ShotSourceFacts.dialogue_text | ShotSpecBuilder | empty |
| subjects | ShotSourceFacts.characters (resolved) | ShotSpecBuilder | beat.characters |
| environment.storyboard_description | ShotSourceFacts.storyboard_description | ShotSpecBuilder | absent |

Human canonical edits always take highest precedence (existing M2 convention).

## WC SSE Contract (M4-relevant subset)

| Event | Payload | M4 Usage |
|---|---|---|
| `projectId` | `{projectId}` | Capture WC project ID for later import |
| `step` | `{step: string}` | Progress tracking |
| `plan` | Full DirectorPlan | Progress (not used for import — DB is authoritative) |
| `script` | Full Script | Progress (not used for import — DB is authoritative) |
| `characters` | Array | Progress |
| `scenes` | Array | Progress |
| `storyboardPlans` | Array | Progress |
| `complete` | Full pipeline result | Terminal: proceed to DB import |
| `error` | `{message, code?, retryable?, stage?}` | Terminal: fail request |

## M4 Error Taxonomy

| Error | HTTP | When |
|---|---|---|
| `WindComicUnavailableError` | 503 | WC not reachable, auth failure |
| `WindComicPreproductionError` (NEW) | 502 | WC pipeline error/timeout |
| `NormalizationError` | 500 | Import/normalization failure |
| `EnrichmentError` | 422 | Enrichment validation failure |

## File Structure

```
src/film_director/
  adapters/
    wind_comic.py              # MODIFY: extend read_project_bundle, add storyboard/script reading
  adapters/
    wind_comic_preproduction.py # CREATE: WC HTTP SSE client + auth
  models/
    canonical.py               # MODIFY: DialogueIntent, ProductionProject.director_context
    wind_comic_dto.py          # MODIFY: add WCScript, WCDirectorPlan DTOs
    source_facts.py            # CREATE: ShotSourceFacts, StoryboardParser
  enrichment/
    shot_spec_builder.py       # MODIFY: accept + apply ShotSourceFacts
  services/
    import_service.py          # MODIFY: build ShotSourceFacts during import
    preproduction_service.py   # CREATE: idea → WC → import → enrich orchestrator
  persistence/
    database.py                # MODIFY: add director_context column
    repositories.py            # MODIFY: save/load director_context
  errors.py                    # MODIFY: add WindComicPreproductionError
  config.py                    # MODIFY: add WC auth settings
  api/routes.py                # MODIFY: add POST /projects/from-idea
  main.py                      # MODIFY: wire PreproductionService
tests/
  unit/
    test_wind_comic_preproduction.py    # SSE client + auth (fake transport)
    test_source_facts.py                # ShotSourceFacts + StoryboardParser
    test_dialogue_intent.py             # DialogueIntent + speaker resolution
    test_shot_spec_builder_precedence.py # source fact precedence
  integration/
    test_rich_import.py                 # script/storyboard import
    test_preproduction_service.py       # full mocked pipeline
    test_m4_api.py                      # API endpoint tests
    test_wc_live.py                     # real WC pipeline (live)
```

---

## Task Dependency Table

| Task | Requires | Produces |
|---|---|---|
| M4.A | Settings, errors | WC preproduction client, auth, SSE parser, typed events |
| M4.B | canonical models | DialogueIntent, ShotSourceFacts, director_context, DB migration |
| M4.C | M4.A (DTOs), M4.B (models) | Rich WC normalization, storyboard parser, import extension |
| M4.D | M4.B, M4.C | Source precedence in ShotSpecBuilder, enrichment integration |
| M4.E | M4.A, M4.C, M4.D | PreproductionService orchestrator |
| M4.F | M4.C | Reimport/stale for script/storyboard changes |
| M4.G | M4.E | API entry point |
| M4.H | M4.G, WC + LLM running | Live acceptance |

**Forward dependencies: ZERO.**

---

### Task M4.A: WC Preproduction Client + Auth

**Goal:** HTTP SSE client for WC `/api/create-stream` with JWT auth.

**Files:** Create `adapters/wind_comic_preproduction.py`. Modify `config.py`, `errors.py`. Create `tests/unit/test_wind_comic_preproduction.py`.

**Settings additions:**
- `windcomic_base_url: str = "http://127.0.0.1:3000"`
- `windcomic_email: str = ""`
- `windcomic_password: str = ""`

**Error additions:**
- `WindComicPreproductionError(FilmDirectorError)` — WC pipeline error/timeout

**WindComicPreproductionClient:**
- `login()` — POST /api/auth/login, cache JWT
- `create_project(idea, style?, aspect?, language?)` — POST /api/create-stream with SSE
- SSE event parsing: typed DTOs for projectId, step, complete, error
- Completion: SSE "complete" event → return WC project ID
- Failure: SSE "error" event → raise WindComicPreproductionError
- Timeout: configurable, raise WindComicPreproductionError
- Connection failure: raise WindComicUnavailableError

Tests: fake HTTP transport for auth, fake SSE stream for events, timeout, error, auth failure.

- [ ] **Steps: TDD, settings, errors, client, tests, commit**

```bash
git commit -m "M4.A: WC preproduction SSE client with JWT auth"
```

---

### Task M4.B: Canonical Models + DB Migration

**Goal:** DialogueIntent, ShotSourceFacts, ProductionProject.director_context with DB migration.

**Files:** Create `models/source_facts.py`. Modify `models/canonical.py`, `persistence/database.py`, `persistence/repositories.py`. Create `tests/unit/test_source_facts.py`, `tests/unit/test_dialogue_intent.py`.

**DialogueIntent:**
- Pydantic model with: text, speaker_character_id, speaker_name, emotion
- Validation: text required if present, speaker rules documented above
- Serialized as dict inside audio_intent["dialogue"]

**ShotSourceFacts:**
- Frozen Pydantic model with all approved fields
- Transport-only — consumed by ShotSpecBuilder, not persisted separately

**StoryboardParser:**
- Conservative regex for known WC markers: `camera angle:`, `lighting:`, `color tone:`, `composition:`
- Returns parsed fields or None per field
- Malformed/missing markers → None (fallback to enrichment)

**ProductionProject.director_context:**
- New field: `director_context: dict = Field(default_factory=dict)`
- DB migration: `ALTER TABLE production_projects ADD COLUMN director_context TEXT NOT NULL DEFAULT '{}'`
- Repository: save/load director_context as JSON

Tests: DialogueIntent speaker rules, ShotSourceFacts validation, StoryboardParser (valid/partial/malformed/missing), director_context round-trip.

- [ ] **Steps: TDD, models, migration, repository, tests, commit**

```bash
git commit -m "M4.B: DialogueIntent, ShotSourceFacts, director_context models"
```

---

### Task M4.C: Rich WC Normalization + Import

**Goal:** Import script shots, storyboard data, and Director context from WC.

**Files:** Modify `adapters/wind_comic.py`, `models/wind_comic_dto.py`, `services/import_service.py`. Create `tests/integration/test_rich_import.py`.

**WC DTO additions:**
- `WCScriptShot`: shotNumber, sceneDescription, characters, dialogue, action, emotion
- `WCDirectorPlan`: genre, style, storyStructure

**WindComicAdapter extensions:**
- `read_project_bundle` now also reads:
  - `projects.script_data` → parsed into list[WCScriptShot]
  - `project_assets WHERE type='plan'` → WCDirectorPlan
  - `project_assets WHERE type='storyboard'` → already exists as WCStoryboardShot
- Bundle gains: script_shots, director_plan, storyboard_shots

**ImportService extensions:**
- Build ShotSourceFacts by correlating script shots and storyboard shots via shotNumber
- Apply StoryboardParser to extract camera/lighting from storyboard description
- Persist director_context on ProductionProject
- Persist storyboard_description in environment on ShotSpec (via enrichment flow)

**Source hash extension:**
- Include script_data content and storyboard data in project source hash computation

Tests: full import with WC DB fixture containing script+storyboard, round-trip verification, source fact correlation, director context, missing script graceful handling.

- [ ] **Steps: TDD, DTOs, adapter, import, tests, commit**

```bash
git commit -m "M4.C: rich WC import with script/storyboard/director data"
```

---

### Task M4.D: Source Precedence in ShotSpecBuilder

**Goal:** ShotSpecBuilder consumes ShotSourceFacts, applies field-by-field precedence.

**Files:** Modify `enrichment/shot_spec_builder.py`, `services/enrichment_service.py`. Create `tests/unit/test_shot_spec_builder_precedence.py`.

**ShotSpecBuilder changes:**
- `build_shots()` accepts optional `source_facts: dict[int, ShotSourceFacts]` keyed by shotNumber
- For each shot, if matching source fact exists:
  - action: source fact action (if non-empty/non-template) → else beat.dramatic_action
  - dramatic_purpose: source fact emotion (if non-empty/non-template) → else coverage.purpose
  - camera: parsed source camera_angle/movement → else coverage camera
  - lighting: parsed source lighting → else {}
  - audio_intent["dialogue"]: DialogueIntent from source dialogue_text + resolved speaker
  - environment["storyboard_description"]: verbatim storyboard_description
  - duration_sec: source duration (ALREADY DONE) → else coverage default
  - subjects: source characters resolved via existing _resolve_characters → else beat.characters
- Template detection: WC template defaults like "动作"/"情绪" treated as empty (not real content)

**EnrichmentService changes:**
- `enrich_project()` / `plan_beat_coverage()`: load storyboard shots, build source facts dict, pass to ShotSpecBuilder
- Existing `storyboard_shots=[]` replaced with actual storyboard data

**Backward compatibility:**
- Empty/None source_facts → existing behavior unchanged
- All M2 tests pass without modification (source_facts defaults to None)

Tests: source fact applied for each field, template values skipped, mixed (some facts present, some missing), human-edited shot not overwritten, backward compatibility with no source facts.

- [ ] **Steps: TDD, precedence, enrichment wiring, backward compat, tests, commit**

```bash
git commit -m "M4.D: deterministic source-fact precedence in ShotSpecBuilder"
```

---

### Task M4.E: PreproductionService

**Goal:** Orchestrate idea → WC SSE → import → enrich in one synchronous call.

**Files:** Create `services/preproduction_service.py`. Create `tests/integration/test_preproduction_service.py`.

**PreproductionService:**
- `create_from_idea(idea, style?, aspect?, language?) → ProductionProject`
- Steps:
  1. Authenticate to WC via WindComicPreproductionClient
  2. POST /api/create-stream with idea
  3. Consume SSE events until complete/error
  4. Extract WC project ID from projectId event
  5. Wait for "complete" event (or handle "error"/timeout)
  6. After completion: import via existing ImportService
  7. Validate required artifacts (script shots, characters, scenes)
  8. Run enrichment via existing EnrichmentService (with source facts)
  9. Return canonical ProductionProject

**Failure handling:**
- WC unavailable → WindComicUnavailableError
- Auth failure → WindComicUnavailableError
- SSE error event → WindComicPreproductionError
- SSE timeout → WindComicPreproductionError
- Import validation failure (missing script/characters/scenes) → NormalizationError
- Enrichment failure → propagated as-is

Tests: full pipeline with fake WC client, failure at each stage, partial artifact handling.

- [ ] **Steps: TDD, service, failure handling, tests, commit**

```bash
git commit -m "M4.E: PreproductionService orchestrator"
```

---

### Task M4.F: Reimport + Stale Propagation

**Goal:** Detect script/storyboard/director changes and propagate stale.

**Files:** Modify `services/import_service.py`, `models/provenance.py`. Create tests in `tests/integration/test_rich_import.py` (extend).

**Source hash extensions:**
- `build_project_source_payload` includes script_data hash
- New `build_storyboard_source_payload` for storyboard content
- Director context changes detected via project source hash

**Stale propagation:**
- Changed script → Beats and Shots for affected scenes marked outdated
- Changed storyboard → Shots with matching wc_storyboard_id marked outdated
- Changed Director context → project-level change, scenes may become stale
- Existing M1/M2 stale propagator handles downstream cascade
- GenerationRequests/Takes NEVER modified or deleted

Tests: re-import detects changed script, re-import detects changed storyboard, old Takes preserved, stale cascade.

- [ ] **Steps: TDD, hash extension, stale propagation, tests, commit**

```bash
git commit -m "M4.F: reimport stale propagation for script/storyboard changes"
```

---

### Task M4.G: API Entry Point

**Goal:** Synchronous POST /projects/from-idea.

**Files:** Modify `api/routes.py`, `main.py`. Create `tests/integration/test_m4_api.py`.

**Route:**
```
POST /projects/from-idea
Body: {idea: str, style?: str, aspect?: str, language?: str}
Response: {project_id, title, shots_created, plans_created, ...}
```

Thin over PreproductionService. Synchronous.

**Error mapping:**
- WindComicUnavailableError → 503
- WindComicPreproductionError → 502
- NormalizationError → 500
- EnrichmentError → 422

**Main wiring:**
- Instantiate WindComicPreproductionClient with Settings
- Instantiate PreproductionService
- Pass to create_router

Tests: successful mocked pipeline, missing idea, WC unavailable, WC error.

- [ ] **Steps: TDD, route, wiring, error mapping, tests, commit**

```bash
git commit -m "M4.G: synchronous POST /projects/from-idea API"
```

---

### Task M4.H: Live Acceptance

**Goal:** One real idea → WC pipeline → canonical import with source facts.

**Files:** Create `tests/integration/test_wc_live.py`.

**Prerequisites:**
- WC running at localhost:3000
- WC user registered with sufficient budget
- 14B+ LLM model configured for WC (qwen2.5:14b or equivalent)

**Test:**
- Mark `@pytest.mark.live`
- POST a real English idea (30+ chars)
- Wait for WC pipeline completion (SSE)
- Import via WindComicAdapter (read-only)
- Verify:
  - Script shots exist with dialogue/action/emotion fields
  - Characters exist
  - Scenes exist
  - Storyboard data correlated to script shots
  - DirectorContext persisted on ProductionProject
  - Source facts applied in canonical shots
  - Storyboard description preserved verbatim in environment
  - Camera/lighting extracted where parseable
  - No WC SQLite mutation by LFDirector
  - No fabricated speaker identity

**If WC LLM quality prevents valid screenplay JSON:** M4.H = BLOCKED.

- [ ] **Steps: live test, verify source facts, verify provenance, commit**

```bash
git commit -m "M4.H: live WC production handoff acceptance"
```

---

## M4 Exit Criteria

1. POST /projects/from-idea triggers WC pre-production via HTTP SSE
2. LFDirector observes WC completion/failure via SSE events (not DB polling)
3. Script dialogue preserved verbatim in canonical audio_intent["dialogue"]
4. Script action/emotion preserved in canonical shot fields when non-template
5. Storyboard camera/lighting extracted where parseable (conservative regex)
6. Storyboard description preserved verbatim in environment["storyboard_description"]
7. ShotSpecBuilder uses source facts when available, LLM fills gaps only
8. Director plan genre/style preserved in ProductionProject.director_context
9. Re-import detects script/storyboard changes and propagates stale
10. No direct mutation of WC SQLite by LFDirector
11. One real idea → WC → canonical import works end-to-end (live)
12. M0-M3 regression tests remain green (673 deterministic)
13. No M5 reference-management scope leak

## Task Dependency Audit

| Task | Forward refs? | M5+? | Verdict |
|---|---|---|---|
| M4.A | NO | NO | PASS |
| M4.B | NO | NO | PASS |
| M4.C | NO | NO | PASS |
| M4.D | NO | NO | PASS |
| M4.E | NO | NO | PASS |
| M4.F | NO | NO | PASS |
| M4.G | NO | NO | PASS |
| M4.H | NO | NO | PASS |

**Forward dependencies: ZERO.**

## LLM Prerequisite

Current WC model (gemma4:e4b) FAILS strict JSON parsing. M4.A-G implementation proceeds regardless. M4.H live acceptance requires a WC-compatible model:
- Install qwen2.5:14b or qwen3:14b via `ollama pull`
- OR configure WC to use OpenRouter with a capable cloud model
- Exact model is runtime configuration, not architecture.
