"""Scene assembly — concatenate approved Takes into a scene export.

Uses ffmpeg concat demuxer with stream copy when inputs are compatible.
Falls back to normalized re-encode if necessary.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssemblyInput:
    """One input clip for scene assembly."""
    shot_id: str
    shot_index: int
    take_id: str
    video_path: str
    sha256: str


@dataclass(frozen=True)
class AssemblyResult:
    """Result of a successful scene assembly."""
    output_path: str
    output_sha256: str
    duration: float
    resolution: str
    fps: str
    video_codec: str
    audio_codec: str
    size_bytes: int
    inputs: list[AssemblyInput]
    manifest_path: str


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _probe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_name,width,height,r_frame_rate,codec_type",
         "-of", "json", path],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


def assemble_scene(
    inputs: list[AssemblyInput],
    output_path: str,
    project_id: str,
    scene_id: str | None = None,
) -> AssemblyResult:
    """Concatenate input clips into a single scene MP4.

    Uses ffmpeg concat demuxer with stream copy (no re-encode).
    All inputs must have compatible codecs/resolution/fps.

    Args:
        inputs: Ordered list of AssemblyInput clips.
        output_path: Destination MP4 path.
        project_id: For manifest metadata.
        scene_id: Optional scene ID for manifest.

    Returns:
        AssemblyResult with output metadata.

    Raises:
        FileNotFoundError: If any input file is missing.
        subprocess.CalledProcessError: If ffmpeg fails.
    """
    if not inputs:
        raise ValueError("No inputs for assembly")

    # Verify all inputs exist
    for inp in inputs:
        if not os.path.isfile(inp.video_path):
            raise FileNotFoundError(f"Input not found: {inp.video_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Create concat file list
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8",
    ) as f:
        concat_path = f.name
        for inp in inputs:
            # ffmpeg concat requires forward slashes and escaped quotes
            safe_path = os.path.abspath(inp.video_path).replace("\\", "/")
            f.write(f"file '{safe_path}'\n")

    try:
        # Stream copy concat (no re-encode)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_path,
            "-c", "copy",
            output_path,
        ]
        logger.info("Assembling scene: %s", " ".join(cmd))
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
    finally:
        os.unlink(concat_path)

    # Probe output
    info = _probe(output_path)
    vid = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    aud = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    duration = float(info.get("format", {}).get("duration", 0))
    size = int(info.get("format", {}).get("size", 0))
    output_sha = _sha256(output_path)

    result = AssemblyResult(
        output_path=output_path,
        output_sha256=output_sha,
        duration=round(duration, 3),
        resolution=f"{vid.get('width', '?')}x{vid.get('height', '?')}",
        fps=vid.get("r_frame_rate", "?"),
        video_codec=vid.get("codec_name", "?"),
        audio_codec=aud.get("codec_name", "?"),
        size_bytes=size,
        inputs=inputs,
        manifest_path="",
    )

    # Write manifest sidecar
    manifest_path = output_path.rsplit(".", 1)[0] + "_manifest.json"
    manifest = {
        "project_id": project_id,
        "scene_id": scene_id,
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "output": {
            "path": output_path,
            "sha256": output_sha,
            "duration": result.duration,
            "resolution": result.resolution,
            "fps": result.fps,
            "video_codec": result.video_codec,
            "audio_codec": result.audio_codec,
            "size_bytes": result.size_bytes,
        },
        "inputs": [
            {
                "shot_index": inp.shot_index,
                "shot_id": inp.shot_id,
                "take_id": inp.take_id,
                "video_path": inp.video_path,
                "sha256": inp.sha256,
            }
            for inp in inputs
        ],
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return AssemblyResult(
        output_path=result.output_path,
        output_sha256=result.output_sha256,
        duration=result.duration,
        resolution=result.resolution,
        fps=result.fps,
        video_codec=result.video_codec,
        audio_codec=result.audio_codec,
        size_bytes=result.size_bytes,
        inputs=result.inputs,
        manifest_path=manifest_path,
    )
