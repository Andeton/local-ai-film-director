"""Tests for media_utils — staging, verification, FFmpeg, finalization."""
import os
import subprocess

import pytest

from film_director.errors import MediaProcessingError
from film_director.generation.media_utils import (
    cleanup_dir,
    create_staging_dir,
    extract_last_frame,
    make_final_dir,
    move_to_final,
    sanitize_filename,
    verify_media,
)


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_normal_filename(self):
        assert sanitize_filename("video.mp4") == "video.mp4"

    def test_path_with_directory(self):
        assert sanitize_filename("video/subfolder/file.mp4") == "file.mp4"

    def test_traversal_rejected(self):
        with pytest.raises(MediaProcessingError, match="(?i)unsafe"):
            sanitize_filename("../../etc/passwd")

    def test_empty_rejected(self):
        with pytest.raises(MediaProcessingError, match="(?i)unsafe"):
            sanitize_filename("")

    def test_backslash_path_extracts_base(self):
        result = sanitize_filename("subfolder\\file.mp4")
        assert result == "file.mp4"


# ---------------------------------------------------------------------------
# create_staging_dir
# ---------------------------------------------------------------------------

class TestCreateStagingDir:
    def test_creates_directory(self, tmp_path):
        staging = create_staging_dir(str(tmp_path), "req-123")
        assert os.path.isdir(staging)
        assert "req-123" in staging
        assert ".staging" in staging


# ---------------------------------------------------------------------------
# make_final_dir
# ---------------------------------------------------------------------------

class TestMakeFinalDir:
    def test_creates_final_directory(self, tmp_path):
        final = make_final_dir(str(tmp_path), "proj-1", "shot-1", 1)
        assert os.path.isdir(final)
        assert "take_1" in final

    def test_existing_directory_raises(self, tmp_path):
        make_final_dir(str(tmp_path), "proj-1", "shot-1", 1)
        with pytest.raises(MediaProcessingError, match="(?i)already exists"):
            make_final_dir(str(tmp_path), "proj-1", "shot-1", 1)


# ---------------------------------------------------------------------------
# verify_media — using real FFmpeg
# ---------------------------------------------------------------------------

class TestVerifyMedia:
    def _create_synthetic_video(self, tmp_path, name="test.mp4"):
        """Create a tiny synthetic video with FFmpeg."""
        path = os.path.join(str(tmp_path), name)
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=red:s=64x64:d=0.5:r=24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            pytest.skip("FFmpeg not available or cannot create synthetic video")
        return path

    def test_valid_video(self, tmp_path):
        path = self._create_synthetic_video(tmp_path)
        info = verify_media(path)
        assert "streams" in info
        video_streams = [s for s in info["streams"] if s["codec_type"] == "video"]
        assert len(video_streams) >= 1

    def test_missing_file_raises(self):
        with pytest.raises(MediaProcessingError, match="(?i)not found"):
            verify_media("/nonexistent/video.mp4")

    def test_empty_file_raises(self, tmp_path):
        empty = os.path.join(str(tmp_path), "empty.mp4")
        with open(empty, "wb") as f:
            pass
        with pytest.raises(MediaProcessingError, match="(?i)empty"):
            verify_media(empty)

    def test_non_video_file_raises(self, tmp_path):
        txt = os.path.join(str(tmp_path), "text.txt")
        with open(txt, "w") as f:
            f.write("not a video")
        with pytest.raises(MediaProcessingError):
            verify_media(txt)


# ---------------------------------------------------------------------------
# extract_last_frame — using real FFmpeg
# ---------------------------------------------------------------------------

class TestExtractLastFrame:
    def _create_synthetic_video(self, tmp_path, name="test.mp4"):
        path = os.path.join(str(tmp_path), name)
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=blue:s=64x64:d=0.5:r=24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            pytest.skip("FFmpeg not available")
        return path

    def test_extraction_succeeds(self, tmp_path):
        video = self._create_synthetic_video(tmp_path)
        output = os.path.join(str(tmp_path), "last.png")
        result = extract_last_frame(video, output)
        assert result == output
        assert os.path.isfile(output)
        assert os.path.getsize(output) > 0

    def test_missing_video_raises(self, tmp_path):
        output = os.path.join(str(tmp_path), "last.png")
        with pytest.raises(MediaProcessingError):
            extract_last_frame("/nonexistent.mp4", output)

    def test_extracts_true_final_frame(self, tmp_path):
        """BLOCKING: Prove extracted frame is the TRUE last frame.

        Creates a 1-second video where the first 0.5s is red and the last
        0.5s is blue. Extracts last frame and verifies it is blue, not red.
        """
        video = os.path.join(str(tmp_path), "bicolor.mp4")
        # Use filter_complex to generate red→blue color change
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=64x64:r=24:d=0.5",
            "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24:d=0.5",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[out]",
            "-map", "[out]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            video,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            pytest.skip("FFmpeg not available or concat failed")

        output = os.path.join(str(tmp_path), "last.png")
        extract_last_frame(video, output)

        # Verify the last frame is blue by checking average color with ffprobe
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-f", "lavfi", "-i", f"movie={output},signalstats",
            "-show_entries", "frame_tags=lavfi.signalstats.HUEMED",
            "-print_format", "json",
        ]
        # Alternative: use ffmpeg to get pixel value
        check_cmd = [
            "ffmpeg", "-i", output,
            "-vf", "crop=1:1:32:32,format=rgb24",
            "-f", "rawvideo", "-frames:v", "1",
            "-"
        ]
        check = subprocess.run(check_cmd, capture_output=True, timeout=10)
        if check.returncode != 0:
            pytest.skip("Cannot verify pixel color")

        rgb = check.stdout[:3]
        r, g, b = rgb[0], rgb[1], rgb[2]
        # Blue frame: R should be low, B should be high
        # Red frame: R should be high, B should be low
        # H.264 encoding shifts values slightly, so use generous thresholds
        assert b > r, f"Expected blue frame (b>r), got R={r} G={g} B={b}"


# ---------------------------------------------------------------------------
# move_to_final
# ---------------------------------------------------------------------------

class TestMoveToFinal:
    def test_moves_files(self, tmp_path):
        staging = os.path.join(str(tmp_path), "staging")
        final = os.path.join(str(tmp_path), "final")
        os.makedirs(staging)
        os.makedirs(final)
        with open(os.path.join(staging, "video.mp4"), "wb") as f:
            f.write(b"video data")
        with open(os.path.join(staging, "last.png"), "wb") as f:
            f.write(b"png data")
        move_to_final(staging, final)
        assert os.path.isfile(os.path.join(final, "video.mp4"))
        assert os.path.isfile(os.path.join(final, "last.png"))


# ---------------------------------------------------------------------------
# cleanup_dir
# ---------------------------------------------------------------------------

class TestCleanupDir:
    def test_removes_directory(self, tmp_path):
        target = os.path.join(str(tmp_path), "to_clean")
        os.makedirs(target)
        with open(os.path.join(target, "file.txt"), "w") as f:
            f.write("data")
        cleanup_dir(target)
        assert not os.path.exists(target)

    def test_nonexistent_does_not_raise(self, tmp_path):
        cleanup_dir(os.path.join(str(tmp_path), "nonexistent"))
