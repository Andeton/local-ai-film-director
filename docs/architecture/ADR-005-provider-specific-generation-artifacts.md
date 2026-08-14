# ADR-005: Provider-Specific Generation Artifacts Separated from Canonical Model

**Date:** 2026-08-14
**Status:** Accepted
**Corrects:** M0.5 ARCHITECTURE_V1.md (original had H3 fields inside Shot and CharacterReference)

---

## Context

The original M0.5 architecture placed three H3-specific fields directly inside canonical entities:

- `Shot.h3_prompt` — MiniMax H3 prompt text
- `Shot.h3_workflow_id` — H3 workflow template ID
- `CharacterReference.h3_subject_definition` — H3 `<Subject N>` definition

This violated ADR-002's principle that the canonical Production Specification should be model-agnostic. The original spec (§7) explicitly states: "Director layer should not know that MiniMax H3 exists." Mixing provider-specific data into canonical entities would:

1. Prevent future engine switching (e.g., adding WAN 2.2, LTX, Kling)
2. Force Shot schema changes for every new provider
3. Blur the boundary between "what to produce" and "how to produce it"
4. Make invalidation logic provider-dependent

## Decision

**Separate provider-specific data into distinct derived artifacts.**

Three layers, each with clear ownership:

| Layer | Contains | Example |
|---|---|---|
| **ShotSpecificationV1** (model-agnostic) | Narrative intent: subjects, action, camera, lighting, audio, duration | "Close-up, detective reacts, flickering fluorescent light, 6 seconds" |
| **GenerationPlan** (model-agnostic) | Production strategy: engine family, strategy type, reference requirements, seed policy | "REFERENCE_TO_VIDEO via minimax_h3, needs character refs, random seed" |
| **H3PromptV1** (provider-specific) | MiniMax H3 prompt structure: subject_definitions, retention_analysis, detailed_description, soundscape | Full H3 prompt text with `<Subject N>` / `<Picture N>` tags |

### What was removed from ShotSpecificationV1

- `h3_prompt` → moved to **H3PromptV1.rendered_prompt_text**
- `h3_workflow_id` → moved to **GenerationPlan.workflow_profile** (generic) + resolved to specific workflow at **GenerationRequest** time
- `generation_strategy` → moved to **GenerationPlan.strategy** (using generic enum values)
- `seed` → moved to **GenerationPlan.seed** / **GenerationRequest.seed**
- `resolution` → moved to **GenerationPlan.resolution_intent**

### What was removed from CharacterReference

- `h3_subject_definition` → derived at build time by **H3PromptBuilder** from model-agnostic fields (name, description, appearance, visual_anchors)

### What was added

- **GenerationPlan** — model-agnostic production strategy per shot
- **H3PromptV1** — provider-specific derived artifact with full MiniMax H3 prompt structure
- **GenerationRequest** — immutable snapshot capturing exact shot_version, prompt_artifact_version, workflow_definition_version, parameters, references, and seed used for each submission

## Alternatives Considered

| Option | Verdict | Reason |
|---|---|---|
| Keep H3 fields in Shot with "ignore if not H3" convention | Rejected | Schema pollution; every new engine adds columns |
| Generic `provider_data` JSON blob on Shot | Rejected | Loses type safety; still couples Shot to providers |
| No GenerationPlan (derive strategy at submission time) | Rejected | Loses human editability and auditability of strategy decisions |

## Consequences

**Positive:**
- ShotSpecificationV1 is purely about narrative/production intent — reusable across any video engine
- Adding a new engine (e.g., WAN 2.2) requires only a new PromptBuilder and WorkflowDefinitions — no Shot schema changes
- GenerationPlan is human-editable: user can override strategy before generation
- GenerationRequest is an immutable audit trail: every Take can be exactly reproduced
- Invalidation chain is clean: upstream change → outdated plan → stale prompt → user decides

**Negative:**
- More entities to manage (GenerationPlan + H3PromptV1 alongside Shot)
- Prompt building requires joining Shot + CharacterReferences + GenerationPlan

**Acceptable cost:** The join complexity is limited to the H3PromptBuilder, which already needs all this data. The additional entities are small and their lifecycle is clear.

## Risks

1. **Over-engineering for a single engine** — Mitigated: the separation is lightweight (two small tables) and the generic strategy enum is already useful for choosing between R2V/T2V/I2V workflows
2. **GenerationPlan version drift** — Mitigated: GenerationRequest snapshots the exact versions used

## Revisit Conditions

- If the project permanently commits to a single engine and never adds another, the GenerationPlan could be merged back into Shot (unlikely given the spec's multi-engine roadmap in §65)
- If H3PromptV1 becomes the only prompt format ever needed, it could be simplified to a single prompt text field on GenerationPlan
