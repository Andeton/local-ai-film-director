# Product Specification — Local AI Film Director

**Purpose:** Durable operator-facing product specification documenting the complete production journey, current implementation state, and demonstrated gaps.

**Last updated:** 2026-08-20

---

## Complete Operator Journey

### Stage 1: Project Creation (Idea)

**TARGET PRODUCT BEHAVIOR:**
The operator enters a creative idea/premise and gets a working production project. The original idea remains visible, inspectable, and referenceable throughout the entire production — it is the single source of creative truth.

**CURRENT IMPLEMENTATION:**
- Operator enters idea text + optional style in a modal dialog (`showNewProject()`)
- `POST /projects/from-idea` sends idea to Wind Comic, which creates story/script/storyboard/characters
- Returns: project with `director_context.description` (original idea stored here)
- WC project is imported into canonical LFDirector models
- **Original Idea section** (P4.1 + Fix A): displayed in the sidebar between the project selector and shot list, sourced from `director_context.original_idea`. This is the exact text entered by the operator in LFDirector's Create Project flow, preserved before Wind Comic processing. The WC-processed `director_context.description` (which may contain WC-appended production instructions) is kept as imported context but NOT displayed as "Original Idea". Collapsible (80px default, 300px expanded). Read-only.
- **Legacy projects** created before Fix A (including proj_cfb89b04f3c8) do not have `original_idea`. The UI shows "Imported Description" with "legacy project" label instead of "Original Idea" — honest degradation without mislabeling.

**GAP:**
- ~~G1: Original idea becomes invisible.~~ **RESOLVED (P4.1 + Fix A).** True operator idea is preserved in `director_context.original_idea` at project creation time, before WC processing. Displayed as "Original Idea" in the sidebar. WC-processed description stored separately. Legacy projects degrade honestly.
- **G2: WC import results are opaque.** The operator sees "Created!" but cannot inspect what WC actually produced (scenes, script, storyboard descriptions) vs what was generic placeholder content.

---

### Stage 2: Enrichment

**TARGET PRODUCT BEHAVIOR:**
The operator triggers enrichment to fill in production data: character definitions, environment description, and shot plan. Each enrichment result should be clearly attributed (LLM-derived vs WC-imported vs operator-written) and inspectable before use.

**CURRENT IMPLEMENTATION:**
- `POST /projects/{id}/enrich` runs three sub-processes atomically:
  1. Character enrichment: deficient characters get LLM-generated `appearance` + `display_name`
  2. Environment derivation: LLM extracts physical set description from idea → stored in `director_context.environment_description`
  3. Shot planning: LLM generates 5-7 shot plan with actions, camera, subjects, duration
- `POST /projects/{id}/replan` replaces shot plan (refuses if Takes exist)

**GAP:**
- **G3: Enrichment is a black box.** The operator clicks "Enrich" and gets results with no visibility into what the LLM was asked, what context it received, or why it produced these specific shots/characters/environment. There is no preview-before-commit.
- **G4: Enrichment source attribution missing.** Characters, environment description, and shots all appear the same in the UI regardless of whether they came from WC import, LLM enrichment, or operator editing. The operator cannot distinguish "WC gave us this" from "the LLM generated this" from "I wrote this."

---

### Stage 3: Character Definitions

**TARGET PRODUCT BEHAVIOR:**
The operator can inspect, understand, and edit each character's production definition. The relationship between the character's origin (WC), enrichment (LLM), and current state (operator-edited) should be clear.

**CURRENT IMPLEMENTATION:**
- Reference Manager shows each character with:
  - Editable `Display Name` field
  - Editable `Appearance` textarea
  - Save button → `PUT /characters/{id}`
  - Status badge (Ready / Needs ref)
- Editing appearance triggers staleness on GENERATED references

