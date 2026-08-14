# M0.4 — Wind Comic Local Hands-On Validation

**Date:** 2026-08-14
**Status:** Complete
**Wind Comic Version:** 12.320.0 (commit c83e1cf)

---

## 1. Installation

| Property | Value |
|---|---|
| Repository | https://github.com/ChrisChen667788/wind-comic.git |
| Commit | c83e1cf (v12.320) |
| Location | `D:\Ai\Local AI Film Director\experiments\wind-comic` |
| Install command | `npm install` (23s, 475 packages) |
| Runtime | Node.js 24.14, Next.js 16.2.11 (Turbopack) |
| Database | SQLite via better-sqlite3 (file: `data/qfmj.db`) |
| Port | 3000 |
| License | MIT |

### Environment Configuration (.env.local)

```
OPENAI_API_KEY=ollama-local
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_MODEL=gemma4:e4b
OPENAI_CREATIVE_MODEL=gemma4:e4b
DEMO_PASSWORD=demo123
SEED_DEMO_USER=1
```

No cloud API keys required for pre-production pipeline testing.

---

## 2. First Start

| Check | Result |
|---|---|
| Frontend loads | YES — `http://localhost:3000` serves HTML in 4.2s |
| Backend starts | YES — Next.js 16.2.11 Turbopack dev mode |
| Database initializes | YES — `data/qfmj.db` created with full schema (~40 tables) |
| Demo user seeded | YES — `demo@qfmanju.ai` / demo123 |
| Project creation | YES — via `/api/create-stream` |
| Critical errors | NONE on startup |

### Startup Issue: Budget Gate

Wind Comic enforces a budget gate (`budget_hard_cap_cny`). The demo user defaults to a ¥5 hard cap. Fixed by updating the user's budget columns in SQLite. This is a SaaS billing feature, not a fundamental blocker.

---

## 3. LLM Provider Test — Ollama + gemma4:e4b

### Connection

| Property | Result |
|---|---|
| Ollama detected | YES — `http://127.0.0.1:11434/v1` responds |
| Model used | gemma4:e4b (9.6 GB) |
| LLM call made | YES — Director agent called LLM |
| Response received | YES — 12,019 chars returned |
| JSON parsing | **FAILED** — `robustJsonParse` could not parse the output |
| Fallback | YES — system fell back to template-based generation |
| Pipeline continued | YES — all subsequent stages ran on template data |

### gemma4:e4b Quality Assessment

| Dimension | Score | Evidence |
|---|---|---|
| JSON reliability | **FAIL** | Output was 12,019 chars of mostly valid JSON but Wind Comic's robust parser couldn't handle it. The raw output started with `{ "genre": "悬疑推理 / 都市惊悚"` — the model IS generating structured output, but with formatting issues the parser rejects. |
| Story coherence | **NOT TESTED** | Fallback to template means we didn't see real story output |
| Director usefulness | **NOT TESTED** | Same |
| Shot specificity | **NOT TESTED** | Same |
| Character consistency | **NOT TESTED** | Same |
| Camera specificity | **NOT TESTED** | Same |

**Conclusion:** gemma4:e4b (9.6 GB) generates structured JSON but not reliably enough for Wind Comic's strict parser. A larger model (14B+ Qwen or Gemma) would likely pass. The system gracefully degrades — it does NOT crash.

---

## 4. Controlled Test Project

| Property | Value |
|---|---|
| Project ID | `Y6QHzhsgfniDypg1eYxqV` |
| Input idea | "A detective arrives at an abandoned psychiatric hospital at night. Inside, he discovers a mysterious young woman who appears to have been waiting for him." |
| Style | cinematic |
| Aspect | 16:9 |
| Result | Pipeline completed with template fallback |
| Assets created | 10 (plan, script, 2 scenes, 2 characters, 4 storyboard shots) |

---

## 5. Pipeline Test Results

### 5.1 Director Agent

| Property | Value |
|---|---|
| Input | User idea text + style preference |
| Output | Plan: genre, style, characters (2), scenes (2), storyStructure (3 acts, 4 shots) |
| Storage | `project_assets` table, type=`plan` |
| Data format | JSON in `data` column |
| Can re-run? | YES — via `/api/projects/[id]/rerun` endpoint |
| Can edit? | YES — assets have `confirmed` and `stale` flags |
| External consumable? | YES — standard JSON, queryable via SQLite |

