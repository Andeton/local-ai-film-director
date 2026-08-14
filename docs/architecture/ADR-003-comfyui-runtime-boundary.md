# ADR-003: ComfyUI Runtime Boundary via REST/WebSocket API

**Date:** 2026-08-14
**Status:** Accepted

---

## Context

ComfyUI provides three access methods: (1) REST API at `127.0.0.1:8188`, (2) WebSocket at `/ws` for real-time monitoring, (3) MCP plugin via Claude Code. M0 verified that the REST API accepts workflow submissions, returns structured errors, and provides queue/history inspection. M0.3 confirmed the MCP provides rich development tooling.

## Decision

**Production runtime uses ComfyUI REST + WebSocket API exclusively. MCP is development tooling only.**

`ComfyUIAdapter` encapsulates all ComfyUI communication:
- POST `/prompt` — submit workflow
- WebSocket `/ws` — monitor execution progress
- GET `/queue` — check queue state
- GET `/history/{prompt_id}` — retrieve results
- GET `/object_info` — node schema discovery (startup only)
- POST `/upload/image` — stage reference images

Workflow templates are external JSON files loaded from a `WorkflowRegistry`. The adapter injects parameters (prompt, images, duration, seed, resolution) into template copies before submission.

## Alternatives Considered

| Option | Verdict | Reason |
|---|---|---|
| MCP as runtime layer | Rejected | Session-scoped, Claude Code dependency, not available to end users |
| Direct Python ComfyUI import | Rejected | Tight coupling; breaks if ComfyUI internals change |
| comfy-cli subprocess | Rejected | Extra dependency; REST API is sufficient and more stable |

## Consequences

- Standard HTTP/WS integration; no exotic dependencies
- Workflow templates are version-controlled JSON files
- Adapter is testable with mock HTTP responses
- WebSocket provides real-time progress without polling

## Revisit Conditions

- If ComfyUI introduces a stable Python SDK that's simpler than REST
- If multi-GPU support requires the comfy-cli orchestration layer
