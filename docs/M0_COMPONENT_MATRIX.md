# M0 Component Matrix

**Date:** 2026-08-14

## Core Components

| Component | Purpose | Current Version | Location | Local/Remote | Input | Output | Integration Method | Status | Risk | License | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ComfyUI | Workflow execution engine | 0.32.0 | D:\ComfyUI\TD_1\ComfyUI | Local | Workflow JSON via REST API | Video/image/audio files | REST API (POST /prompt) + WebSocket monitoring | Running, verified | LOW — stable, actively maintained | GPL-3.0 | Use as primary generation backend |
| MiniMax H3 fl2va | First/Last frame to Video+Audio diffusion model | int8 pruned | models/diffusion_models/ | Local (20 GB) | Latent + conditioning | AV latent | Via ComfyUI nodes | Installed, verified | LOW | Research/community license | Use for I2V, T2V, First-Last workflows |
| MiniMax H3 ref2va | Reference to Video+Audio diffusion model | int8 pruned | models/diffusion_models/ | Local (20 GB) | Latent + conditioning + refs | AV latent | Via ComfyUI nodes | Installed, verified | LOW | Research/community license | Use for R2V (primary workflow) |
| Qwen3-VL 32B | H3 text encoder | nvfp4 AWQ | models/text_encoders/ | Local (15 GB) | Text + images | Token embeddings | Via ComfyUI CLIPLoader | Installed, verified | LOW | Apache 2.0 | Required for all H3 generation |
| H3 Video VAE | Video decoder | fp16 | models/vae/ | Local (4.9 GB) | Video latent | Video frames | Via ComfyUI VAEDecode | Installed, verified | LOW | Part of H3 | Required |
| H3 Audio VAE | Audio decoder | fp32 | models/vae/ | Local (578 MB) | Audio latent | Audio waveform | Via ComfyUI VAEDecodeAudio | Installed, verified | LOW | Part of H3 | Required |
| ComfyUI-MiniMax-H3-StoryBoard | Multi-panel storyboard-to-movie | Latest | custom_nodes/ | Local | Panel images + prompts | Continuous video with audio | Via ComfyUI API (GAPStoryboard nodes) | Installed, verified | LOW | Check license | Use for rapid prototyping; evaluate for production |
| Ollama | Local LLM runtime | 0.32.5 | System install | Local | Chat messages | Text/JSON responses | OpenAI-compatible API (/v1) | Running, verified | LOW | MIT | Primary local LLM provider |
| gemma4:e4b | General-purpose LLM | e4b (9.6 GB) | Ollama | Local | Chat messages | Text/JSON | Via Ollama /v1/chat/completions | Available | MEDIUM — may be too small for Director | Gemma license | Test; likely need larger model |
| LM Studio | Local LLM runtime (alternative) | CLI available | System install | Local | Chat messages | Text/JSON responses | OpenAI-compatible API (/v1) | Installed, no models | LOW | Proprietary (free) | Secondary provider; download models as needed |
| OpenRouter | Cloud LLM gateway | N/A | Remote | Remote | Chat messages | Text/JSON responses | OpenAI-compatible API | No API key set | MEDIUM — requires paid API key | N/A (service) | Configure via OPENROUTER_API_KEY env var |
| FFmpeg | Media processing | 8.0.1 | System install | Local | Video/audio files | Processed media | CLI / subprocess | Available, verified | LOW | LGPL/GPL | Use for post-processing, timeline assembly, export |
| Docker | Container runtime | 29.6.1 | System install | Local | Dockerfiles | Containers | Docker API / CLI | Available | LOW | Apache 2.0 | Optional; available if needed |
| Python | Primary backend language | 3.14.3 | System install | Local | Source code | Runtime | Direct execution | Available | LOW — very new version | PSF | Use for backend (FastAPI) |
| Node.js | Frontend runtime | 24.14.0 | System install | Local | Source code | Runtime | Direct execution | Available | LOW | MIT | Use for frontend (Next.js/React) |
| pnpm | Package manager | 9.15.4 | System install | Local | package.json | node_modules | CLI | Available | LOW | MIT | Use for frontend dependencies |

## Reference Projects (not dependencies)

| Component | Purpose | Current Version | Location | Local/Remote | Input | Output | Integration Method | Status | Risk | License | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Wind Comic | AI story/storyboard/director pipeline | v12.320 (2026-08-11) | github.com/ChrisChen667788/wind-comic | Remote (not installed) | Idea/prompt | Story JSON, Character DNA, Storyboard PNG+JSON, NLE exports (AAF/EDL/FCPXML) | Run alongside + consume artifacts; or extract agent patterns | NOT TESTED LOCALLY | MEDIUM — needs local validation | **MIT** | Test locally; strong candidate for pre-production layer |
| KupkaProd | Cinema pipeline architecture reference | No releases (2026-07-19) | github.com/Matticusnicholas/KupkaProd-Cinema-Pipeline | Remote (not installed) | Script/prompt | Video takes, storyboard, state.json | Architecture reference only — do NOT depend | NOT TESTED | N/A (reference only) | **Non-commercial only; commercial requires license** | Reference only; reimplement patterns independently |
| Director's Console | Storyboard canvas + multi-ComfyUI | Active | github.com/NickPittas/DirectorsConsole | Remote (not installed) | N/A | N/A | UX reference only | NOT TESTED | N/A | **Proprietary / All rights reserved** | UX reference only; cannot use code |
| OpenMontage (AI Video Production Editor) | Agent-first production pipeline | Active | github.com/calesthio/OpenMontage | Remote (not installed) | N/A | N/A | Pipeline concept reference | NOT TESTED | N/A | **AGPL-3.0** (not GPL-3.0 as spec stated) | Pipeline reference; cannot copy code |
| ComfyUI Cinema Pipeline | Cinema-grade ComfyUI orchestration | Active | github.com/ismael-joffroy-chandoutis/comfyui-cinema-pipeline | Remote (not installed) | N/A | N/A | Architecture reference for advanced phases | NOT TESTED | N/A | **Unknown** | Advanced path reference; Blender/ControlNet not for MVP |
| DaVinci Resolve | NLE for final editing | Not detected | N/A | Local | Timeline + clips | Final film | EDL/OTIO/MP4 export from our system | NOT INSTALLED | LOW — optional for MVP | Proprietary (free version available) | Install when needed for export testing |

## VRAM Budget Estimate

| Operation | Est. VRAM | Source |
|---|---|---|
| H3 ref2va model load | ~20 GB | INFERENCE from model size |
| Qwen3-VL 32B encoder | ~8-10 GB | INFERENCE from NVF4 quantization |
| Video VAE | ~5 GB | INFERENCE from fp16 size |
| Audio VAE | <1 GB | VERIFIED from file size |
| Total peak (R2V generation) | ~28-30 GB | INFERENCE |
| Available VRAM | 32 GB | VERIFIED LOCALLY |
| Headroom | ~2-4 GB | INFERENCE |

**Note:** ComfyUI is running with `--highvram` flag, which keeps models in VRAM. The RTX 5090's 32 GB should handle all H3 operations with tight but sufficient headroom.