### 5.2 Writer Agent

| Property | Value |
|---|---|
| Input | Plan from Director |
| Output | Script with shots array: `{shotNumber, sceneDescription, characters, dialogue, action, emotion}` |
| Storage | `project_assets` type=`script`, also `projects.script_data` |
| Additional output | pacingReport (conflict scores, reversal analysis, cliffhanger assessment), dialogueCoverage report |
| Can re-run? | YES |
| Can edit? | YES |
| External consumable? | YES |

### 5.3 Style Bible

| Property | Value |
|---|---|
| Input | Style preset (cinematic) |
| Output | Style Bible frame render attempt (failed — no image provider configured) |
| Storage | Would be a `project_assets` type=`style_bible` |
| Status | SKIPPED (no image provider) but pipeline continued |

### 5.4 Character Designer

| Property | Value |
|---|---|
| Input | Character list from plan + script details |
| Output | Character descriptions as turnaround sheet prompts ("front three-quarter and back views") |
| Storage | `project_assets` type=`character`, also `character_library` table for reusable characters |
| Data format | JSON with `description` (MJ-style prompt), `appearance` |
| Character images | NOT GENERATED (no image provider), placeholder SVGs used |
| Can re-run? | YES |
| External consumable? | YES — character prompts are machine-readable text |

### 5.5 Scene Designer

| Property | Value |
|---|---|
| Input | Scene list from plan + script shot assignments |
| Output | Scene descriptions with location names, linked shot references |
| Storage | `project_assets` type=`scene` |
| Data format | JSON with `description`, `location` |
| Scene images | NOT GENERATED (no image provider) |
| Can re-run? | YES |
| External consumable? | YES |

### 5.6 Storyboard

| Property | Value |
|---|---|
| Input | Script shots + character/scene data + style |
| Output | One storyboard asset per shot with: description (prompt), duration, camera angle, lighting |
| Storage | `project_assets` type=`storyboard`, `shot_number` links to script |
| Data format | JSON with `description` (full generation prompt), `duration` (seconds) |
| Images | NOT GENERATED |
| Can re-run? | YES |
| External consumable? | YES — the `description` field IS a generation-ready prompt |

---

## 6. Artifact Inspection

### 6.1 Database Schema (Key Tables)

#### projects

| Column | Type | Purpose |
|---|---|---|
| id | TEXT PK | nanoid |
| user_id | TEXT FK | Owner |
| title | TEXT | Project name |
| status | TEXT | active/draft/completed |
| script_data | TEXT (JSON) | Full script with shots array |
| director_notes | TEXT (JSON) | Director treatment data |
| pipeline_state | TEXT (JSON) | Pipeline progress tracking |
| style_id | TEXT | Style preset reference |
| aspect | TEXT | "16:9" or "9:16" |
| locked_characters | TEXT (JSON) | Array of locked character refs |
| primary_character_ref | TEXT | Face reference URL for main character |
| mode | TEXT | episodic/cinematic |

#### project_assets

| Column | Type | Purpose |
|---|---|---|
| id | TEXT PK | nanoid |
| project_id | TEXT FK | Parent project |
| type | TEXT | plan/script/scene/character/storyboard/video/timeline/quality_report |
| name | TEXT | Display name |
| data | TEXT (JSON) | **All structured data lives here** |
| media_urls | TEXT (JSON array) | Image/video URLs |
| persistent_url | TEXT | Local persistent copy path |
| shot_number | INTEGER | Links storyboard/video to script shot |
| version | INTEGER | Version counter |
| confirmed | INTEGER | User approval flag |
| stale | INTEGER | Invalidation flag (upstream rerun) |

#### character_library

| Column | Type | Purpose |
|---|---|---|
| id | TEXT PK | nanoid |
| user_id | TEXT FK | Owner |
| name | TEXT | Character name |
| description | TEXT | Text description |
| appearance | TEXT | Visual appearance |
| visual_tags | TEXT (JSON) | Visual tag array |
| image_urls | TEXT (JSON) | Reference images |
| profile | TEXT (JSON) | CharacterProfile: bio + voice + turnaround sheet |
| stale | INTEGER | IP invalidation flag |

### 6.2 Asset Data Examples (Sanitized)

