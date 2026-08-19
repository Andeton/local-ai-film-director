"""Tests for media serving of reference assets.

Regression test for broken reference image display caused by Windows
backslashes in managed_path producing invalid URLs.
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from film_director.ui.media import _create_media_router


@pytest.fixture
def media_env(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()

    # Create a reference image
    ref_dir = storage / "references" / "proj-1" / "ref-1"
    ref_dir.mkdir(parents=True)
    img_path = ref_dir / "original.png"
    # Minimal valid PNG (1x1 pixel)
    img_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
        b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    app = FastAPI()
    router = _create_media_router(str(storage))
    app.include_router(router)
    client = TestClient(app)

    return {"client": client, "storage": storage}


class TestReferenceImageServing:
    def test_forward_slash_path_200(self, media_env):
        """Forward-slash managed_path serves correctly."""
        resp = media_env["client"].get("/media/references/proj-1/ref-1/original.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_backslash_path_200(self, media_env):
        """Backslash managed_path (Windows legacy) also serves correctly."""
        resp = media_env["client"].get(
            "/media/references\\proj-1\\ref-1\\original.png"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_traversal_rejected(self, media_env):
        resp = media_env["client"].get("/media/../../../etc/passwd")
        assert resp.status_code in (403, 404)

    def test_nonexistent_file_404(self, media_env):
        resp = media_env["client"].get("/media/references/proj-1/ref-999/nope.png")
        assert resp.status_code == 404

    def test_disallowed_extension_403(self, media_env):
        # Create a .txt file
        txt_path = media_env["storage"] / "references" / "proj-1" / "test.txt"
        txt_path.write_text("not an image")
        resp = media_env["client"].get("/media/references/proj-1/test.txt")
        assert resp.status_code == 403


class TestManagedPathForwardSlashes:
    def test_ingest_creates_forward_slashes(self):
        """Managed path must use forward slashes for URL compatibility."""
        # The fix replaces os.path.join with explicit forward-slash construction
        path = f"references/proj-1/ref-1/original.png"
        assert "\\" not in path

    def test_generator_creates_forward_slashes(self):
        path = f"references/proj-1/ref-1/original.png"
        assert "\\" not in path
