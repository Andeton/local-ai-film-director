"""Tests for P2 scene assembly logic."""
import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from film_director.services.scene_assembly import AssemblyInput, assemble_scene


def _make_test_video(path, duration=1.0):
    """Create a minimal valid MP4 for testing."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"color=c=black:s=320x180:d={duration}",
        "-f", "lavfi", "-i", f"anullsrc=r=32000:cl=stereo",
        "-t", str(duration),
        "-c:v", "libx264", "-c:a", "aac",
        "-shortest", path,
    ], capture_output=True, check=True, timeout=30)


class TestAssembleScene:
    def test_concat_two_clips(self, tmp_path):
        v1 = str(tmp_path / "s1.mp4")
        v2 = str(tmp_path / "s2.mp4")
        _make_test_video(v1, 1.0)
        _make_test_video(v2, 1.0)

        inputs = [
            AssemblyInput(shot_id="s1", shot_index=0, take_id="t1",
                         video_path=v1, sha256="a" * 64),
            AssemblyInput(shot_id="s2", shot_index=1, take_id="t2",
                         video_path=v2, sha256="b" * 64),
        ]
        out = str(tmp_path / "out.mp4")
        result = assemble_scene(inputs, out, "proj1")
        assert os.path.isfile(result.output_path)
        assert result.duration > 1.5  # ~2s total
        assert len(result.output_sha256) == 64
        assert len(result.inputs) == 2

    def test_manifest_written(self, tmp_path):
        v1 = str(tmp_path / "s1.mp4")
        _make_test_video(v1, 0.5)
        inputs = [AssemblyInput("s1", 0, "t1", v1, "a" * 64)]
        out = str(tmp_path / "out.mp4")
        result = assemble_scene(inputs, out, "proj1")
        assert os.path.isfile(result.manifest_path)
        with open(result.manifest_path) as f:
            manifest = json.load(f)
        assert manifest["project_id"] == "proj1"
        assert len(manifest["inputs"]) == 1

    def test_missing_input_raises(self, tmp_path):
        inputs = [AssemblyInput("s1", 0, "t1", str(tmp_path / "missing.mp4"), "a" * 64)]
        with pytest.raises(FileNotFoundError):
            assemble_scene(inputs, str(tmp_path / "out.mp4"), "proj1")

    def test_empty_inputs_raises(self, tmp_path):
        with pytest.raises(ValueError):
            assemble_scene([], str(tmp_path / "out.mp4"), "proj1")

    def test_order_preserved(self, tmp_path):
        v1 = str(tmp_path / "s1.mp4")
        v2 = str(tmp_path / "s2.mp4")
        _make_test_video(v1, 0.5)
        _make_test_video(v2, 0.5)
        inputs = [
            AssemblyInput("s2", 1, "t2", v2, "b" * 64),
            AssemblyInput("s1", 0, "t1", v1, "a" * 64),
        ]
        out = str(tmp_path / "out.mp4")
        result = assemble_scene(inputs, out, "proj1")
        # Inputs preserved in given order
        assert result.inputs[0].shot_id == "s2"
        assert result.inputs[1].shot_id == "s1"