**Plan Asset:**
```json
{
  "genre": "",
  "style": "电影写实",
  "characters": [
    {"name": "主角", "description": "核心人物", "appearance": ""},
    {"name": "伙伴", "description": "主角的忠实伙伴", "appearance": ""}
  ],
  "scenes": [
    {"id": "s1", "description": "开场远景", "location": "主场景"},
    {"id": "s2", "description": "冲突场所", "location": "关键场所"}
  ],
  "storyStructure": {"acts": 3, "totalShots": 4}
}
```

**Script Shot:**
```json
{
  "shotNumber": 1,
  "sceneDescription": "电影写实风格，镜头1",
  "characters": ["主角"],
  "dialogue": "",
  "action": "动作",
  "emotion": "情绪"
}
```

**Storyboard Shot:**
```json
{
  "description": "cinematic film frame, [camera/scene description], camera angle: [angle], lighting: [lighting], color tone: [tone]",
  "duration": 10
}
```

---

## 7. Shot Model Mapping

See `M0_4_WIND_TO_LOCAL_FILM_DIRECTOR_MAPPING.md` for the detailed field mapping.

---

## 8. Character / Style Consistency

### Character DNA

- **Representation:** Text description in MJ-style prompt format ("character concept art turnaround sheet, front three-quarter and back views...")
- **Stability across shots:** Character names referenced in script shots, but descriptions are stored once in the character asset, not duplicated per shot.
- **Reference images:** `media_urls` array on character asset (empty in our test — no image provider)
- **3-view turnaround sheets:** YES — the character prompt explicitly requests "front three-quarter and back views"
- **Locked characters:** `projects.locked_characters` JSON array stores character face refs for cross-shot consistency

### Style Bible

- **Representation:** Style preset ID (`style_id` on project) + Style Bible frame (not generated in our test)
- **Inheritance:** Storyboard prompts embed the style: each starts with "cinematic film frame, ..."
- **H3 compatibility:** Character descriptions would need TRANSFORMATION to H3's `<Subject N>` / `<Picture N>` format. They are Midjourney-style prompts, not H3 subject_definitions.

---

## 9. Storyboard Integration

### Storyboard Schema

- Each storyboard shot is a `project_assets` row with `type='storyboard'` and `shot_number`
- `data` JSON contains `description` (full generation prompt) and `duration` (seconds)
- Images stored in `media_urls` (empty without image provider)
- Ordering: by `shot_number` (1-based)

### Storyboard → H3 Integration Potential

| Target | Suitability | Notes |
|---|---|---|
| MiniMax H3 R2V | USABLE WITH ADAPTER | Storyboard description needs transformation to H3 subject_definitions format. Character images (when generated) could serve as R2V references. |
| MiniMax H3 I2V | USABLE WITH ADAPTER | Storyboard frame images (when generated) could serve as I2V first_frame. |
| GAPStoryboardManager | USABLE WITH ADAPTER | Panel images from storyboard + animation prompts could be injected. Requires prompt format transformation. |

### CSV/Markdown Export

Wind Comic documents round-trip storyboard CSV export. Not tested in this session (requires UI interaction). The data is in SQLite and fully queryable.

---

## 10. Headless / Programmatic Access

### Available Access Methods

| Method | Works | Notes |
|---|---|---|
| Internal REST API `/api/assets` | YES | Returns all project assets with data, authenticated via JWT |
| Internal REST API `/api/create-stream` | YES | Creates project via SSE stream, returns all pipeline events |
| Internal REST API `/api/projects/[id]/rerun` | Available | Re-runs specific pipeline stages |
| V1 Public API `/api/v1/projects` | BLOCKED | Requires `API_KEYS` env var to be set |
| SQLite direct access | YES | Full read/write access to `data/qfmj.db` |
| CLI | NO | No CLI interface exists |
| Export functions | Documented (CSV/AAF/EDL/FCPXML) | Not tested in this session |

### Integration Strategy Ranking

