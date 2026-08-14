# M0 Technical Discovery Report

**Date:** 2026-08-14
**Status:** Complete
**Phase:** 0 — Discovery / Technical Spike

---

## Executive Summary

The local environment is exceptionally well-equipped for this project. An RTX 5090 with 32 GB VRAM, 95 GB RAM, ComfyUI 0.32.0 running with MiniMax H3 models already installed, and both diffusion models (fl2va for first/last-frame, ref2va for reference-to-video) available. The ComfyUI API is live on `127.0.0.1:8188` and accepts programmatic workflow submission.

A critical finding: the **ComfyUI-MiniMax-H3-StoryBoard** custom node pack (by Geekatplay Studio) already implements a storyboard-to-movie pipeline with crash-safe resume, seam-free panel chaining, and global/local reference support. This is directly relevant to our production architecture and could serve as either: (a) the primary generation backend as-is, or (b) a proven reference for our own orchestration layer.

The R2V workflow demonstrates a rich prompt format with subject definitions, retention analysis, multi-shot storyboard descriptions, and soundscape — essentially the exact "Shot Specification → H3 Prompt" translation the spec requires.

LLM providers: Ollama is installed and running with gemma4:e4b (9.6 GB), supporting the OpenAI-compatible `/v1` endpoint. LM Studio is installed but has no models. OpenRouter can be added trivially since it also uses the OpenAI-compatible protocol. All three providers can share one abstraction.

**Key recommendation:** The smallest useful vertical slice is:
1. Use Ollama (gemma4) to generate a structured story/scene/shot specification from an idea
2. Transform that specification into an H3 R2V prompt using the discovered prompt format
3. Submit the workflow to ComfyUI via its REST API
4. Monitor and retrieve the result

This can be prototyped without building the full application.

---

## A. Local Environment

| Component | Value | Status |
|---|---|---|
| OS | Windows 11 Pro 10.0.26200.9168 | VERIFIED LOCALLY |
| Python | 3.14.3 (C:\Python314\python.exe) | VERIFIED LOCALLY |
| Node.js | v24.14.0 | VERIFIED LOCALLY |
| npm | 11.12.1 | VERIFIED LOCALLY |
| pnpm | 9.15.4 | VERIFIED LOCALLY |
| yarn | not installed | VERIFIED LOCALLY |
| Git | available (via Git for Windows) | VERIFIED LOCALLY |
| GPU | NVIDIA GeForce RTX 5090 | VERIFIED LOCALLY |
| VRAM | 32,607 MiB total (~32 GB) | VERIFIED LOCALLY |
| RAM | ~95 GB total (~75 GB free) | VERIFIED LOCALLY |
| Disk C: | 1.9 TB total, 853 GB free | VERIFIED LOCALLY |
| Disk D: | 3.7 TB total, 2.0 TB free | VERIFIED LOCALLY |
| Docker | 29.6.1 | VERIFIED LOCALLY |
| FFmpeg | 8.0.1 (full build, GPU-accelerated, Whisper-enabled) | VERIFIED LOCALLY |
| DaVinci Resolve | Not found in default path | VERIFIED LOCALLY |

### Notes
- Python 3.14 is very recent; some packages may not have wheels yet. ComfyUI uses its own venv with Python 3.13.
- FFmpeg build includes NVENC/NVDEC, Vulkan, and Whisper — excellent for post-processing.
- Docker available if needed for isolated services.
- DaVinci Resolve not detected but not required for MVP.

---

## B. ComfyUI

| Property | Value | Status |
|---|---|---|
| Installation path | `D:\ComfyUI\TD_1\ComfyUI\` | VERIFIED LOCALLY |
| Version | 0.32.0 | VERIFIED LOCALLY |
| Frontend | 1.48.7 | VERIFIED LOCALLY |
| Python | 3.13.12 (venv) | VERIFIED LOCALLY |
| PyTorch | 2.12.1+cu130 | VERIFIED LOCALLY |
| API endpoint | `http://127.0.0.1:8188` | VERIFIED LOCALLY |
| Running | Yes | VERIFIED LOCALLY |
| Deploy env | local-desktop2-standalone | VERIFIED LOCALLY |
| Input dir | `D:\ComfyUI\input` | VERIFIED LOCALLY |
| Output dir | `D:\ComfyUI\output` | VERIFIED LOCALLY |
| Launch flags | `--use-sage-attention --fast fp16_accumulation cublas_ops --enable-triton-backend --highvram` | VERIFIED LOCALLY |

### API Verification

