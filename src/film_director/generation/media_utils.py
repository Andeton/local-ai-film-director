"""Media staging, verification, FFmpeg last-frame extraction, and finalization.

Owns the filesystem lifecycle for generated media. Does NOT contain
generation orchestration, ComfyUI communication, or persistence logic.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from film_director.errors import MediaProcessingError

logger = logging.getLogger(__name__)

_FFPROBE_TIMEOUT = 30
_FFMPEG_TIMEOUT = 60


def sanitize_filename(name: str) -> str:
    """Extract safe basename — reject traversal or absolute paths."""
    if not name or '..' in name or os.path.isabs(name):
        raise MediaProcessingError(
            f"Unsafe filename: {name!r}",
        )
    base = os.path.basename(name)
    if not base:
        raise MediaProcessingError(
            f"Unsafe filename: {name!r}",
        )
    return base


def create_staging_dir(storage_root: str, request_id: str) -> str:
    staging = os.path.join(storage_root, ".staging", request_id)
    os.makedirs(staging, exist_ok=True)
    return staging


def make_final_dir(storage_root: str, project_id: str, shot_id: str, take_number: int) -> str:
    final = os.path.join(storage_root, "takes", project_id, shot_id, f"take_{take_number}")
    if os.path.exists(final):
        raise MediaProcessingError(
            f"Final Take directory already exists: {final}",
            detail=f"path={final}",
        )
    os.makedirs(final, exist_ok=True)
    return final


def verify_media(path: str) -> dict:
    """Verify media file with ffprobe. Returns basic stream info."""
    if not os.path.isfile(path):
        raise MediaProcessingError(f"Media file not found: {path}")
    if os.path.getsize(path) == 0:
        raise MediaProcessingError(f"Media file is empty: {path}")

    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_FFPROBE_TIMEOUT, shell=False,
        )
    except FileNotFoundError:
        raise MediaProcessingError("ffprobe not found — FFmpeg must be installed")
    except subprocess.TimeoutExpired:
        raise MediaProcessingError(f"ffprobe timed out on {path}")

    if result.returncode != 0:
        raise MediaProcessingError(
            f"ffprobe failed on {path}",
            detail=result.stderr[:500] if result.stderr else None,
        )

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise MediaProcessingError(f"ffprobe output not valid JSON: {e}")

    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        raise MediaProcessingError(f"No video stream found in {path}")

    return info


def extract_last_frame(video_path: str, output_path: str) -> str:
    """Extract the last video frame as PNG using FFmpeg.

    Uses -sseof to seek near the end, then extract one frame.
    M3.A validated: ffmpeg -sseof -0.04 -i video.mp4 -frames:v 1 lastframe.png
    Uses -0.5s offset for robustness with variable GOP sizes.
    """
    cmd = [
        "ffmpeg", "-y",
        "-sseof", "-0.5",
        "-i", video_path,
        "-frames:v", "1",
        "-update", "1",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_FFMPEG_TIMEOUT, shell=False,
        )
    except FileNotFoundError:
        raise MediaProcessingError("ffmpeg not found — FFmpeg must be installed")
    except subprocess.TimeoutExpired:
        raise MediaProcessingError("FFmpeg last-frame extraction timed out")

    if result.returncode != 0:
        raise MediaProcessingError(
            "FFmpeg last-frame extraction failed",
            detail=result.stderr[:500] if result.stderr else None,
        )

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise MediaProcessingError(
            f"Last frame output missing or empty: {output_path}",
        )

    return output_path


def move_to_final(staging_dir: str, final_dir: str) -> None:
    """Move all files from staging to final directory."""
    for name in os.listdir(staging_dir):
        src = os.path.join(staging_dir, name)
        dst = os.path.join(final_dir, name)
        if os.path.isfile(src):
            shutil.move(src, dst)


def cleanup_dir(path: str) -> None:
    """Safely remove a directory tree. Logs but does not raise on failure."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            logger.debug("Cleaned up: %s", path)
    except OSError as e:
        logger.warning("Cleanup failed for %s: %s", path, e)