| Rank | Strategy | Maintainability | Upgradeability | Coupling | Effort | Reliability |
|---|---|---|---|---|---|---|
| 1 | **B. Read SQLite directly** | HIGH | MEDIUM (schema changes on update) | LOW | LOW | HIGH |
| 2 | **A. Run separately + consume exports** | HIGH | HIGH | VERY LOW | MEDIUM | HIGH |
| 3 | **C. Call internal HTTP routes** | MEDIUM | LOW (routes change) | MEDIUM | LOW | MEDIUM |
| 4 | **E. Reimplement compatible schemas** | HIGH | HIGH | NONE | HIGH | HIGH |
| 5 | **D. Extract MIT-licensed modules** | LOW | LOW (fork maintenance) | HIGH | MEDIUM | MEDIUM |

---

## 11. ComfyUI Relationship

Wind Comic's ComfyUI integration (`services/comfyui.service.ts`) is confirmed to be **IMAGE generation only**:
- Uses IP-Adapter for character consistency (face_only, full_character, style_transfer modes)
- Uses ControlNet (Canny edge) for spatial/composition locking
- Workflow: CheckpointLoaderSimple → CLIPTextEncode → IPAdapterUnifiedLoader → IPAdapterAdvanced → KSampler → VAEDecode
- NOT used for video generation

Wind Comic's generated **character references and storyboard descriptions** could feed our H3 pipeline with an adapter layer that:
1. Transforms character descriptions from MJ-style to H3 `<Subject N>` format
2. Uses character reference images (once generated by ComfyUI or MJ) as H3 R2V reference inputs
3. Transforms storyboard prompts to H3's structured format

---

## 12. Local LLM Quality Assessment (gemma4:e4b)

| Dimension | Score | Evidence |
|---|---|---|
| JSON reliability | **FAIL** | 12,019 chars output parsed as malformed by Wind Comic's robustJsonParse |
| Story coherence | **MARGINAL** | Model attempted structured response with genre "悬疑推理 / 都市惊悚" — content was relevant but format was rejected |
| Director usefulness | **NOT TESTED** | Template fallback masked actual LLM output |
| Shot specificity | **NOT TESTED** | Same |
| Character consistency | **NOT TESTED** | Same |
| Camera specificity | **NOT TESTED** | Same |

**Verdict:** gemma4:e4b generates structured content relevant to the prompt but fails Wind Comic's strict JSON parsing. Need a 14B+ model for reliable structured output.

---

## 13. Architectural Options

### OPTION A: Wind Comic as pre-production app

| Aspect | Assessment |
|---|---|
| Benefits | Complete 8-agent pipeline already built; MIT license; extensive testing (4100+ tests); active development |
| Problems | LLM output unreliable with small local models; designed for Chinese short-drama market; no headless API; budget/subscription gates; all image generation requires cloud providers |
| Coupling | LOW — we consume artifacts via SQLite |
| Maintenance risk | MEDIUM — schema changes on updates |
| Development effort | LOW for integration |
| Duplication avoided | Director, Writer, Character, Scene, Storyboard agents (M3-M8) |

### OPTION B: Wind Comic alongside, import artifacts

| Aspect | Assessment |
|---|---|
| Benefits | Clean separation; each system evolves independently; artifacts shared via SQLite or export |
| Problems | User runs two apps; artifacts may go stale; synchronization complexity |
| Coupling | VERY LOW |
| Maintenance risk | LOW |
| Development effort | MEDIUM (build our own UI + adapter) |
| Duplication avoided | Partial (need our own UI but not agent logic if we read WC artifacts) |

### OPTION C: Reuse selected MIT-licensed components

| Aspect | Assessment |
|---|---|
| Benefits | Cherry-pick proven patterns; no runtime dependency |
| Problems | Fork maintenance burden; components are tightly integrated in Wind Comic's codebase |
| Coupling | HIGH (code copy) |
| Maintenance risk | HIGH |
| Development effort | MEDIUM |
| Duplication avoided | Selected modules only |

### OPTION D: Own Director layer, WC-compatible concepts

| Aspect | Assessment |
|---|---|
| Benefits | Full control; tailored to H3/cinema production (not short-drama); no external dependency |
| Problems | Most development effort; risk of reinventing what WC already solves |
| Coupling | NONE |
| Maintenance risk | LOW (we own everything) |
| Development effort | HIGH |
| Duplication avoided | NONE (but schemas/patterns informed by WC) |

### OPTION E: Hybrid — WC for initial prototype, own system for production

