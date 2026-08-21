# Product Specification — Local AI Film Director

**Purpose:** Durable operator-facing product specification. Defines the target production journey, current implementation state, and gaps between them. Not a handoff file — a separate product reference.

**Last updated:** 2026-08-21 (Location design finalized)

---

## Product Identity

LFDirector is an **AI Director + Production Manager + ComfyUI Orchestrator**.

It is NOT a prompt-to-video generator. It owns the complete production pipeline from idea to assembled film, using ready-made solutions (Wind Comic, ComfyUI, video models, LLMs) while maintaining its own canonical production specification.

The operator should be able to start with a simple idea and follow a managed process without needing to understand the underlying AI video production mechanics. The system proposes production structure but the operator can intervene at any stage.

---

## Target Operator Journey

### Stage 1: Idea

**TARGET:** The operator enters a creative idea/premise. The original idea remains visible, inspectable, and referenceable throughout the entire production — it is the single source of creative truth.

**CURRENT:** Idea is captured as `director_context.original_idea` before Wind Comic processing. Displayed in sidebar "Original Idea" section. Legacy projects degrade honestly with "Imported Description" label.

**GAP:**
- ~~G1: Original idea invisible.~~ **RESOLVED (P4.1).**
- **G2: WC import results opaque.** Operator cannot inspect what WC actually produced vs what was generic placeholder content.

---

### Stage 2: Canonical Pre-Production (Story, Treatment, Style)

**TARGET:** From the idea, the system produces three canonical pre-production artifacts:

- **Story** — narrative structure: logline, genre, tone, characters, beginning/middle/ending
- **Director Treatment** — visual language, cinematography, pacing, camera language, lighting strategy, shot density
- **Style Bible** — visual style, color palette, lighting conventions, texture, production design references

Each artifact:
- has a SOURCE (Wind Comic, LLM, operator input)
- is owned canonically by LFDirector (ADR-002)
- is editable by the operator
- carries provenance attribution
- is consumed by downstream planning and generation

The operator should be able to review, accept, edit, or regenerate each artifact independently.

**CURRENT:** These concepts exist only as fragments in `director_context`:
- `director_context.description` — WC-processed project description (consumed by planning)
- `director_context.genre`, `.style`, `.story_structure` — imported from WC `WCDirectorPlan` but **never consumed downstream**
- No `Story`, `DirectorTreatment`, or `StyleBible` canonical entities
- No editing, versioning, or provenance for these fragments
- `style_id` from WC is captured in provenance hash but not stored accessibly

**GAP:**
- **G24: No canonical Story entity.** Story is a dict field inside `director_context`, not an independently versioned artifact.
- **G25: Director Treatment imported but dead.** Genre/style/structure are imported from WC and then completely ignored by all enrichment and generation code.
- **G26: No Style Bible.** No representation at any level.
- **G3: Enrichment is a black box.** No visibility into what the LLM was asked or why it produced specific results.
- **G4: Enrichment source attribution missing.** Cannot distinguish WC/LLM/operator origins.

---

### Stage 3: Characters

**TARGET:** The operator can inspect, create, edit, and manage each character's production identity. Characters are canonical LFDirector entities with clear provenance (WC-imported, LLM-enriched, operator-authored). Each character has:
- display name
- description (narrative role)
- appearance (visual production description)
- visual identity references (managed `ReferenceAsset` entities)

Editing a character's appearance invalidates generated visual references. Character definitions are consumed by shot planning, prompt building, and reference generation.

**CURRENT:** Characters are stored as `CharacterReference` entities (naming debt — see Implementation Notes). Display name and appearance are editable. LLM enrichment fills empty appearances. Staleness propagation works for GENERATED refs.

**IMPLEMENTATION NOTE — naming debt:** The current class `CharacterReference` and table `character_references` store what is conceptually the Character entity. Actual visual reference images are stored in `ReferenceAsset`. The `face_ref_path`, `turnaround_paths`, and `visual_anchors` fields on `CharacterReference` are dead code from early design — all visual assets are managed through `ReferenceAsset`. Target documentation and UI should use "Character" terminology. Code/schema rename is deferred.