**GAP:**
- **G5: Character origin/history invisible.** The operator sees current name + appearance but not what WC originally imported, what the LLM enriched, or what they changed. No diff or provenance trail.
- The character `description` field (WC-originated) is stored but never shown in the UI — only `appearance` is visible.

---

### Stage 4: Environment Description

**TARGET PRODUCT BEHAVIOR:**
The operator can inspect, understand, and edit the physical set/location description. The relationship between the original idea and the derived environment should be clear.

**CURRENT IMPLEMENTATION:**
- Reference Manager shows environment description textarea
- Editable via Save → `PUT /projects/{id}/environment-description`
- Editing triggers staleness on GENERATED environment references

**GAP:**
- **G6: No connection shown between idea and derived environment.** The environment description appears as a standalone text field. The operator cannot see the original idea alongside it to verify that the derivation makes sense. (See G1 — the idea itself is invisible.)

---

### Stage 5: Reference Prompts & Generation

**TARGET PRODUCT BEHAVIOR:**
Before generating expensive reference images, the operator should see exactly what prompt will be sent to ComfyUI. The operator should be able to edit the prompt, adjust negative prompts, and understand what visual output to expect.

**CURRENT IMPLEMENTATION:**

Character references:
- `POST /characters/{id}/references/generate` accepts optional `prompt_override` and `negative_prompt_override`
- Default prompt built from: `"A character reference photo of {name}. {appearance}. {pose}, neutral studio lighting..."` (`reference_generator.py:194-201`)
- Default negative: `"text, labels, watermark, blurry, low quality, deformed, split panels..."` (`reference_generator.py:43`)
- The UI has Generate Body / Generate Face buttons but NO prompt preview or editing before generation

Environment references:
- `POST /projects/{id}/environment-references/generate` accepts NO prompt_override in UI
- Default prompt built from: `"A single continuous cinematic production design reference photograph of: {description}..."` (`reference_generator.py:54-65`)
- Default negative includes story-specific exclusions: `"police car, blood, envelope, weapon"` (`reference_generator.py:44-51`)
- The UI has Generate Environment button but NO prompt preview or editing before generation

**GAP:**
- ~~G7: Reference generation prompts are invisible.~~ **RESOLVED (P4.2).** Clicking Generate Body/Face/Environment now opens a prompt preview panel showing the exact default prompt and negative prompt. The operator can edit both before confirming generation. The displayed defaults come from the same `build_character_prompt()` / `build_environment_prompt()` functions used by actual generation. API endpoints `GET /characters/{id}/reference-prompt-preview` and `GET /projects/{id}/environment-reference-prompt-preview` provide the preview data. Both `prompt_override` and `negative_prompt_override` are now supported for character AND environment generation.
- ~~G8: Negative prompts are hardcoded with story-specific terms.~~ **RESOLVED (P4.2a).** The P3 story-specific terms (`"police car, blood, envelope, weapon"`) have been removed from the generic `DEFAULT_ENVIRONMENT_NEGATIVE`. The default now contains only generic empty-set exclusions (people, text, artifacts, composition problems). The operator can see and edit the negative prompt before every generation (P4.2). Regression tests prevent recontamination. Future enhancement: project-derived automatic negative prompts (not a contamination bug, separate feature).

---

### Stage 6: Reference Review

**TARGET PRODUCT BEHAVIOR:**
The operator reviews generated/uploaded reference images and approves or rejects them. The operator should understand what prompt produced each reference and be able to regenerate with adjustments.

**CURRENT IMPLEMENTATION:**
- Reference cards show: thumbnail, status badge (candidate/approved/rejected/archived), source state (current/stale), dimensions, source provenance ID
- Actions: Approve, Reject, Archive, Pin/Unpin
- Re-generation: click Generate again (creates new candidate)

