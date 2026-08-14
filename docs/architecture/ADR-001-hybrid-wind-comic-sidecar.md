# ADR-001: Hybrid Wind Comic Sidecar Architecture

**Date:** 2026-08-14
**Status:** Accepted
**Deciders:** Project lead + development team

---

## Context

M0.4 hands-on validation revealed that Wind Comic v12.320 (MIT license) already implements 8 of our first 9 planned milestones: project persistence, LLM abstraction, writer, director, character system, style bible, scene breakdown, and storyboard generation. Building these from scratch would duplicate ~6 months of active development with 4100+ tests.

However, Wind Comic is designed for Chinese short-drama SaaS production, not cinema-grade H3 video production. It lacks: beat decomposition, coverage planning, generation strategy selection, take management, continuity chain, H3 prompt format, ComfyUI video orchestration, and per-shot review/regeneration — the exact capabilities that differentiate our system.

## Decision

**Adopt a Hybrid Sidecar Architecture.**

Wind Comic runs as a separate application alongside ours. Our application consumes Wind Comic's pre-production artifacts through a `WindComicAdapter` boundary, then extends them through our own enrichment, production, and orchestration layers.

```
Wind Comic (port 3000)          Our Application (port 8000)
────────────────────            ─────────────────────────────
Project creation                WindComicAdapter
Writer agent                      → reads WC SQLite
Director agent                  Enrichment Layer
Character Designer                → Beat decomposition
Style Bible                       → Coverage planning
Scene Designer                    → Shot specification
Storyboard Artist               H3 Production Layer
  │                               → Prompt builder
  │                               → ComfyUI adapter
  └──── data/qfmj.db ───────────→ → Take manager
        (SQLite)                  → Continuity manager
                                  → Review / regeneration
                                Timeline / Export
```

## Alternatives Considered

| Option | Verdict | Reason |
|---|---|---|
| A. Wind Comic as sole pre-production app | Rejected | No programmatic API; UI-only interaction too fragile |
| C. Extract MIT-licensed modules | Rejected | High fork maintenance; components tightly integrated |
| D. Build everything from scratch | Rejected | Duplicates 8 milestones; wastes months |
| E. WC for prototype only, then replace | Deferred | May be needed if WC becomes blocking, but not the starting position |

## Consequences

**Positive:**
- Skip M1-M8 duplication (project core, LLM, writer, director, character, style, scene, storyboard)
- Leverage Wind Comic's 4100+ test suite and active development
- Clean separation of concerns: pre-production vs production
- MIT license allows any future integration depth

**Negative:**
- User must run two applications during development/testing
- Wind Comic schema changes on updates may break our adapter
- Pre-production quality depends on Wind Comic's LLM integration quality
- Wind Comic's short-drama orientation may produce suboptimal cinema content

## Risks

1. **Schema breakage on Wind Comic updates** — Mitigated by adapter boundary; adapter tests will detect breakage
2. **Wind Comic project abandoned** — MIT license allows forking; our adapter boundary limits blast radius
3. **gemma4:e4b too small for reliable output** — Need 14B+ model; not a Wind Comic problem but affects pipeline quality
4. **Two-app UX friction** — Acceptable during development; future M9 UI could unify

## Revisit Conditions

- If Wind Comic stops being maintained for >6 months
- If adapter maintenance exceeds 20% of development time
- If Wind Comic's output quality consistently fails to meet our enrichment layer's input requirements
- If a better pre-production tool emerges with an API-first architecture