**GAP:**
- **G5: Character origin/history invisible.** No diff or provenance trail visible to operator.
- `CharacterReference.description` (WC-originated narrative role) is stored but never shown — only `appearance` is visible.

---

### Stage 4: Locations

**TARGET:** Locations are first-class canonical production concepts (PD-1):

- A **Location** represents a persistent, reusable physical place/set. It has an identity (name), a physical/set description (architecture, furnishing, spatial layout — may include location-specific production design), and owns its reference assets. Example: "Marcus's Kitchen — 1970s linoleum, overhead fluorescent panels, formica table, window facing a brick airshaft."
- Multiple Scenes may reference the same Location (e.g., Kitchen used in Scene 1 morning and Scene 4 night).
- A project with distinct physical places (apartment + subway + street + office) should have four Location entities.
- **Scene Environment State** (future) — how the Location exists during a particular scene: time of day, weather, damage/state, practical conditions. This is a scene-level concept, not a Location property or a Shot property. Not implemented initially but the domain design reserves a clean place for it on Scene.
- **Shot Environment** — shot-specific framing/state details that differ from the scene baseline. Already exists as `Shot.environment` dict. Shots inherit their Scene's Location; shot-level alternate-location/cutaway semantics are architecturally possible but not implemented initially.

For new projects, enrichment identifies distinct production Locations from the source material, creates/reuses canonical Location entities, assigns Scenes to Locations, and derives a physical description for each. A story about "apartment + subway + office" should produce three Locations, not one.

**CURRENT:** No `Location` entity exists. Environment is represented as:
- `director_context.environment_description` — a single project-level string (legacy/MVP)
- `ReferenceAsset` with `kind=ENVIRONMENT` — project-level, one per project
- `Scene.location` — imported from WC but never displayed, edited, or consumed downstream
- `Shot.environment` — JSON dict, partially consumed by FLF continuity prompts but not editable in UI

**GAP:**
- **G27: No Location entity.** A multi-scene project with different locations cannot have different environment references. All shots share one project-wide ENVIRONMENT ref.
- **G6: No idea↔environment connection shown.** Partially mitigated by G1 fix, but the derivation relationship is not visible.

---

### Stage 5: Props

**TARGET:** Props are trackable production elements with optional reference assets. A prop may have a visual reference, location/condition state, and ownership (which character holds it). Props appear in shot specifications and can affect continuity.

**CURRENT:** No `Prop` entity. `AssetRole.PROP_REFERENCE` and `PROP_TURNAROUND` exist in the `AssetRole` enum. H3_IMAGE_PACK_RECIPE slot 3 is typed as `PROP_REFERENCE` (optional). `ReferenceKind` does NOT include a PROP value. No prop tracking, no prop references in any production path.

**GAP:**
- **G28: No Prop entity or lifecycle.**

---

### Stage 6: Scenes and Beats

**TARGET:** The production hierarchy is: Sequence → Scene → Beat → Shot.

A Scene represents a continuous dramatic unit at a Location with specific cast and props. A Scene has:
- Location reference
- Cast (which Characters appear)
- Dramatic purpose
- Beats (dramatic sub-units)

A Beat is a dramatic moment within a scene (action, character intention, change). Beats group shots that cover the same dramatic moment. A beat may map 1:1 with a shot or may have multiple shots providing coverage (master, close-up, reaction, etc.).

**CURRENT:**
- Scene: exists as structural entity. `location` and `description` are imported from WC but never displayed or consumed. No cast assignment. No scene-level editing UI.
- Beat: exists and persists. The current `ShotPlanner` creates 1:1 beat→shot mappings. The legacy `BeatEnricher` → `CoveragePlanner` chain supports multi-shot-per-beat coverage. Beats have full API (`GET/PUT` beats, `POST enrich-beats`, `POST plan-coverage`) but are **invisible in the operator UI**.
- `shots.beat_id` is a NOT NULL foreign key — shots cannot exist without beats.