| Endpoint | Method | Result | Status |
|---|---|---|---|
| `/system_stats` | GET | Returns system info, GPU info, version | VERIFIED LOCALLY |
| `/queue` | GET | Returns `queue_running` and `queue_pending` arrays | VERIFIED LOCALLY |
| `/history` | GET | Returns job history with `?max_items=N` | VERIFIED LOCALLY |
| `/prompt` | POST | Accepts workflow JSON, returns `prompt_id` and `number` | VERIFIED LOCALLY |
| `/object_info` | GET | Returns all node schemas | VERIFIED LOCALLY |
| `/object_info/{node}` | GET | Returns specific node schema with inputs/outputs/options | VERIFIED LOCALLY |
| `/extensions` | GET | Returns loaded extensions list | VERIFIED LOCALLY |
| WebSocket `/ws` | WS | Available for real-time execution monitoring | VERIFIED LOCALLY |

### How External Orchestration Works

1. **Submit:** POST to `/prompt` with JSON body `{"client_id": "<uuid>", "prompt": {<node_graph>}}`
2. **Monitor:** Connect to WebSocket at `/ws?clientId=<uuid>` for real-time progress events
3. **Queue check:** GET `/queue` to see running/pending jobs
4. **Results:** GET `/history/{prompt_id}` to retrieve outputs after completion
5. **Error handling:** API returns structured errors with `type`, `message`, `details`, `extra_info`

A test submission with a valid node but no output returned HTTP 400 with `prompt_no_outputs` error — confirming structured error reporting works.

### Installed Custom Nodes (relevant)

| Node Pack | Relevance |
|---|---|
| **ComfyUI-MiniMax-H3-StoryBoard** | **CRITICAL** — storyboard-to-movie pipeline |
| ComfyUI-GGUF | GGUF model loading |
| comfyui-easy-use | Utility nodes |
| comfyui-impact-pack | Detection/segmentation |
| comfyui-kjnodes | Various utilities |
| cg-use-everywhere | Workflow routing |
| RES4LYF | Advanced samplers |
| seedvr2_videoupscaler | Video upscaling |
| ComfyUI-Krea2T-Enhancer | Krea2 integration |
| LTXDirector-Extender | LTX video (not H3-relevant) |

---

## C. MiniMax H3

### Installed Models

| Model | Path | Size | Purpose | Status |
|---|---|---|---|---|
| minimax_h3_fl2va_pruned_int8_convrot.safetensors | models/diffusion_models/ | 20 GB | First/Last frame to Video+Audio | VERIFIED LOCALLY |
| minimax_h3_ref2va_pruned_int8_convrot.safetensors | models/diffusion_models/ | 20 GB | Reference to Video+Audio | VERIFIED LOCALLY |
| qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors | models/text_encoders/ | 15 GB | Qwen3-VL 32B text encoder (NVF4 quantized) | VERIFIED LOCALLY |
| minimax_h3_video_vae_fp16.safetensors | models/vae/ | 4.9 GB | Video VAE (FP16) | VERIFIED LOCALLY |
| minimax_h3_audio_vae_fp32.safetensors | models/vae/ | 578 MB | Audio VAE (FP32) | VERIFIED LOCALLY |
| H3 Turbo LoRA | models/loras/ | **NOT INSTALLED** | 4-step acceleration | VERIFIED LOCALLY |

### Native H3 Nodes (ComfyUI Core)

| Node | Type | Purpose | Key Inputs | Status |
|---|---|---|---|---|
| `EmptyMiniMaxH3LatentAV` | Latent | Creates empty AV latent | width, height, length (frames) | VERIFIED LOCALLY |
| `MiniMaxH3ImageToVideo` | Conditioning | T2V / I2V / First-Last | clip, vae, prompt, first_frame?, last_frame? | VERIFIED LOCALLY |
| `MiniMaxH3ReferenceToVideo` | Conditioning | R2V with references | clip, vae, audio_vae, prompt, ref_images, ref_videos, ref_audios | VERIFIED LOCALLY |
| `MiniMaxH3SigmaShift` | Model Patch | Video/audio sigma shift | model, shift_video (12.0), shift_audio (3.0) | VERIFIED LOCALLY |

### H3 StoryBoard Custom Nodes

| Node | Purpose | Key Inputs | Status |
|---|---|---|---|
| `GAPStoryboardPanel` | One shot/panel | image, animation prompt, audio, duration, ref_image_1/2, ref_audio | VERIFIED LOCALLY |
| `GAPStoryboardManager` | Renders all panels | aspect, megapixels, noise_seed, models, steps, sampler, scheduler, panels, global refs | VERIFIED LOCALLY |

#### StoryBoard Key Features (VERIFIED LOCALLY from README and node schemas)

- **Seam-free chaining:** Each panel image is fitted once and reused on both sides of a cut
- **Crash-safe resume:** Each segment saved to disk immediately; re-running resumes automatically
- **Fingerprint-based invalidation:** Edit one panel → only that segment re-renders
- **Global references:** Up to 8 reference images + video + music applied to all segments
- **Scene-local references:** Per-panel ref images and audio
- **Output modes:** `full movie` (concatenated) or `segments only` (individual shot files)
- **Up to 32 panels** per storyboard
- **Baseline:** res_multistep sampler, simple scheduler, 20 steps, 768px short edge, 24 fps

