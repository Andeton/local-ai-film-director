# ADR-002: Canonical Production Specification Independent of Wind Comic

**Date:** 2026-08-14
**Status:** Accepted

---

## Context

Wind Comic uses a flat `project_assets` table with JSON `data` blobs. This schema is optimized for a SaaS comic/short-drama pipeline and lacks: beats, coverage, generation strategy, takes, continuity state, and H3-specific metadata. Our production system needs a richer hierarchical model (Project → Sequence → Scene → Beat → Shot → Take) with explicit dependency tracking and provenance.

## Decision

**Define our own canonical Production Specification data model, completely independent of Wind Comic's schema.**

Wind Comic data enters our system ONLY through `WindComicAdapter`, which normalizes it into our canonical types. Our application never reads Wind Comic tables directly — only the adapter does.

Our canonical model:
- `ProductionProject` — top-level container linking to a Wind Comic project
- `Sequence` → `Scene` → `Beat` → `Shot` → `Take` — hierarchical production graph
- `CharacterReference`, `StyleReference`, `SceneReference` — resolved reference assets
- `ContinuityState` — per-shot state tracking
- `GenerationRequest` / `GenerationResult` — ComfyUI job tracking
- `ReviewResult` — AI + human review data

## Alternatives Considered

| Option | Verdict | Reason |
|---|---|---|
| Use Wind Comic schema directly | Rejected | Missing beats, coverage, takes, continuity; couples us to WC internals |
| Extend Wind Comic schema | Rejected | Modifying WC code violates sidecar boundary; upgrade breakage |
| Use Wind Comic schema + overlay tables | Rejected | Split-brain persistence; complex joins across schemas |

## Consequences

- Clean internal model optimized for cinema production
- Adapter boundary absorbs all Wind Comic schema changes
- Our model can evolve independently
- Cost: must define and maintain our own schema (but much smaller than WC's full schema — we only model production entities, not users/billing/teams)

## Revisit Conditions

- If Wind Comic adds beats/coverage/takes natively, adapter can import richer data without changing our model

## Related

- **ADR-005** reinforces this decision by further separating the provider-specific prompt artifact (H3PromptV1) from the canonical Shot and CharacterReference entities. GenerationPlan is model-agnostic (describes production strategy without provider-specific content) and belongs to the canonical model. H3PromptV1 is MiniMax H3-specific and is a derived artifact. GenerationRequest captures the immutable resolved execution snapshot linking both.