**GAP:**
- Scenes are invisible/non-editable. Location, cast, and description fields exist but are not exposed.
- Beats are invisible. Whether they should be visible as a grouping UI or remain a structural intermediate is an accepted product decision (lightweight grouping layer, preserve multi-shot semantics).

---

### Stage 7: Storyboard

**TARGET:** Before expensive video generation, the operator reviews a visual storyboard of the entire production. Each shot has a storyboard frame showing the intended composition. Sources:
- Wind Comic import (WC storyboard images)
- Generated image (via ComfyUI/image model from shot description)
- Operator upload

The operator can review, approve, reject, and regenerate storyboard frames. The storyboard provides a pre-generation quality gate.

**CURRENT:**
- `ShotSpecificationV1.storyboard_image_path` exists (nullable, never populated)
- `ShotSpecificationV1.wc_storyboard_id` and `wc_shot_number` exist for WC correlation
- `ReferenceKind.STORYBOARD` exists with ownership rules (shot_id required)
- WC storyboard descriptions are parsed during import for camera/lighting extraction
- WC storyboard **images** are available via URLs but **never downloaded or stored**

**GAP:**
- **G29: No storyboard review stage.** Storyboard images are not imported, generated, or displayed. The operator goes directly from shot plan to video generation with no visual preview of intended compositions.

---

### Stage 8: Shot Specification

**TARGET:** Each shot has a complete, model-agnostic production specification: action, dramatic purpose, subjects, camera (size/angle/movement), lighting, audio intent (ambient/music/dialogue), duration, and environment. The operator can edit all fields. Changes trigger version increments and prompt recompilation.

**CURRENT:** `ShotSpecificationV1` covers all listed fields except dialogue. The Shot Production Editor (P4.4) provides semantic editing for action, dramatic purpose, camera, duration, lighting, ambient, music, and subjects. Shot editing triggers version increment and prompt rebuild.

**GAP:**
- **G14: No intentional dialogue direction.** H3 generates spontaneous dialogue without operator control. The `audio_intent` model has `ambient` and `music` but no `dialogue` field.
- **G15: Prompt sections not explained.** Mitigated by semantic editor but H3 section labels remain unexplained.

---

### Stage 9: References

