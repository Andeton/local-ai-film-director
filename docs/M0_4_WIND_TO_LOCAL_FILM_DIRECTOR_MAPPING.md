# M0.4 — Wind Comic → Local AI Film Director Field Mapping

**Date:** 2026-08-14

---

## Shot Specification Mapping

| Our Field | Wind Comic Field | Source | Mapping |
|---|---|---|---|
| `shot_id` | `project_assets.id` (nanoid) | project_assets | DIRECT — rename only |
| `beat_id` | (none) | — | MISSING — Wind Comic has no beat concept |
| `dramatic_purpose` | `script.shots[].emotion` | script_data | TRANSFORM — "emotion" is closest but less specific |
| `subjects` | `script.shots[].characters` | script_data | TRANSFORM — array of names, need to resolve to character assets |
| `action` | `script.shots[].action` | script_data | DIRECT — text description |
| `environment` | `scene.data.location` + `scene.data.description` | project_assets type=scene | TRANSFORM — need to link shot to scene via plan |
| `camera` | `storyboard.data.description` (embedded) | project_assets type=storyboard | TRANSFORM — camera info is embedded in the prompt string, not structured |
| `lighting` | `storyboard.data.description` (embedded) | project_assets type=storyboard | TRANSFORM — same, embedded in prompt |
| `audio` | `script.shots[].dialogue` | script_data | PARTIAL — only dialogue, no ambient/SFX/music |
| `references` | `character.data.media_urls` + `scene.data.media_urls` | project_assets | DIRECT — image URLs (when generated) |
| `duration` | `storyboard.data.duration` | project_assets type=storyboard | DIRECT — in seconds |
| `generation_strategy` | (none) | — | MISSING — Wind Comic doesn't distinguish T2V/I2V/R2V |
| `seed` | (none per shot) | — | MISSING — managed by video provider, not pre-production |
| `resolution` | `projects.aspect` ("16:9" or "9:16") | projects | TRANSFORM — aspect only, not pixel resolution |

---

## What Wind Comic Provides That Our Spec Does Not

| Wind Comic Feature | Our Spec Equivalent | Value |
|---|---|---|
| `pacingReport` — conflict scores, reversal analysis per shot | None | HIGH — useful for quality validation |
| `dialogueCoverage` — multi-character scene analysis | None | MEDIUM — useful for coverage planning |
| `shot_vision_audits` — AI vision scoring (scene/action/mood/composition match) | Phase 18 Review (similar) | HIGH — already implemented |
| `character_library` — cross-project reusable characters | Not in MVP spec | HIGH — enables multi-episode production |
| `global_assets` — cross-project style/prop/scene library | Not in spec | MEDIUM |
| `locked_characters` — face consistency lock per project | Character Bible (Phase 5) | HIGH — practical consistency mechanism |
| `series_anchors` — cross-episode continuity | Not in MVP spec | HIGH for series production |
| `cost_log` / `budget_enforce` — per-call cost tracking | Not in spec | MEDIUM — useful for cloud LLM/provider tracking |
| `pipeline_jobs` + `pipeline_job_events` — async pipeline with progress | Phase 14 Queue (similar) | HIGH — production-ready async orchestration |

---

## What Our Spec Needs That Wind Comic Does Not Provide

| Our Spec Need | Wind Comic Status | Gap |
|---|---|---|
| Beat-level decomposition (Scene → Beat → Shot) | NO — jumps from Scene to Shot directly | Need to add intermediate beat layer |
| Coverage planning (master, medium, close-up, POV, reaction) | NO — shots are numbered sequentially, no shot type classification | Need to build coverage planner |
| H3 prompt format (subject_definitions, retention_analysis, detailed_description, soundscape) | NO — uses MJ-style prompts | Need prompt transformation adapter |
| Generation strategy selection (T2V/I2V/R2V/FL) | NO — delegates to video provider | Need strategy selector |
| Take management (multiple generates per shot) | NO — one video per shot | Need take system |
| Continuity chain (last frame → next shot reference) | NO — no frame extraction between shots | Need continuity manager |
| H3-specific workflow orchestration | NO — uses cloud video APIs | Need ComfyUI adapter |
| Per-shot seed control | NO — managed by provider | Need seed management |
| Production dependency graph (change propagation) | PARTIAL — has `stale` flag but no full graph | Need full dependency tracking |
| Human review with approve/reject/regenerate per shot | PARTIAL — has `confirmed` flag but limited UI | Need review system |

---

## Production Specification Layer Overlap

```
WIND COMIC COVERAGE              OUR SPEC COVERAGE         OVERLAP
─────────────────────────────────────────────────────────────────
Project creation                 M1 Project Core           FULL
LLM provider abstraction         M2 LLM Layer              FULL
Story/script generation          M3 Writer                 FULL
Director treatment               M4 Director               FULL
Character descriptions           M5 Character Bible        HIGH
Style definition                 M6 Style Bible            HIGH
Scene breakdown                  M7 Scene/Beat             PARTIAL (no beats)
(no coverage planning)           M7 Coverage               NONE
Storyboard per shot              M8 Storyboard             HIGH
(no shot specification)          M9 Shot Spec              LOW
(no reference manager)           M9 Reference Manager      NONE
(no H3 prompt builder)           M10 H3 Prompt             NONE
(cloud video only)               M11 ComfyUI               NONE
(no takes)                       M12 Takes                 NONE
(no continuity chain)            M13 Continuity            NONE
Vision audit scores              M14 Review                PARTIAL
(no targeted regen)              M15 Regeneration          NONE
Timeline (Yjs-based)             M16 Timeline              PARTIAL
(cloud TTS/music)                M17 Audio                 NONE
AAF/EDL/FCPXML export            M18 Export                FULL
```

---

## H3 Prompt Transformation Requirement

Wind Comic storyboard prompts look like this:
```
cinematic film frame, [scene description], camera angle: [angle],
lighting: [lighting], color tone: [tone]
```

Our H3 R2V prompts need this structure:
```
subject_definitions:
<Subject 1> is [character from <Picture 1>]

summary:
[reference generation] <Subject 1> does X in <Subject 2>

retention_analysis:
<Subject 1> (appears in [Shot N]): fully_preserved - [details]

detailed_description:
[Shot 1] [camera, action, details]

overall_soundscape:
[ambient audio]

non_diegetic_music:
[score description]
```

**Transformation requires:**
1. Resolving character names to `<Subject N>` + `<Picture N>` tags
2. Generating retention_analysis from character descriptions
3. Restructuring camera/lighting from inline text to structured fields
4. Adding soundscape and music (not present in Wind Comic)
5. Adding temporal markers ([Shot N] timestamps)
