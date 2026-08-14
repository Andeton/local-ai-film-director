# ADR-004: ComfyUI MCP as Development Tool Only

**Date:** 2026-08-14
**Status:** Accepted

---

## Context

M0.3 verified that the ComfyUI MCP plugin provides comprehensive development capabilities: node schema inspection, workflow analysis/construction/validation, batch execution with parameter sweeps, structured error diagnosis, and output inspection. These accelerate development significantly.

## Decision

**ComfyUI MCP is approved for development use only. It is explicitly prohibited as a production runtime dependency.**

### Approved Development Uses

- Inspect H3 node schemas (`create_workflow` action:node_info)
- Analyze existing workflows (`get_workflow` action:analyze)
- Construct new workflow templates (`create_workflow` action:modify)
- Validate generated workflow JSON (`create_workflow` action:validate)
- Debug execution failures (`get_history` action:diagnose)
- Benchmark generation settings (`batch` action:submit with sweeps)
- Experiment with prompts and parameters
- Inspect generated outputs (`get_image`)
- Download models during development setup (`download_model`)

### Prohibited Uses

- MCP tools in production application code
- MCP as the ComfyUI communication layer
- MCP for end-user-facing operations
- MCP session state as persistent application state

## Consequences

- Development velocity increased by MCP's rich tooling
- Production code remains simple (HTTP/WS only)
- No deployment dependency on Claude Code or MCP infrastructure
- Workflow templates created via MCP are exported as static JSON files for production use

## Revisit Conditions

- Not expected to change unless MCP becomes a stable, deployable runtime library independent of Claude Code