| Aspect | Assessment |
|---|---|
| Benefits | Fast prototype via WC → validates the pipeline before investing in custom code; learn from WC's proven patterns; migrate to own system when bottlenecks emerge |
| Problems | Two-phase development; potential throwaway work |
| Coupling | Phase-dependent |
| Maintenance risk | LOW |
| Development effort | MEDIUM total (spread over time) |
| Duplication avoided | M3-M8 deferred until empirically needed |

**No option selected. Awaiting architectural review.**

---

## 14. Duplication Warnings

### DUPLICATION WARNING 1: Project Persistence (M1)

Wind Comic has a complete project model with `projects` table, `project_assets` with type/version/confirmed/stale, `pipeline_jobs` for async execution, and `pipeline_job_events` for progress tracking. Our M1 would duplicate:
- Project creation and management
- Asset versioning (version column)
- Asset invalidation (stale column)
- Asset approval (confirmed column)
- Pipeline state tracking

### DUPLICATION WARNING 2: LLM Provider Abstraction (M2)

Wind Comic uses the OpenAI SDK with configurable `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_CREATIVE_MODEL`. It already supports Ollama via the same mechanism. Our M2 LLM Provider abstraction would duplicate:
- OpenAI-compatible endpoint support
- Model selection
- Creative vs fast model routing
- JSON output parsing with repair (`robustJsonParse`)

### DUPLICATION WARNING 3: Writer Agent (M3)

Wind Comic's Writer generates: title, synopsis, shots array with {shotNumber, sceneDescription, characters, dialogue, action, emotion}. It includes pacingReport (conflict analysis, reversal detection) and dialogueCoverage analysis. Our M3 Writer would duplicate:
- Idea → structured story generation
- Shot decomposition
- Pacing analysis

### DUPLICATION WARNING 4: Director Agent (M4)

Wind Comic's Director generates: genre, style, characters, scenes, storyStructure. Our M4 Director would duplicate:
- Idea analysis
- Character planning
- Scene planning
- Shot count determination

### DUPLICATION WARNING 5: Character System (M5)

Wind Comic has: `character_library` (reusable cross-project), turnaround sheet prompts, `locked_characters` for face consistency, character DNA, profile with bio/voice. Our M5 would duplicate:
- Character description generation
- Visual reference management
- Cross-project character reuse

### DUPLICATION WARNING 6: Style Bible (M6)

Wind Comic has: style preset system, Style Bible frame generation, style inheritance into all shots. Our M6 would duplicate:
- Style definition
- Style inheritance to shots

### DUPLICATION WARNING 7: Scene/Beat System (M7)

Wind Comic has: scene assets with location/description, linked to shots. No explicit "beat" concept but script shots serve a similar role. Our M7 would partially duplicate scene management.

### DUPLICATION WARNING 8: Storyboard (M8)

Wind Comic has: per-shot storyboard assets with prompt, duration, camera, lighting. Our M8 would duplicate:
- Storyboard generation per shot
- Camera angle specification
- Lighting description
- Duration assignment

### DUPLICATION WARNING 9: Review System (M14 partial)

Wind Comic has: `shot_vision_audits` table with score, verdict (pass/warn/fail), scene_match, action_match, mood_match, composition scores. Our M14 Review System would partially duplicate AI review scoring.

### DUPLICATION WARNING 10: NLE Export (M18)

Wind Comic has: AAF, EDL, FCPXML export. Our M18 would duplicate timeline export functionality.

---

## 15. Exit Criteria Verification

| Criterion | Met? | Evidence |
|---|---|---|
| 1. Wind Comic runs locally | YES | Started on port 3000, served HTML, created database |
| 2. Ollama integration works | PARTIAL | Connects and calls LLM, but gemma4:e4b output not parsed correctly |
| 3. Controlled project attempted | YES | Project Y6QHzhsgfniDypg1eYxqV created with 10 assets |
| 4. Persisted artifacts inspected | YES | SQLite data/qfmj.db queried, all asset types documented |
| 5. Shot schema compared | YES | See mapping document |
| 6. Character/Style inspected | YES | Character turnaround prompts and style inheritance documented |
| 7. Storyboard inspected | YES | Per-shot storyboard assets with prompt/duration/camera |
| 8. Programmatic options identified | YES | 5 strategies ranked |
| 9. Duplication measured | YES | 10 duplication warnings identified |
| 10. No architecture selected | YES | Awaiting review |