**GAP:**
- ~~G9: Generated reference prompt not shown on reference card.~~ **RESOLVED (P4.3).** Each generated reference card now has a "Show prompt used" toggle that fetches and displays the stored prompt, negative prompt, seed, and kind from the `ReferenceGenerationRequest` via `GET /reference-generation-requests/{id}`. User-uploaded refs show "User upload" instead. Historical refs with pre-P4 pseudo provenance IDs degrade gracefully (show "Prompt details unavailable").

---

### Stage 7: Shot Plan

**TARGET PRODUCT BEHAVIOR:**
The operator can inspect, understand, and edit every aspect of each shot: action description, dramatic purpose, camera (size/angle/movement), subjects, duration, lighting intent, and audio intent. The shot plan should be understandable as a coherent sequence.

**CURRENT IMPLEMENTATION:**
- Shot list in sidebar shows shot number + truncated action + status badge
- Shot Plan Editor: textarea per shot for action text, move up/down/delete buttons, Add Shot button
- Main panel shows: Shot Description, Camera metadata, Duration, Workflow
- Right panel shows: Duration input, Seed control
- Shot editing via `PUT /shots/{id}` supports: action, dramatic_purpose, subjects, camera, environment, lighting, audio_intent, duration_sec

**GAP:**
- ~~G10: Shot editing is split across disconnected views.~~ **RESOLVED (P4.4).** The main panel now shows a unified "Shot Production Editor" with editable fields for: action, dramatic_purpose, camera (size/angle/movement), duration, lighting, ambient sound, music, and subjects. An explicit "Save Shot" button persists changes and regenerates the H3 prompt preview.
- ~~G11: Subjects are not editable in the UI.~~ **RESOLVED (P4.4).** The editor shows current subjects as badges. An "Add character" picker populated from the project's character list lets the operator add characters. "Remove Last" removes the most recent subject. Duplicate characters are prevented. Subject changes affect reference binding (visible in the generation preview pictures).
- ~~G12: Audio/lighting/environment not editable in the UI.~~ **PARTIALLY RESOLVED (P4.4).** Ambient sound and music fields are now editable and map directly to `[Overall Soundscape]` and `[Non-Diegetic Music]` H3 prompt sections. Lighting is editable as key:value pairs. Environment intent (dict) is not yet exposed — low priority since environment continuity is driven by the environment reference image, not shot-level text. **Remaining gap: intentional spoken dialogue is not represented in the current `audio_intent` model (H3 generates dialogue spontaneously without operator control). This is G14.**

---

### Stage 8: Generation Prompt (H3 Video Prompt)

**TARGET PRODUCT BEHAVIOR:**
Before committing to an expensive 7-15+ minute ComfyUI render, the operator should see the exact prompt that will be sent to H3, understand every section, and be able to edit or override it.

**CURRENT IMPLEMENTATION:**
- Generation Preview (`GET /shots/{id}/generation-preview`) returns the prompt text
- The main panel shows the prompt in a textarea with "Generated" or "Override" badge
- Operator can edit the prompt inline → `onPromptEdit()` sets `draftPrompt`
- Edited prompt is sent as `prompt_override` to `POST /shots/{id}/generate`
- Reset button returns to generated prompt

H3 prompt sections (assembled by `H3PromptBuilder.build()`):
1. `[Subject Definitions]` — `<Subject N> is {name} — {appearance} in <Picture N>`
2. `[Summary]` — `{names}: {action}. {dramatic_purpose}.`
3. `[Retention Analysis]` — `<Subject N> fully_preserved — {appearance}`
4. `[Detailed Description]` — `Camera: {shot_size}. angle {angle}. movement {movement}. Action: {action}. Duration: {duration}s.`
5. `[Overall Soundscape]` — from `audio_intent.ambient` (if set)
6. `[Non-Diegetic Music]` — from `audio_intent.music` (if set)

