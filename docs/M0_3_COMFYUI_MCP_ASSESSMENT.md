# M0.3 — ComfyUI MCP Development Tooling Assessment

**Date:** 2026-08-14
**Purpose:** Evaluate the ComfyUI MCP integration as a DEVELOPMENT / DIAGNOSTIC tool.
**Scope:** This MCP is NOT proposed as a production runtime dependency.

---

## 1. MCP Identity

| Property | Value | Status |
|---|---|---|
| Name | `comfyui-mcp` (plugin:comfy:comfyui) | VERIFIED |
| Type | Claude Code MCP plugin (claude.ai integrated) | VERIFIED |
| ComfyUI endpoint | `http://127.0.0.1:8188` | VERIFIED |
| Workspace | `D:\ComfyUI\TD_1\ComfyUI` (auto-detected) | VERIFIED |
| Connection | Active — health check returns full system info | VERIFIED |
| ComfyUI version seen | 0.32.0, frontend 1.48.7, Python 3.13.12, PyTorch 2.12.1+cu130 | VERIFIED |
| GPU seen | NVIDIA GeForce RTX 5090, 30.2/31.8 GB VRAM free | VERIFIED |

---

## 2. Tool Inventory

### 2.1 System / Diagnostics

| Capability | Tool | Verified |
|---|---|---|
| ComfyUI system stats | `get_system_stats` (action:stats) | YES |
| Health check (GPU, VRAM, queue, models, errors) | `get_system_stats` (action:health) | YES |
| Server logs | `get_system_stats` (action:logs) | YES — keyword filtering supported |
| Workspace info | `workspace` (action:get/list) | YES |
| VRAM clear | `clear_vram` | Available |

### 2.2 Node / Model Inspection

| Capability | Tool | Verified |
|---|---|---|
| Node schema inspection | `create_workflow` (action:node_info) | YES — tested with MiniMax and GAPStoryboard filters |
| Node search (fuzzy) | `comfy_cli` (action:search_nodes) | Available |
| Model listing by type | `list_local_models` (action:list) | YES — tested diffusion_models |
| Model metadata read | `model_metadata` (action:read) | Available |
| Model search (HuggingFace) | `download_model` (action:search) | Available |
| Model search (CivitAI) | `download_model` (action:search_civitai) | Available |
| Model download | `download_model` (action:download) | Available |
| Custom node search (registry) | `search_custom_nodes` (action:search) | Available |
| Custom node install/manage | `install_custom_node` | Available |

### 2.3 Workflow Operations

| Capability | Tool | Verified |
|---|---|---|
| List saved workflows | `get_workflow` (action:list) | YES — found 12 workflows including all H3 |
| Read workflow (API format) | `get_workflow` (action:get, format:api) | YES — full R2V API JSON retrieved |
| Read workflow (UI format) | `get_workflow` (action:get, format:ui) | Available |
| Analyze workflow structure | `get_workflow` (action:analyze) | YES — sectioned analysis with connection graph |
| Query workflow nodes | `get_workflow` (action:query) | Available — filter by type/title/widget values |
| Strip workflow (resolve buses/reroutes) | `get_workflow` (action:strip) | Available |
| Slice workflow (extract subgraph) | `get_workflow` (action:slice) | Available |
| Create from template | `create_workflow` (action:create) | Available — txt2img, img2img, etc. (no H3 template) |
| Modify workflow (set_input, add_node, connect) | `create_workflow` (action:modify) | Available |
| Validate workflow | `create_workflow` (action:validate) | Available |
| Visualize (Mermaid) | `visualize_workflow` (action:render) | Available |
| Save workflow | `save_workflow` (action:save) | Available |
| Provenance lock/verify | `save_workflow` (action:lock/verify_lock) | Available |

### 2.4 Execution / Queue