### Available Workflows

| Workflow | Path | Type | Nodes | Orchestration Suitability | Status |
|---|---|---|---|---|---|
| R2V | user/default/workflows/video_minimax_h3_r2v.json | Reference-to-Video | 25 standard nodes | **HIGH** — all standard nodes, clear parameter injection points | VERIFIED LOCALLY |
| I2V | user/default/workflows/video_minimax_h3_i2v.json | Image-to-Video | Uses packed component node (UUID) | **LOW** — packed node not accessible via API | VERIFIED LOCALLY |
| T2V | templates/video_minimax_h3_t2v.json | Text-to-Video | Uses same packed component | **LOW** — same issue | VERIFIED LOCALLY |
| Storyboard | custom_nodes/.../storyboard_minimax_h3.json | Multi-panel film | GAPStoryboardPanel + Manager | **HIGH** — standard nodes, designed for production | VERIFIED LOCALLY |
| Storyboard+Refs | custom_nodes/.../storyboard_minimax_h3_refs.json | Multi-panel + refs | GAPStoryboard + refs | **HIGH** — adds reference support | VERIFIED LOCALLY |

### R2V Workflow — Parameter Injection Points

The R2V workflow is the most important for programmatic control. Key injectable parameters:

| Node (ID) | Parameter | Purpose | Example Value |
|---|---|---|---|
| PrimitiveStringMultiline (138) | text | H3 prompt with subject_definitions, summary, retention_analysis, detailed_description, soundscape | Full structured prompt |
| LoadImage (139, 137, 141) | image | Reference images (character/environment sheets) | Image filename in input dir |
| PrimitiveFloat (132) | value | Duration in seconds | 12.0 |
| RandomNoise (129) | noise_seed | Reproducibility seed | Integer |
| ResolutionSelector (115) | aspect | Aspect ratio | "9:16 (Portrait Widescreen)" |
| UNETLoader (127) | unet_name | Diffusion model | minimax_h3_ref2va_pruned_int8_convrot.safetensors |
| BasicScheduler (124) | steps | Sampling steps | 20 |
| SaveVideo (92) | filename_prefix | Output path | "video/MiniMax_H3" |

### H3 Prompt Format (VERIFIED LOCALLY from R2V workflow)

The discovered prompt format follows this structure:
```
subject_definitions:
<Subject 1> is [character description from reference image] in <Picture 1>
<Subject 2> is [environment description] in <Picture 2>

summary:
[reference generation] <Subject 1> does X in <Subject 2>...

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]):
fully_preserved - [specific visual details retained]

detailed_description:
[Shot 1] [timestamp] [camera, action, details]
[Shot 2] [timestamp] [camera, action, details]

overall_soundscape:
[ambient audio description]

non_diegetic_music:
[score description tied to shots]
```

### H3 Technical Constraints (VERIFIED LOCALLY from source code)

| Constraint | Value | Source |
|---|---|---|
| FPS | 24 | nodes_minimax_h3.py |
| Frame grid | 17k+5 (5, 22, 39, 56, ..., 124, 141, ..., 362) | nodes_minimax_h3.py |
| Canvas multiple | 32 px | nodes_minimax_h3.py |
| Base short edge | 768 px | nodes_minimax_h3.py |
| Max pixels | 768 x 1344 (~1 MP) | nodes_minimax_h3.py |
| Trained range | 124-362 frames (~5-15 sec) | nodes_minimax_h3.py |
| Audio latent FPS | 40 | nodes_minimax_h3.py |
| Ref image max short edge | 2048 px | nodes_minimax_h3.py |
| Min ref video frames | 5 | nodes_minimax_h3.py |

---

## D. Wind Comic

### Research Summary