**GAP:**
- ~~G13: Prompt is a raw text wall.~~ **RESOLVED (P4.4).** The operator now edits meaningful production inputs (action, camera, subjects, audio) in the Shot Production Editor. The H3 prompt is clearly labeled "Generated from inputs" and compiled from the actual H3PromptBuilder path via the generation-preview endpoint. The operator can still override the raw prompt text for fine-tuning, with a "Reset to generated" action. The relationship INPUTS → COMPILED PROMPT → OPTIONAL OVERRIDE → GENERATION is now visible.
- **G14: No intentional dialogue direction.** H3 generates audio (including spontaneous dialogue) but the operator has no explicit control over spoken dialogue content. The `audio_intent` model supports `ambient` and `music` fields (now editable in the UI), but there is no `dialogue` field or mechanism to direct what characters say. H3's dialogue generation is not controllable through the prompt. This remains an observation-level gap — H3's dialogue controllability is not established.
- **G15: Prompt sections are not explained.** The operator sees `[Subject Definitions]`, `[Retention Analysis]` etc. without explanation of what each section does or how H3 interprets them. Mitigated by P4.4 — the operator now edits semantic fields rather than raw prompt sections, making section understanding less critical for basic operation.

---

### Stage 9: Generation Preview

**TARGET PRODUCT BEHAVIOR:**
Before generation, the operator should see all inputs that will be sent to ComfyUI: reference images, continuity frame, workflow, resolution, duration, frame count, seed, and prompt. Each input should be inspectable and its purpose clear.

**CURRENT IMPLEMENTATION:**
- Right panel shows Picture cards with: thumbnail, role label, reference ID, status badge
- Duration input with resolved frames/duration display
- Seed mode selector (random/explicit)
- Workflow + resolution display
- Generation summary: workflow, duration, inputs count, seed
- "Blocked" state shown when predecessor shot has no approved Take
- Generate button enabled/disabled based on `can_generate` and active job state

**GAP:**
- ~~G16: Reference images are small thumbnails with no inspection.~~ **RESOLVED (P4 UX cleanup).** Reference card thumbnails increased to 72x72. Clicking any reference thumbnail opens a large modal preview (90vw/90vh). Aspect ratio preserved.
- The generation preview is otherwise well-implemented and shows the correct information.

---

### Stage 10: Generation (Durable Async)

**TARGET PRODUCT BEHAVIOR:**
Generation should be durable: the operator clicks Generate, gets immediate feedback, and can close/refresh the browser without losing the generation. Status should be visible throughout. Completed renders must always be persisted.

**CURRENT IMPLEMENTATION:**
- `POST /shots/{id}/generate` returns 202 with `{job_id, status: "pending", seed}`
- Embedded QueueWorker claims and executes in background thread
- UI polls `GET /queue/jobs/{job_id}` every 4 seconds
- "Generating" badge shown on shot in sidebar
- Generate button disabled during active generation
- Duplicate protection: 409 if active jobs exist for shot
- Page refresh rediscovers active jobs via `GET /queue/jobs?shot_id=X`
- Timeout leaves job claimed for recovery (not permanently failed)
- Worker recovery checks ComfyUI history for failed-with-prompt_id

**GAP:**
- **NONE for core generation lifecycle.** Durable async generation is fully implemented (P3).
- Minor: no estimated completion time or progress indicator (ComfyUI does not provide percent-complete for H3).

---

### Stage 11: Take Review

**TARGET PRODUCT BEHAVIOR:**
The operator reviews generated Takes: watches the video, compares multiple takes, approves or rejects each. Approved takes become the canonical output for the shot and feed continuity to downstream shots.

**CURRENT IMPLEMENTATION:**
- Main panel shows video player with the latest/approved take
- Takes list shows each take with: status badge, seed, take ID, Approve/Reject buttons
- Approve → `POST /takes/{id}/approve` (triggers continuity chain)
- Reject → `POST /takes/{id}/reject`
- Only one approved take per shot (enforced by partial unique index)