| Capability | Tool | Verified |
|---|---|---|
| Submit workflow | `enqueue_workflow` (action:enqueue) | Available |
| Re-run previous job | `enqueue_workflow` (action:rerun) | Available |
| Batch submit (sweep) | `batch` (action:submit) | Available — param sweeps supported |
| Queue list | `queue` (action:list) | YES — empty queue confirmed |
| Job status | `queue` (action:status) | Available |
| Cancel running job | `queue` (action:cancel) | Available |
| Cancel pending job | `queue` (action:cancel_queued) | Available |
| Clear queue | `queue` (action:clear) | Available |
| Edit pending job | `queue` (action:edit) | Available |
| Wait for jobs | `comfy_cli` (action:jobs_wait) | Available |
| Run workflow file | `comfy_cli` (action:workflow_run) | Available |
| Validate workflow file | `comfy_cli` (action:workflow_validate) | Available |

### 2.5 Output / History

| Capability | Tool | Verified |
|---|---|---|
| Execution history | `get_history` (action:list) | Available |
| Diagnose failures | `get_history` (action:diagnose) | Available |
| List output files | `get_image` (action:list_outputs) | Available |
| Retrieve image (inline) | `get_image` (action:get) | Available |
| View asset | `get_image` (action:view) | Available |
| Color analysis | `get_image` (action:analyze_color) | Available |
| Upload image to input | `upload_image` (action:image) | Available |
| Stage output as input | `upload_image` (action:stage) | Available |

### 2.6 High-Level Generation

| Capability | Tool | Verified |
|---|---|---|
| Generate image (txt2img) | `generate_image` (action:image) | Available |
| Generate video (LTX only) | `generate_image` (action:video) | Available — but LTX-specific, NOT H3 |
| Generate audio | `generate_image` (action:audio) | Available |
| Upscale | `generate_image` (action:upscale) | Available |
| Remove background | `generate_image` (action:remove_background) | Available |

### 2.7 Other

| Capability | Tool |
|---|---|
| Math calculator | `calculate` |
| ComfyUI install/update | `install_comfyui` |
| Node pack authoring | `node_pack` |
| Node snapshots | `node_snapshot` |
| Manifest apply | `apply_manifest` |
| Apps (micro-workflows) | `apps` |
| LoRA training | `train_*` tools |
| RunPod cloud GPUs | `runpod` / `runpod_watch` |
| Bisect (debug custom nodes) | `bisect` |
| Issue reporting | `report_issue` |
| ComfyUI restart/stop/start | `restart_comfyui` |

---

## 3. H3 Compatibility

### 3.1 Node Visibility

| Node | Visible via MCP | Schema Retrieved | Status |
|---|---|---|---|
| EmptyMiniMaxH3LatentAV | YES | Full schema (width, height, length → LATENT) | VERIFIED |
| MiniMaxH3ImageToVideo | YES | Full schema (clip, vae, prompt, first_frame?, last_frame? → CONDITIONING, LATENT) | VERIFIED |
| MiniMaxH3ReferenceToVideo | YES | Full schema (clip, vae, audio_vae, prompt, ref_images, etc. → CONDITIONING, LATENT) | VERIFIED |
| MiniMaxH3SigmaShift | YES | Full schema (model, shift_video, shift_audio → MODEL) | VERIFIED |
| GAPStoryboardPanel | YES | Full schema (image, animation, audio, duration → GAP_STORYBOARD_PANEL) | VERIFIED |
| GAPStoryboardManager | YES | Full schema (all model selectors, panels, refs → VIDEO, IMAGE, AUDIO, STRING) | VERIFIED |

All 6 target H3 nodes are fully visible and inspectable via the MCP.

### 3.2 R2V Workflow Inspection

The MCP successfully:

1. **Listed** `video_minimax_h3_r2v.json` in the workflow library — VERIFIED
2. **Analyzed** the workflow into 7 sections with full connection graph — VERIFIED
3. **Retrieved** the complete API-format JSON with all 22 nodes, all connections, all widget values — VERIFIED
4. **Identified** all parameter injection points:
   - Node 138: prompt text (PrimitiveStringMultiline)
   - Node 137/139/141: reference images (LoadImage)
   - Node 132: duration in seconds (PrimitiveFloat)
   - Node 129: noise seed (RandomNoise)
   - Node 115: aspect ratio (ResolutionSelector)
   - Node 127: diffusion model (UNETLoader)
   - Node 92: output filename prefix (SaveVideo)

### 3.3 H3 Model Visibility

