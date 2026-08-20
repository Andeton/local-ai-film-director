# Source Reference Workflows

Byte-identical copies of locally verified working ComfyUI workflows.

## Purpose

These files are **evidence fixtures** — they document the exact node topology,
wiring, and parameters of real working ComfyUI workflows as found on the
development machine.

## Rules

- Originals live outside this repository (in the local ComfyUI installation)
- Originals must NEVER be modified by LFDirector development
- These copies must remain byte-identical to their originals
- These are NOT runtime templates — LFDirector's API-format templates in
  `workflows/h3/` are derived from these but adapted for programmatic use
- Future workflow changes must compare against these source fixtures

## Files

### minimax_h3/video_minimax_h3_r2v.json

- **Original**: `D:\ComfyUI\TD_1\ComfyUI\user\default\workflows\video_minimax_h3_r2v.json`
- **Origin**: User-saved working MiniMax H3 Reference-to-Video workflow
- **Format**: ComfyUI UI graph JSON (NOT API format)
- **Key node**: MiniMaxH3ReferenceToVideo (node 136)
- **SHA-256**: `b6224a53c92f819c33cf1e96df95bb9946c0b792c4f01f2b5037ea18fb8f9d9e`

### minimax_h3/video_minimax_h3_i2v.json

- **Original**: `D:\ComfyUI\TD_1\ComfyUI\user\default\workflows\video_minimax_h3_i2v.json`
- **Origin**: User-saved working MiniMax H3 Image-to-Video workflow
- **Format**: ComfyUI UI graph JSON (NOT API format)
- **Key node**: MiniMaxH3ImageToVideo
- **SHA-256**: `b9f11d8249edb2cee0b4e2e270ac8a58ccde282eaa41d465bda07ac8f7386305`