**GAP:**
- **G17: No side-by-side take comparison.** When multiple takes exist, the operator can only view one at a time via the video player. There is no comparison/A-B view.
- ~~G18: No take-level metadata display.~~ **RESOLVED (P4.9/P4.12).** Each Take has a "Details" button that shows the immutable historical prompt, seed, workflow, references, continuity source, and timestamps from its GenerationRequest. Multiple Takes each resolve to their own historical data.

---

### Stage 12: Prompt Revision & Regeneration

**TARGET PRODUCT BEHAVIOR:**
After reviewing a take, the operator should be able to revise the prompt, adjust camera/duration/seed, and regenerate while preserving the history of previous takes and their settings.

**CURRENT IMPLEMENTATION:**
- Operator edits prompt in textarea, changes duration/seed in right panel
- Clicks Generate again → new queue job, new take with new take_number
- Previous takes remain in the Takes list (never deleted)
- `draftPrompt`/`draftDuration`/`draftSeedValue` are UI-local state (reset on shot switch)

**GAP:**
- ~~G19: No prompt history.~~ **RESOLVED (P4.9).** Each Take now has an expandable "Details" section showing the historical prompt, seed, workflow, references, continuity source, and timestamps from the immutable GenerationRequest that produced it. Multiple Takes from the same shot each show their own historical data. Editing the current shot does NOT change displayed Take provenance.
- **G20: Override state is ephemeral.** If the operator sets a prompt override and switches shots, the override is lost. There is no way to persist a custom prompt for a shot without generating.

---

### Stage 13: Continuity

**TARGET PRODUCT BEHAVIOR:**
The operator should understand the continuity chain: which approved take feeds into which downstream shot as Picture 3. Replacing an approved take should clearly show which downstream shots are affected.

**CURRENT IMPLEMENTATION:**
- Continuity is automatic: approved take's last frame becomes Picture 3 for next shot
- "Blocked" state shown when predecessor has no approved take
- ContinuityState tracks upstream provenance (take_id, shot_id, last_frame_sha256)
- Replace-approved triggers downstream invalidation

**GAP:**
- **G21: Continuity chain is not visualized.** The operator can see "Blocked: Approve Shot N" but cannot see the full continuity graph (Shot 1 → Shot 2 → Shot 3...). There is no visual representation of which takes feed which shots.

---

### Stage 14: Scene Assembly

**TARGET PRODUCT BEHAVIOR:**
When all shots are approved, the operator builds the final scene. The assembled video should be playable, inspectable, and exportable.

**CURRENT IMPLEMENTATION:**
- Scene Assembly section in sidebar shows approval count
- "Build Scene" button enabled when all shots approved
- `POST /projects/{id}/build-scene` concatenates all approved take videos via ffmpeg
- Result shown in video player with duration and resolution

**GAP:**
- **G22: No scene re-assembly after shot replacement.** If the operator replaces an approved take after assembly, there is no indication that the scene is stale or needs rebuilding.
- Scene assembly is otherwise functional.

---

### Stage 15: Final Review & Export

**TARGET PRODUCT BEHAVIOR:**
The operator can review the complete assembled scene, export it, and access individual shot videos.

**CURRENT IMPLEMENTATION:**
- Assembled scene plays in main panel video player
- Scene export metadata available via `GET /projects/{id}/scene-export`
- Individual take videos accessible via media URLs

**GAP:**
- **G23: No export/download button.** The assembled scene plays in the browser but there is no explicit download/export action. The operator must know the media URL convention.

---

## Future Accepted Capabilities (Not P4 Priority)

### Environment View Packs / Multi-View Environment
A single project-level ENVIRONMENT reference was observed to be insufficient for maintaining spatial/environment continuity across shots using substantially different camera angles. A multi-view environment pack (e.g., front/side/overhead views of the set) or 360-degree environment source would address this. Record as accepted future capability but do NOT prioritize in P4.