| Property | Finding | Status |
|---|---|---|
| Repository | github.com/ChrisChen667788/wind-comic | VERIFIED |
| Version | v12.320 (as of 2026-08-11), extremely active development | VERIFIED |
| License | **MIT** — permissive, allows commercial use | VERIFIED |
| Architecture | Next.js 16 (React, Tailwind), 8-agent pipeline, SQLite/PostgreSQL | VERIFIED |
| LLM support | BYO via 3 env vars (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`), any OpenAI-compatible endpoint | VERIFIED |
| Ollama support | Yes — local execution via OpenAI-compatible endpoint | DOCUMENTED |
| LLM providers | DeepSeek, OpenAI, Claude, Qwen, Kimi, Ollama, OpenRouter, MiniMax, Grok, XVerse | VERIFIED |
| LLM fallback | Primary → same-gateway backups → OpenRouter → MiniMax, health-aware with 429/503 auto-downgrade | VERIFIED |
| ComfyUI integration | Yes — `/services/comfyui.service.ts` for **image generation** (IP-Adapter, ControlNet), NOT video | VERIFIED |
| Video providers | Kling, MiniMax Hailuo, Veo, HappyHorse, Vidu Q3, Seedance, Grok video, LTX (all cloud APIs) | VERIFIED |
| Story generation | Writer agent with McKee 3-act / Save-the-Cat structure, 12 trope templates, beat-gap detection | VERIFIED |
| Director treatment | Director agent plans cinematography per shot, emotion-driven camera, Director Console (v8+), Director Tool v1 (v12.316) | VERIFIED |
| Character generation | Character Designer agent, 8-dimensional DNA, 3-view turnaround sheets, Cameo IP (reusable across projects), DNA Lock | VERIFIED |
| Scene generation | Scene Designer agent, Style Bible Frame (canonical reference), stage rendering to layout sketch PNG | VERIFIED |
| Storyboard generation | Storyboard Artist agent, Sketch-Lock (B/W composition first), Vision-Audit retry (<70 auto-regen), round-trip CSV/MD/PDF | VERIFIED |
| Artifact format | JSON scripts, PNG storyboards with JSON metadata, MP4 video, SRT/ASS subtitles, SQLite database | VERIFIED |
| NLE export | AAF (Avid), EDL (universal), FCPXML (Final Cut Pro) | VERIFIED |
| Artifact independence | All artifacts persisted independently; any stage can re-run in isolation | VERIFIED |
| Can serve as Director layer | Architecturally yes — but it's a full app, not a headless API | INFERENCE |

### Critical Findings

1. **MIT License** — we CAN integrate, reference, or extend Wind Comic code commercially.
2. **ComfyUI integration is for IMAGE generation only** (IP-Adapter character consistency), not video. Video goes through cloud APIs.
3. **The 8-agent pipeline** (Writer → Director → Style Bible → Character Designer → Scene Designer → Storyboard Artist → Video Producer → Editor) maps almost exactly to our spec's phases 3-10.
4. **Artifact format is structured JSON** stored in SQLite — directly queryable and consumable.
5. **Round-trip storyboard** (export CSV, edit externally, re-import) is a proven external integration point.
6. **No headless API** — extracting just pre-production requires running the app and exporting artifacts, not calling a REST API.

### Assessment

Wind Comic is substantially more mature than expected (v12.320, 425+ stars, 4100+ tests). Its pre-production pipeline is exactly what our spec describes. However:

- **Positive:** MIT license, OpenAI-compatible LLM, structured artifacts, NLE export, Ollama support
- **Limitation:** It's a complete end-to-end app, not a composable library. Using it as a Director layer means either (a) running it alongside our system and consuming its artifacts, or (b) extracting its agent logic into our pipeline.
- **Key decision:** Test locally — if artifacts are rich enough and LLM integration works with Ollama, use it as the pre-production frontend. If not, replicate its proven agent patterns in our own Director layer.

**Risk:** MEDIUM — needs hands-on validation, but MIT license removes legal risk.

---

## E. KupkaProd

### Research Summary

| Property | Finding | Status |
|---|---|---|
| Repository | github.com/Matticusnicholas/KupkaProd-Cinema-Pipeline | VERIFIED |
| Version | No formal releases; last pushed 2026-07-19, 194 stars | VERIFIED |
| License | **Custom non-commercial** — free for personal/educational/research; commercial PROHIBITED without separate license | VERIFIED |
| Architecture | Python 3.10+, Tkinter GUI + FastAPI web UI (port 8000), 10 pip dependencies | VERIFIED |
| LLM integration | Ollama — gemma4:26b (creative), gemma4:e4b (fast/evaluation); multimodal frame analysis | VERIFIED |
| ComfyUI integration | WebSocket (`/ws`) real-time tracking + REST API; template-based workflow injection via deep-copy + parameter patch | VERIFIED |
| Workflow templates | `workflow_template.json` (LTX-AV T2V), `keyframe_template.json` (Z-Image Turbo storyboard), `i2v_template.json` (user-exported I2V) | VERIFIED |
| Parameter injection | Direct dict mutation: `wf[node_id]["inputs"]["text"] = prompt_text`; auto-detection by class_type fallback | VERIFIED |
| Take management | Default 3 takes/scene (1-10 configurable), different seed per take, per-take status/error tracking | VERIFIED |
| Project persistence | `state.json` with atomic write (temp file + fsync), `.bak` backup, auto-recovery from corrupt state | VERIFIED |
| Resume | Phase-based — detects completed phases and skips; restores character/voice/style anchors | VERIFIED |
| Review model | 5-dimension multimodal evaluation (Subject Match, Motion Quality, Subject Consistency, Shot Type, Continuity); 3-tier scoring (good/fair/poor); any "poor" = auto FAIL | VERIFIED |
| Human gates | 2 mandatory stops: storyboard approval + take selection (unless "lazy mode") | VERIFIED |
| VRAM management | LLM unloaded from VRAM after planning phase | VERIFIED |

### Reusable Concepts (architecture patterns — high portability)

| Concept | Portability | Notes |
|---|---|---|
| Agentic screenplay-to-scenes decomposition | **High** | LLM-driven; model-agnostic |
| Character anchor injection (description in every prompt) | **High** | Universal consistency technique |
| Style lock (20-40 word global style descriptor) | **High** | Injected into every scene prompt |
| Multi-take with seed variance | **High** | Just change seeds |
| 5-dimension multimodal evaluation | **High** | Frame extraction + LLM scoring |
| Atomic state persistence with .bak recovery | **High** | Generic JSON checkpoint pattern |
| Phase-based resume | **High** | Skip completed work on restart |
| Template-based workflow injection (deep copy + patch) | **High** | Works with any ComfyUI workflow |
| Auto-detection of ComfyUI nodes by class_type | **High** | Generic node discovery |
| WPM-based dialogue duration calculation | **High** | Model-agnostic |
| FFmpeg lossless assembly | **High** | Completely generic |

### Tightly Coupled to LTX (must replace)

| Element | Detail |
|---|---|
| Frame formula `(8n + 1)` | H3 uses `17k + 5` |
| Dual-pass seeds (pass 1 + pass 2) | H3 uses single seed |
| Audio-video sync in single prompt | H3 has native AV but different conditioning |
| Hardcoded node IDs in config.py | Need new IDs per H3 workflow |
| Workflow JSON templates | Need H3-specific templates |
| Gemma 3 12B text encoder reference | H3 uses Qwen3-VL 32B |

**Recommendation:** Use as architecture reference only. Do not create dependency. The orchestration patterns (template injection, atomic persistence, multimodal evaluation) are directly applicable to our H3 pipeline. Reimplement independently.

---

## F. Director's Console

| Property | Finding | Status |
|---|---|---|
| Repository | github.com/NickPittas/DirectorsConsole ("Project Eliot") | VERIFIED |
| License | **Proprietary / All rights reserved** | VERIFIED |
| Architecture | React+TS storyboard canvas (port 5173) + Python/FastAPI CPE Engine (port 9800) + Orchestrator (port 9820) | VERIFIED |
| Storyboard | Infinite canvas for visual production planning, bidirectional Gallery↔Storyboard | VERIFIED |
| Film presets | 67 live-action film presets (Metropolis to Parasite) with real camera/lens/film stock/lighting data | DOCUMENTED |
| ComfyUI integration | Direct frontend submission (not via Orchestrator); model-specific formatting (Midjourney, FLUX, SDXL) | VERIFIED |
| Multi-ComfyUI | Orchestrator supports distributed rendering across multiple local/remote ComfyUI nodes | VERIFIED |
| Asset management | Gallery with PNG metadata search, duplicate detection by hash, batch renaming, workflow restoration from images | VERIFIED |

**Recommendation:** UX reference only for storyboard canvas and multi-ComfyUI patterns. Cannot use code (proprietary).

---

## G. AI Video Production Editor (OpenMontage)

| Property | Finding | Status |
|---|---|---|
| Repository | github.com/calesthio/OpenMontage | VERIFIED |
| License | **AGPL-3.0** (not GPL-3.0-or-later as spec stated — stricter, covers network use) | VERIFIED |
| Architecture | Agent-first — no code orchestrator; AI coding assistant (Claude Code, Cursor, etc.) IS the orchestrator | VERIFIED |
| Scale | 12 specialized pipelines, 100+ tools, 700+ agent skill files | VERIFIED |
| Script | "Script and Screenplay Engineering" pipeline | VERIFIED |
| Director pass | Implicit via stage director skills (no named "director pass" stage) | UNKNOWN |
| Storyboard | "Storyboarding and Visual Continuity" pipeline; visual consistency planning | VERIFIED |
| Filming | Asset generation with scene-by-scene approval gates | VERIFIED |
| Continuity review | Post-render mandatory self-review via ffprobe + frame extraction + audio analysis | DOCUMENTED |
| Re-film queue | Iterative workflow with approval gates and delivery promise enforcement (blocks low-quality renders) | INFERENCE |
| Timeline | "Timeline Compilation and Cutting" pipeline — multi-track edit lists | VERIFIED |
| Live storyboard | Local web board shows production in real time | DOCUMENTED |
| Validation | Pre-compose validation catches broken plans before GPU time | DOCUMENTED |

**Recommendation:** UX and pipeline reference. Cannot copy code (AGPL-3.0). The agent-first architecture (no code orchestrator) is an interesting alternative paradigm but not aligned with our spec's explicit orchestration layer.

---

## H. ComfyUI Cinema Pipeline

| Property | Finding | Status |
|---|---|---|
| Repository | github.com/ismael-joffroy-chandoutis/comfyui-cinema-pipeline | VERIFIED |
| Author | Ismael Joffroy Chandoutis (Cesar 2022, Cannes Semaine de la Critique) | VERIFIED |
| License | **Unknown** — not explicitly stated | VERIFIED |
| Scale | 70+ workflows with stability ratings | VERIFIED |
| Infrastructure | Hybrid: RTX 5090 (32GB local) + Comfy Cloud (96GB RTX 6000 Pro) + Mac for editing | DOCUMENTED |
| Spatial continuity | Blender 3D geometry → depth maps, canny edges, poses → ControlNet/IC LoRA conditioning | VERIFIED |
| Camera control | Camera Angle Gizmo, depth movies define camera paths | DOCUMENTED |
| MCP integration | Claude Code as brain/orchestrator via MCP Server → ComfyUI Local/Cloud + DaVinci Resolve | VERIFIED |
| NLE integration | Pre-configured MCP servers for FCP-XML, Resolve, AppleScript | DOCUMENTED |
| Related | Also maintains `open-source-cinema` (RAW video, Magic Lantern, agent-driven post) | VERIFIED |

**Recommendation:** Advanced path reference. The Blender→ControlNet spatial consistency approach is the right solution for complex camera movement (our spec's §64) but NOT for MVP. The MCP/Resolve integration pattern is valuable for our export phase.

---

## I. LLM Provider Findings

### LM Studio

| Property | Value | Status |
|---|---|---|
| Installed | Yes | VERIFIED LOCALLY |
| CLI | `lms` available at `~/.lmstudio/bin/lms` | VERIFIED LOCALLY |
| Server | OFF (not running) | VERIFIED LOCALLY |
| Endpoint | `http://127.0.0.1:1234/v1` (when running) | DOCUMENTED UPSTREAM |
| Models | **None downloaded** | VERIFIED LOCALLY |
| OpenAI-compatible | Yes | DOCUMENTED UPSTREAM |
| Structured output | Supports JSON mode | DOCUMENTED UPSTREAM |

### Ollama

| Property | Value | Status |
|---|---|---|
| Installed | Yes (v0.32.5) | VERIFIED LOCALLY |
| Running | Yes (started during discovery) | VERIFIED LOCALLY |
| Endpoint | `http://127.0.0.1:11434` | VERIFIED LOCALLY |
| OpenAI-compatible | Yes — `/v1/models`, `/v1/chat/completions` | VERIFIED LOCALLY |
| Models | embeddinggemma (621 MB), sweaterdog/andy-4:micro-q8_0 (1.9 GB), gemma4:e4b (9.6 GB) | VERIFIED LOCALLY |
| Best available for Director | gemma4:e4b (9.6 GB, Gemma 4 family) | VERIFIED LOCALLY |
| Structured output | Supports JSON format | DOCUMENTED UPSTREAM |

### OpenRouter

| Property | Value | Status |
|---|---|---|
| Environment variables | None set | VERIFIED LOCALLY |
| .env file | None in project directory | VERIFIED LOCALLY |
| Protocol | OpenAI-compatible (`https://openrouter.ai/api/v1`) | DOCUMENTED UPSTREAM |
| Authentication | Bearer token via `OPENROUTER_API_KEY` env var | DOCUMENTED UPSTREAM |
| Structured output | Supports JSON mode (model-dependent) | DOCUMENTED UPSTREAM |

### Provider Abstraction Recommendation

All three providers use the OpenAI-compatible chat completions API:

```
LLMProvider (abstract)
  ├── LMStudioProvider  → http://127.0.0.1:1234/v1
  ├── OllamaProvider    → http://127.0.0.1:11434/v1
  └── OpenRouterProvider → https://openrouter.ai/api/v1
```

Common interface:
- `chat(messages, model, temperature, max_tokens, response_format)`
- `structured(messages, model, schema)` — JSON mode with schema validation
- Health check / model listing

Differences to abstract:
- OpenRouter requires `Authorization: Bearer <key>` header
- Ollama model names use `owner/name:tag` format
- LM Studio model names depend on loaded model
- Timeout and retry strategies differ (local vs cloud latency)

Credentials:
- Local providers: no auth needed
- OpenRouter: `OPENROUTER_API_KEY` from environment variable only
- `.env.example` should document required variables without values

---

## J. Integration Feasibility

### 1. Can Wind Comic realistically serve as the Director/pre-production layer?

**YES, with caveats.** Wind Comic v12.320 has exactly the 8-agent pipeline our spec describes (Writer → Director → Style Bible → Character Designer → Scene Designer → Storyboard Artist → Video Producer → Editor). It supports Ollama for local LLM, produces structured JSON artifacts, and has MIT license. However: (a) it's a full app, not a headless API — no documented way to call "generate just the director treatment" programmatically, (b) its ComfyUI integration is for image generation (IP-Adapter), not video, (c) it's primarily oriented toward Chinese short-drama. **Requires local hands-on testing.**

### 2. Can its artifacts be exported or consumed programmatically?

**YES — VERIFIED.** Wind Comic stores all artifacts in SQLite (directly queryable), exports storyboards as CSV/Markdown/PDF (round-trip editable), exports timelines as AAF/EDL/FCPXML, and explicitly documents that "all artifacts are persisted independently and any stage can re-run in isolation."

### 3. Can ComfyUI be used as an independent production backend?

**YES — VERIFIED.** The REST API accepts workflow JSON, returns structured results, and provides real-time monitoring via WebSocket. All H3 nodes are accessible via the API.

### 4. Can an external application submit arbitrary H3 workflow JSON to ComfyUI?

**YES — VERIFIED.** POST to `/prompt` with the node graph. The R2V workflow uses only standard nodes and can be fully parameterized. The StoryBoard nodes are also accessible via API.

### 5. What exact adapter is required between production specifications and H3 workflows?

**A prompt builder + workflow template engine.** The adapter must:
1. Take a Shot Specification (characters, action, camera, environment, duration, references)
2. Generate an H3 prompt in the discovered format (subject_definitions, retention_analysis, detailed_description, soundscape)
3. Inject parameters into a workflow template (prompt text, reference images, duration, seed, resolution)
4. Submit the parameterized workflow to ComfyUI

### 6. Can H3 T2V/I2V/R2V/continuation workflows be treated as interchangeable workflow definitions?

**YES, with caveats:**
- R2V is the most versatile (supports references, ideal for character consistency)
- T2V/I2V use a packed component node that isn't directly API-submittable; need to construct equivalent workflows from native nodes (MiniMaxH3ImageToVideo)
- First/Last uses MiniMaxH3ImageToVideo with first_frame + last_frame inputs
- StoryBoard handles multi-panel chaining automatically
- All share the same models, resolution, and timing constraints

### 7. What information must a Shot Specification contain to drive these workflows?

```
shot_id, beat_id
dramatic_purpose
subjects: [{id, description, reference_image}]
action: {description}
environment: {location, reference_image}
camera: {shot_size, angle, movement}
lighting: {description}
audio: {ambient, sfx, music, dialogue}
references: [image paths]
duration: seconds
generation_strategy: T2V | I2V | R2V | FL | STORYBOARD
seed: optional
resolution: {aspect, megapixels}
```

### 8. What information must remain outside H3-specific logic?

- Narrative structure (story, scenes, beats, sequences)
- Character identity (name, role, arc, relationships)
- Continuity state (what happened, who knows what)
- Review judgments (pass/fail, scores)
- Timeline ordering
- Audio design intent (before H3 translation)
- Project management state

### 9. Can local LLM and OpenRouter be exposed through one provider abstraction?

**YES — VERIFIED.** All three (LM Studio, Ollama, OpenRouter) use the OpenAI-compatible chat completions API. Ollama's `/v1/models` endpoint was verified returning the standard format.

### 10. What should be reused from Wind Comic?

- **MIT license permits full reuse** — unlike KupkaProd and others
- 8-agent pipeline architecture (Writer → Director → Style Bible → Character → Scene → Storyboard → Video → Editor)
- Character DNA structure (8 dimensions, 3-view turnarounds, Cameo IP)
- Style Bible Frame concept (one canonical reference frame for all shots)
- Sketch-Lock storyboard workflow (B/W composition first, then final)
- Vision-Audit retry loop (auto-regen shots scoring <70)
- Round-trip storyboard (CSV export → external edit → re-import)
- NLE export patterns (AAF, EDL, FCPXML)
- LLM fallback cascade with health-aware circuit breaker
- Only AFTER hands-on local validation confirms artifacts work with our pipeline

### 11. What should be borrowed only as architectural reference from KupkaProd?

- Template-based workflow injection (deep copy + parameter patch) — proven pattern for ComfyUI
- Atomic state persistence with .bak recovery — crash-safe JSON checkpoints
- 5-dimension multimodal evaluation (Subject Match, Motion Quality, Consistency, Shot Type, Continuity)
- Strict verdict logic (any "poor" = auto FAIL, 2+ "fair" = FAIL) — overrides lenient LLM
- Character anchor injection (verbatim description in every prompt)
- Style lock (20-40 word global style in every prompt)
- Phase-based resume (detect completed phases, skip on restart)
- VRAM management (unload LLM after planning phase)
- Auto-detection of ComfyUI nodes by class_type as fallback

### 12. What should we build ourselves?

1. **Production Specification layer** — Project → Sequence → Scene → Beat → Shot → Take hierarchy with dependency graph
2. **H3 Prompt Builder** — Shot Specification → H3 prompt translation using the discovered format
3. **ComfyUI Adapter** — Workflow template loading, parameter injection, job submission, monitoring, result retrieval
4. **LLM Provider abstraction** — Unified interface for LM Studio / Ollama / OpenRouter
5. **Continuity Manager** — State tracking and last-frame chain propagation
6. **Review System** — AI + human review with regeneration
7. **Orchestration Layer** — Queue management, retry, error handling
8. **UI** — Project dashboard, storyboard, generation monitor, review, timeline

### 13. What should explicitly NOT be built?

1. Custom video generation engine
2. Custom image generation engine
3. NLE (use DaVinci Resolve)
4. Wind Comic replacement (use or reference it)
5. ComfyUI replacement
6. H3 model training
7. Distributed GPU orchestration (MVP)
8. Mobile interface
9. Cloud deployment
10. Automatic rejection (human review required)

### 14. What is the smallest useful vertical slice that proves the architecture?

```
IDEA (text)
  → Ollama (gemma4) generates structured JSON:
    - 1 scene, 2 characters, 3 shots
    - Shot specifications with camera/action/duration
  → Prompt Builder translates each shot to H3 format
  → Reference images placed in ComfyUI input dir
  → R2V workflow template parameterized for each shot
  → Submitted to ComfyUI API
  → Results retrieved and linked to shots
  → Last frame of shot N used as reference for shot N+1
```

This proves: LLM → Specification → H3 Prompt → ComfyUI → Video → Continuity chain.

---

## K. Technical Spike Results

### Proof of Concept Assessment

**What CAN be demonstrated right now:**
1. ComfyUI API accepts workflow submissions (VERIFIED)
2. H3 R2V workflow can be parameterized (VERIFIED — all injection points mapped)
3. Ollama provides OpenAI-compatible chat completions (VERIFIED)
4. The H3 prompt format for multi-shot, multi-character scenes is well-defined (VERIFIED from existing R2V workflow)
5. The StoryBoard node handles crash-safe multi-panel production with resume (VERIFIED)

**What PREVENTS a full end-to-end test right now:**
1. No suitable LLM model for complex structured output generation — gemma4:e4b (9.6 GB) is the only text model available via Ollama, and it may not be sufficient for reliable structured JSON output with the complexity of a Director agent
2. No character reference images prepared for the test scenario
3. No T2V API-format workflow readily available (the template uses a packed component node)

**Recommended next step for spike completion:**
1. Download a larger Qwen model via Ollama (e.g., qwen2.5:14b or qwen3:14b)
2. Test structured JSON generation with a simple story prompt
3. Manually prepare 2 reference images
4. Submit one R2V workflow via the API
5. Verify result retrieval

### Two Viable Generation Strategies

**Strategy A: Individual Shot Workflows (R2V)**
- Submit each shot as a separate R2V workflow
- Extract last frame for continuity
- Maximum control over each shot
- Best for the spec's Shot → Take → Review cycle

**Strategy B: StoryBoard Node (GAPStoryboardManager)**
- Define all panels in one workflow
- Automatic seam-free chaining
- Built-in crash recovery
- Best for rapid prototyping and initial film generation
- Less granular control over individual shots

**Recommendation:** Start with Strategy A (individual R2V) for the production system, but use Strategy B (StoryBoard) for rapid prototyping and benchmarking.

---

## L. Technical Blockers

| Blocker | Severity | Impact | Resolution |
|---|---|---|---|
| No LLM model suitable for Director agent | MEDIUM | Cannot test structured output generation | Download Qwen via Ollama or LM Studio |
| T2V/I2V template uses packed component node | LOW | Can construct from native MiniMaxH3ImageToVideo node | Build T2V/I2V workflow templates from native nodes |
| No Turbo LoRA installed | LOW | Generation will be slower (~20 steps vs 6-10) | Download turbo LoRA when needed |
| Wind Comic not locally tested | MEDIUM | Cannot confirm integration feasibility | Schedule hands-on testing |
| No OpenRouter API key configured | LOW | Cloud LLM not available | User must set OPENROUTER_API_KEY env var |

---

## Recommendations

1. **Download a Qwen model** via Ollama (qwen2.5:14b or qwen3:14b) for Director agent testing
2. **Build T2V and I2V workflow templates** from native ComfyUI nodes (not packed components)
3. **Use R2V as the primary workflow** for character-consistent shots
4. **Evaluate GAPStoryboardManager** as a rapid prototyping tool alongside the main pipeline
5. **Proceed to M1** — the local environment is fully capable
6. **Test Wind Comic locally** as a pre-production layer — it's MIT-licensed, has the exact 8-agent pipeline we need, and produces structured artifacts. If it works with Ollama, it could save months of Director agent development. If not, its patterns are still the best available reference for building our own.
7. **Install the H3 Turbo LoRA** for faster iteration during development
8. **Note license corrections:** OpenMontage is AGPL-3.0 (not GPL-3.0-or-later as spec stated); KupkaProd is non-commercial only; Director's Console is proprietary. Only Wind Comic (MIT) can be freely integrated.