| Model | Visible | Status |
|---|---|---|
| minimax_h3_fl2va_pruned_int8_convrot.safetensors | YES (diffusion_models) | VERIFIED |
| minimax_h3_ref2va_pruned_int8_convrot.safetensors | YES (diffusion_models) | VERIFIED |

---

## 4. Workflow Development Capability Assessment

### A. Inspect the R2V workflow graph

**SUPPORTED** — `get_workflow` (action:analyze) returns a structured breakdown with sections, node settings, and full connection graph. Verified with the R2V workflow.

### B. Identify parameter injection points

**SUPPORTED** — The analysis identifies every node with its widget values and connections. The API-format JSON (action:get) provides the exact node IDs and input names needed for parameter injection via `create_workflow` (action:modify, op:set_input).

### C. Construct API-compatible T2V/I2V workflows from native H3 nodes

**SUPPORTED** — `create_workflow` (action:modify) supports add_node, connect, and set_input operations. Combined with `create_workflow` (action:node_info) to query exact input/output schemas, workflows can be constructed programmatically. However, there is no built-in H3 workflow template (only txt2img, img2img, ltx_video, etc.), so construction must be done from individual nodes.

### D. Validate generated workflow JSON

**SUPPORTED** — `create_workflow` (action:validate) checks for missing node types, broken connections, invalid outputs, missing models. `comfy_cli` (action:workflow_validate) provides file-based validation.

### E. Diagnose ComfyUI execution errors

**SUPPORTED** — `get_history` (action:diagnose) explains failures with the failed node, exception type, message, traceback, missing models, and missing node types. `get_system_stats` (action:logs) provides server logs with keyword filtering.

### F. Inspect outputs after generation

**SUPPORTED** — `get_image` (action:list_outputs) lists recent outputs including video files. `get_image` (action:get) retrieves images inline; videos are saved to disk. `get_history` (action:list) returns full output metadata.

### G. Benchmark workflows

**PARTIALLY SUPPORTED** — `batch` (action:submit) supports parameter sweeps (e.g., different seeds, steps, resolutions). Combined with `get_history` (action:stats) for generation statistics. However, there is no built-in wall-clock benchmarking tool — timing must be derived from job history timestamps.

### H. Test GAPStoryboardManager programmatically

**PARTIALLY SUPPORTED** — The node schemas are fully visible, and workflows can be constructed via `create_workflow` (action:modify). However, the COMFY_AUTOGROW_V3 panel inputs may require special handling for programmatic panel injection. Storyboard workflows can also be submitted via `enqueue_workflow` using the pre-existing storyboard workflow JSON with modified inputs.

---

## 5. MCP vs Direct API Comparison

| Aspect | Claude Code → ComfyUI MCP | Application → ComfyUI REST/WebSocket |
|---|---|---|
| **Purpose** | Development, debugging, experimentation | Production runtime |
| **User** | Developer (via Claude Code) | End user (via application UI) |
| **Availability** | Only during Claude Code sessions | Always (application runtime) |
| **Workflow inspection** | Rich analysis, visualization, query, validation | Raw JSON only |
| **Workflow construction** | Interactive modify/validate cycle with AI assistance | Template-based parameter injection |
| **Node discovery** | Fuzzy search, schema inspection, registry search | Direct /object_info queries |
| **Error diagnosis** | Structured diagnosis with missing model/node detection | Raw error JSON parsing |
| **Job submission** | Via MCP tools (enqueue_workflow, batch) | Direct POST /prompt |
| **Job monitoring** | Via MCP tools (queue status, history) | WebSocket + polling |
| **Output retrieval** | Inline image viewing, file listing | Direct /view or filesystem |
| **Batch operations** | Parameter sweeps, batch submit/wait | Custom queue management |
| **Model management** | Search, download, metadata, path management | Not applicable |
| **State** | Stateless (each Claude session is fresh) | Persistent application state |
| **Latency** | MCP protocol overhead (acceptable for dev) | Direct HTTP (minimal) |
| **Reliability** | Depends on Claude Code session | Application-controlled |

### Responsibility Split

**MCP is for the developer (us), not for the end user:**