### H3 Prompt Compilation
An intermediate LLM step that "compiles" operator-facing shot direction into optimal H3 prompt format. Should be integrated into the operator-visible prompt control (Stage 8) rather than hidden as backend behavior. The prompt editing UI (G13) should be addressed first so compilation has a proper surface.

### AI Reviewer (M8)
Automated quality assessment of generated Takes before human review. Deferred until operator workflow is stable.

---

## P4 Prioritized Gap List

Priority based on real operator journey order and impact on production quality:

| Priority | Gap | Stage | Type | Impact |
|----------|-----|-------|------|--------|
| ~~P4.1~~ | ~~G1: Original idea invisible after creation~~ | 1 | ~~UI~~ | **RESOLVED** — displayed in sidebar "Original Idea" section |
| ~~P4.2~~ | ~~G7: Reference generation prompts invisible~~ | 5 | ~~UI+Backend~~ | **RESOLVED** — prompt preview + editable before generation |
| ~~P4.3~~ | ~~G9: Generated reference prompt not shown on card~~ | 6 | ~~UI+Backend~~ | **RESOLVED** — "Show prompt used" on generated ref cards |
| ~~P4.4~~ | ~~G13: H3 prompt is raw text wall~~ | 8 | ~~UI~~ | **RESOLVED** — semantic editor + compiled prompt + override |
| **P4.5** | G14: No intentional dialogue direction | 8 | UI+Backend | H3 generates dialogue but operator has no control |
| ~~P4.6~~ | ~~G10: Shot editing split across views~~ | 7 | ~~UI~~ | **RESOLVED** — unified Shot Production Editor |
| ~~P4.7~~ | ~~G11: Subjects not editable in UI~~ | 7 | ~~UI~~ | **RESOLVED** — add/remove via character picker |
| ~~P4.8~~ | ~~G12: Audio/lighting/environment not editable~~ | 7 | ~~UI~~ | **PARTIALLY RESOLVED** — ambient/music/lighting editable; dialogue gap remains (G14) |
| ~~P4.9~~ | ~~G19: No prompt history across takes~~ | 12 | ~~UI+Backend~~ | **RESOLVED** — historical prompt/seed/refs per Take via generation-details |
| ~~P4.10~~ | ~~G16: Reference thumbnails not inspectable~~ | 9 | ~~UI~~ | **RESOLVED** — 72x72 thumbnails + click-to-enlarge modal |
| **P4.11** | G6: No idea↔environment connection shown | 4 | UI | Derived environment appears disconnected from idea |
| ~~P4.12~~ | ~~G18: No take-level metadata display~~ | 11 | ~~UI~~ | **RESOLVED** — expandable generation details per Take |
| ~~P4.13~~ | ~~G8: Hardcoded story-specific negative prompts~~ | 5 | ~~Backend~~ | **RESOLVED (P4.2a)** — P3-specific terms removed, generic defaults only |
| **P4.14** | G17: No side-by-side take comparison | 11 | UI | Can only view one take at a time |
| **P4.15** | G20: Override state is ephemeral | 12 | UI | Prompt overrides lost on shot switch |
| **P4.16** | G4: Enrichment source attribution missing | 2 | UI+Backend | Cannot distinguish WC/LLM/operator sources |
| **P4.17** | G21: Continuity chain not visualized | 13 | UI | No visual graph of shot continuity |
| **P4.18** | G5: Character origin/history invisible | 3 | UI | No provenance trail for character definitions |
| **P4.19** | G3: Enrichment is a black box | 2 | UI+Backend | No visibility into enrichment process |
| **P4.20** | G22: No stale scene indicator | 14 | UI | No warning when assembly is outdated |
| **P4.21** | G23: No export/download button | 15 | UI | Must know media URL convention |
| **P4.22** | G2: WC import results opaque | 1 | UI | Cannot inspect what WC produced |
| **P4.23** | G15: Prompt sections not explained | 8 | UI | Section labels without documentation |