**TARGET:** Before generation, the system assembles reference assets for each shot. Reference types include:
- Character identity references (face, body)
- Location/environment references
- Style references
- Prop references
- Storyboard/shot composition references
- Continuity frames (from predecessor's approved Take)

`ReferenceKind` defines what a managed asset IS (ownership semantics). `AssetRole` defines how an asset is USED in a generation/conditioning recipe. These are separate concepts — a CHARACTER_BODY `ReferenceAsset` might be bound in the `CHARACTER_BODY_FRONT` role. Provider-specific picture-slot semantics are NOT canonical.

**CURRENT:**
- `ReferenceKind` has 4 values: CHARACTER_FACE, CHARACTER_BODY, STORYBOARD, ENVIRONMENT
- `AssetRole` has 18 values (broader semantic roles) but is only used in `VisualAssetPack` bindings, not in `ReferenceAsset.kind`
- Character refs: full lifecycle (generate/upload/approve/reject/archive/pin) with prompt preview
- Environment refs: full lifecycle, project-level ownership
- Storyboard refs: enum exists but never populated
- Style/Prop refs: no `ReferenceKind` values, no production paths
- Continuity frames: tracked in `ContinuityState`, not as `ReferenceAsset` entities

**GAP:**
- **G30: ReferenceKind too narrow.** Target scope must support Character, Location, Prop, Style, and Storyboard references. Current 4-value enum reflects MVP/H3 production needs.
- Style and Prop references have no creation, lifecycle, or selection paths.

---

### Stage 10: Generation Prompt

**TARGET:** The operator sees a compiled generation prompt derived from shot specification inputs. The prompt is provider-specific (currently H3) but the operator edits model-agnostic inputs, not raw provider syntax. The operator can override the compiled prompt for fine-tuning. Two distinct prompt states exist:

1. **Persistent shot-level working draft** — saved per shot, survives shot switching and page refresh
2. **Immutable per-generation snapshot** — frozen in `GenerationRequest` at generation time

**CURRENT:** Shot inputs are persistent (action, camera, subjects, etc. via `PUT /shots/{id}`). The H3 prompt is compiled via `H3PromptBuilder` and shown in the editor. Prompt overrides are **ephemeral JS state** (`draftPrompt`) — lost on shot switch.

**GAP:**
- **G20: Override state is ephemeral.** Prompt/duration/seed drafts are JS-only. No persistent shot-level working draft.

---

### Stage 11: Generation Preview and Execution

**TARGET:** Before generation, the operator sees all inputs (references, prompt, workflow, resolution, duration, seed). Generation is durable and async.

**CURRENT:** Fully implemented (P3/P4). Generation preview shows picture cards, duration, seed, workflow. Durable async generation via persistent queue with timeout recovery. Duplicate protection. Page-refresh discovery.

**GAP:** None for core lifecycle. Minor: no progress indicator.

---

### Stage 12: Takes and Review

**TARGET:** The operator reviews generated Takes, compares multiple takes, and approves or rejects each. Each Take preserves immutable provenance (prompt, seed, references, continuity source). Approved takes feed continuity to downstream shots.

Future: AI Review provides automated quality assessment (character consistency, composition, prompt adherence) before human review.

**CURRENT:** Human review with approve/reject. Take provenance via generation details (P4.9/P4.12). Single approved per shot.

**GAP:**
- **G17: No side-by-side Take comparison.**
- AI Review (originally M8) deferred.

---

### Stage 13: Continuity

**TARGET:** The operator understands the continuity chain and can see which approved Takes feed which downstream shots. Replacing an approved Take shows affected downstream shots. Current baseline: frame-level continuity (predecessor's last frame). Semantic continuity (character state, prop state, narrative state) is a future capability.

**CURRENT:** Frame-level continuity works. `ContinuityState` tracks upstream provenance. Replace-approved triggers downstream invalidation. "Blocked" indicator for unresolved predecessors. API endpoints for chain inspection exist.

**GAP:**
- **G21: Continuity chain not visualized.** API exists but UI shows only "Blocked" indicator.
- Semantic continuity (character/prop/narrative state per ORIGINAL_SPEC §27) is deferred.

---

### Stage 14: Timeline and Assembly

**TARGET:** A minimal production timeline — NOT a full NLE. The timeline represents:
- ordered approved clips per scene
- clip source, start, duration
- transitions (future)
- assembly version/freshness tracking

Scene assembly produces concatenated output. The timeline may eventually export to external NLE (EDL/OTIO for DaVinci Resolve per ORIGINAL_SPEC §34).

**CURRENT:** Scene assembly via FFmpeg stream-copy concatenation. Produces MP4 + JSON manifest. No persistent timeline model. No transition support. No stale tracking.

**GAP:**
- **G31: No timeline model.** Assembly is a one-shot operation with no persistent clip/track representation.
- **G22: No stale assembly indicator.** Replacing a Take after assembly produces no warning.
- **G23: No export/download button.** Operator must know media URL convention.

---

## Accepted Product Decisions

The following decisions were accepted during the product-model audit (2026-08-21) and govern the target architecture:

### PD-1: Location is a first-class canonical concept
Scenes reference reusable Locations. A Location owns its physical/set description (including location-specific production design) and reference assets. The current project-level `environment_description` is legacy/MVP behavior. Three conceptual layers: Location (persistent identity), Scene Environment State (future — time/weather/conditions per scene), Shot Environment (shot-specific details). For new projects, enrichment derives multiple Locations when the source material describes distinct physical places. Legacy migration: one Location per existing project.

**Location model:** `id, project_id, name, description, source, version, created_at, updated_at`. No lifecycle status field — staleness is expressed through dependent reference and shot state, not on Location itself.

**Staleness:** Editing Location.description increments version and marks GENERATED Location refs as STALE. Changing a Scene's Location assignment marks affected shots and their GenerationPlans as `outdated`. Existing Takes remain immutable historical artifacts.

**Readiness:** Evaluated per-shot via Scene → Location. A project with Scene 1 ready and Scene 8 unfinished can generate Scene 1's shots.

### PD-2: CharacterReference is conceptually Character
Visual references remain `ReferenceAsset`. Code/schema rename deferred. Documentation and UI use "Character" terminology.

### PD-3: Beat remains in canonical hierarchy
Scene → Beat → Shot is the target hierarchy. Beat may be a lightweight grouping layer in UI. Multi-shot coverage semantics preserved.

### PD-4: Prompt editing has two states
Persistent shot-level working draft + immutable per-generation snapshot. Current JS-only drafts are not target behavior.

### PD-5: Story, Treatment, Style are canonical LFDirector artifacts
Source may be WC, LLM, or operator. LFDirector owns the canonical version. Provenance attribution preserved.

### PD-6: Wind Comic is source, not canonical owner
ADR-001 adapter boundary remains. WC output-quality revisit condition has been demonstrated. WC not removed.

### PD-7: ReferenceKind and AssetRole remain separate
ReferenceKind = what the asset IS. AssetRole = how it is USED in generation. Target ReferenceKind scope: Character, Location, Prop, Style, Storyboard.

### PD-8: Storyboard is a core pre-generation stage
Each shot can have a storyboard frame. Sources: WC import, generated image, operator upload.

### PD-9: H3 leakage in routes is technical debt
Not a standalone milestone. Cleanup occurs when Generation API is consolidated.

### PD-10: Timeline is future minimal, semantic continuity deferred
Frame-level continuity is the current baseline.

---

## Gap Registry

### Resolved (P4)

| ID | Description | Resolution |
|----|-------------|------------|
| G1 | Original idea invisible | P4.1 — `original_idea` in sidebar |
| G7 | Reference prompts invisible | P4.2 — prompt preview + editable |
| G8 | Story-specific negative prompts | P4.2a — generic defaults only |
| G9 | Reference prompt not on card | P4.3 — "Show prompt used" toggle |
| G10 | Shot editing split | P4.4 — unified editor |
| G11 | Subjects not editable | P4.4 — character picker |
| G12 | Audio/lighting not editable | P4.4 — partially (dialogue remains G14) |
| G13 | Prompt raw text wall | P4.4 — semantic editor + compiled prompt |
| G16 | Thumbnails not inspectable | P4 UX — 72x72 + click-to-enlarge |
| G18 | No take metadata | P4.9/P4.12 — generation details |
| G19 | No prompt history | P4.9 — historical prompt per Take |

### Open

| ID | Description | Stage | Type | Requires |
|----|-------------|-------|------|----------|
| G2 | WC import results opaque | 1 | UI | API changes |
| G3 | Enrichment is black box | 2 | UI+Backend | API changes |
| G4 | Source attribution missing | 2 | UI+Backend | API changes |
| G5 | Character origin/history | 3 | UI | API changes |
| G6 | No idea↔environment link | 4 | UI | UI only |
| G14 | No dialogue direction | 8 | Model limitation | Investigation |
| G15 | Prompt sections unexplained | 10 | UI | UI only |
| G17 | No side-by-side Take comparison | 12 | UI | UI only |
| G20 | Override state ephemeral | 10 | Schema+UI | Schema extension (PD-4) |
| G21 | Continuity chain not visualized | 13 | UI | UI only (API exists) |
| G22 | No stale assembly indicator | 14 | UI | API changes |
| G23 | No export/download button | 14 | UI | UI only |
| G24 | No canonical Story entity | 2 | Schema | Schema extension (PD-5) |
| G25 | Director Treatment dead | 2 | Schema+Service | Schema extension (PD-5) |
| G26 | No Style Bible | 2 | Schema | Schema extension (PD-5) |
| G27 | No Location entity | 4 | Schema | Schema extension (PD-1) |
| G28 | No Prop entity | 5 | Schema | Schema extension |
| G29 | No storyboard review | 7 | Schema+UI | Schema extension (PD-8) |
| G30 | ReferenceKind too narrow | 9 | Enum extension | Schema extension (PD-7) |
| G31 | No timeline model | 14 | Schema | Schema extension (PD-10) |