```
DEVELOPMENT TIME (Claude Code + MCP):
  - Discover H3 node schemas
  - Construct and validate workflow templates
  - Debug workflow failures
  - Experiment with parameters
  - Benchmark generation settings
  - Prototype production pipelines
  - Inspect generated outputs

PRODUCTION RUNTIME (Application + REST/WS API):
  - Submit parameterized workflows
  - Monitor execution via WebSocket
  - Retrieve results via /history
  - Manage job queue
  - Handle errors and retries
  - Track takes and continuity
```

---

## 6. Limitations

1. **No H3 workflow template** — `generate_image` (action:video) only supports LTX. H3 generation requires constructing workflows from nodes or using existing workflow files.

2. **No native H3 prompt builder** — The MCP cannot translate a shot specification to an H3 prompt. This is our application's responsibility.

3. **COMFY_AUTOGROW_V3 inputs** — The autogrow panel inputs (used by R2V ref_images and StoryboardManager panels) require special handling. The API-format JSON uses `ref_images.ref_image_0`, `ref_images.ref_image_1`, etc., which the MCP correctly reads but may need care when constructing programmatically.

4. **Session-scoped** — MCP state does not persist across Claude Code sessions. Workflow templates and discoveries must be saved to project files.

5. **Not a production dependency** — The MCP is a development tool. The production application must use the ComfyUI REST/WebSocket API directly.

6. **Video output handling** — Videos are saved to disk rather than returned inline. Output inspection requires filesystem access or `get_image` (action:list_outputs).

7. **No real-time WebSocket monitoring** — The MCP provides polling-based job status, not a persistent WebSocket connection for real-time progress events.

---

## 7. Security / Reliability Considerations

| Consideration | Assessment |
|---|---|
| No secrets exposed | MCP does not store or transmit API keys for our application |
| Read-only by default | Most inspection tools are read-only; writes require explicit actions |
| No production dependency | MCP is not in the runtime path |
| ComfyUI access | Same localhost:8188 as our application will use |
| Model downloads | MCP can trigger downloads (development convenience, not production) |
| Server restart | MCP can restart ComfyUI — use with caution during shared development |
| Workflow save | MCP can overwrite saved workflows — coordinate with manual workflow development |

---

## 8. Recommended Development Role

### Best Uses During Development

1. **Workflow template construction** — Use `create_workflow` (action:modify) + (action:validate) to build T2V/I2V/R2V templates from native H3 nodes, then export the validated JSON for our application to use.

2. **H3 prompt experimentation** — Use `enqueue_workflow` to submit R2V workflows with different prompt formats and parameters, then `get_image` to inspect results.

3. **Node schema reference** — Use `create_workflow` (action:node_info) to query exact input/output schemas when building our ComfyUI adapter.

4. **Failure diagnosis** — Use `get_history` (action:diagnose) when ComfyUI returns errors during development, instead of manually parsing logs.

5. **Batch benchmarking** — Use `batch` (action:submit) with parameter sweeps to test generation times, quality vs. steps, resolution impact, etc.

6. **Model discovery** — Use `download_model` (action:search) and `list_local_models` to verify model availability when setting up workflows.

7. **Workflow validation** — Use `create_workflow` (action:validate) to verify our generated workflow JSON is correct before submitting to ComfyUI in the production app.

### When to Use Direct API Instead

- **Production runtime** — Always use REST/WebSocket directly
- **Real-time monitoring** — WebSocket gives event-by-event progress
- **High-frequency operations** — Direct API has lower latency
- **Application integration testing** — Test the actual adapter code, not MCP proxied operations
- **Concurrent job management** — Application queue manager needs direct control

---

## 9. Production-Runtime Recommendation

**The ComfyUI MCP must NOT be a production dependency.**

The production architecture remains:

```
Application (FastAPI)
    → ComfyUI Adapter (our code)
    → ComfyUI REST API (POST /prompt, GET /queue, /history)
    → ComfyUI WebSocket (/ws for real-time monitoring)
    → MiniMax H3 (via ComfyUI nodes)
```

The MCP is a development accelerator: it helps us discover, construct, validate, and debug workflows faster. Its outputs (validated workflow templates, node schemas, parameter maps) feed INTO our application design, but the MCP itself is never in the runtime path.
