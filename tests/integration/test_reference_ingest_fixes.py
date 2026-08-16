"""M5.B fix tests — HTTP WC ingest, path confinement hardening, repository dedup.

Extends test_reference_ingest.py with HTTP transport, path traversal attacks,
and dedicated repository dedup query tests.
"""
from __future__ import annotations

import hashlib
import io
import os

import httpx
import pytest
from PIL import Image

from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import ReferenceAssetRepository
from film_director.services.reference_service import (
    IngestOutcome,
    ReferenceIngestService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_image_bytes(fmt="PNG", width=128, height=192) -> bytes:
    img = Image.new("RGB", (width, height), color=(50, 100, 150))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def _create_test_image(path, fmt="PNG", width=128, height=192):
    data = _create_test_image_bytes(fmt, width, height)
    with open(path, "wb") as f:
        f.write(data)
    return path, data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seed_project(db, project_id="proj-001"):
    with db.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO production_projects "
            "(id, wc_project_id, title, status, aspect, director_context, "
            "created_at, updated_at, prov_source_system, prov_source_project_id, "
            "prov_source_asset_id, prov_source_asset_version, prov_imported_at, prov_source_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, f"wc-{project_id}", "Test", "active", "16:9", "{}",
             "t", "t", "wind_comic", project_id, project_id, 1, "t", "a" * 64),
        )


def _mock_transport(handlers):
    def handler(request):
        url = str(request.url)
        for key, fn in handlers.items():
            if key in url:
                return fn(request)
        return httpx.Response(404, text="not found")
    return httpx.MockTransport(handler)


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    _seed_project(db, "proj-001")
    _seed_project(db, "proj-002")
    repo = ReferenceAssetRepository(db)
    storage_root = str(tmp_path / "storage")
    svc = ReferenceIngestService(
        repo=repo, storage_root=storage_root,
        windcomic_base_url="http://wc.local:3000",
    )
    return dict(db=db, repo=repo, svc=svc, tmp_path=tmp_path, storage_root=storage_root)


def _with_mock(env, handlers):
    """Set mock HTTP client on the service."""
    transport = _mock_transport(handlers)
    env["svc"]._http_client = httpx.Client(transport=transport)


# ===========================================================================
# HTTP WC INGEST
# ===========================================================================

class TestHTTPWCIngest:
    def test_http_png_imported(self, env):
        img_bytes = _create_test_image_bytes("PNG")
        _with_mock(env, {
            "/media/char.png": lambda r: httpx.Response(200, content=img_bytes),
        })
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/media/char.png",
            source_provenance="wc-http",
        )
        assert result.outcome == IngestOutcome.IMPORTED
        assert result.asset.source == ReferenceSource.WIND_COMIC
        assert result.asset.content_sha256 == _sha256(img_bytes)

    def test_https_jpeg_imported(self, env):
        img_bytes = _create_test_image_bytes("JPEG")
        _with_mock(env, {
            "/media/body.jpg": lambda r: httpx.Response(200, content=img_bytes),
        })
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_BODY,
            media_url="https://wc.local:3000/media/body.jpg",
            source_provenance="wc-https",
        )
        assert result.outcome == IngestOutcome.IMPORTED

    def test_relative_url_resolved(self, env):
        img_bytes = _create_test_image_bytes("PNG")
        _with_mock(env, {
            "wc.local:3000/uploads/ref.png": lambda r: httpx.Response(200, content=img_bytes),
        })
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="/uploads/ref.png",
            source_provenance="wc-rel",
        )
        assert result.outcome == IngestOutcome.IMPORTED

    def test_404_download_failed(self, env):
        _with_mock(env, {"/missing": lambda r: httpx.Response(404)})
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/missing",
            source_provenance="wc-404",
        )
        assert result.outcome == IngestOutcome.DOWNLOAD_FAILED

    def test_500_download_failed(self, env):
        _with_mock(env, {"/error": lambda r: httpx.Response(500)})
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/error",
            source_provenance="wc-500",
        )
        assert result.outcome == IngestOutcome.DOWNLOAD_FAILED

    def test_timeout_download_failed(self, env):
        _with_mock(env, {"/slow": lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("timeout"))})
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/slow",
            source_provenance="wc-timeout",
        )
        assert result.outcome == IngestOutcome.DOWNLOAD_FAILED

    def test_connection_failure_download_failed(self, env):
        _with_mock(env, {"/down": lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))})
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/down",
            source_provenance="wc-conn",
        )
        assert result.outcome == IngestOutcome.DOWNLOAD_FAILED

    def test_html_response_invalid_image(self, env):
        _with_mock(env, {"/page": lambda r: httpx.Response(200, content=b"<html>not image</html>")})
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/page",
            source_provenance="wc-html",
        )
        assert result.outcome == IngestOutcome.INVALID_IMAGE

    def test_unsupported_scheme_failed(self, env):
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="ftp://evil.com/image.png",
            source_provenance="wc-ftp",
        )
        assert result.outcome == IngestOutcome.DOWNLOAD_FAILED

    def test_downloaded_bytes_match_managed(self, env):
        img_bytes = _create_test_image_bytes("PNG")
        _with_mock(env, {"/ref.png": lambda r: httpx.Response(200, content=img_bytes)})
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/ref.png",
            source_provenance="wc-sha",
        )
        managed = os.path.join(env["storage_root"], result.asset.managed_path)
        with open(managed, "rb") as f:
            managed_bytes = f.read()
        assert managed_bytes == img_bytes
        assert result.asset.content_sha256 == _sha256(img_bytes)

    def test_duplicate_http_download(self, env):
        img_bytes = _create_test_image_bytes("PNG")
        _with_mock(env, {"/dup.png": lambda r: httpx.Response(200, content=img_bytes)})
        r1 = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/dup.png",
            source_provenance="wc-dup1",
        )
        r2 = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="http://wc.local:3000/dup.png",
            source_provenance="wc-dup2",
        )
        assert r1.outcome == IngestOutcome.IMPORTED
        assert r2.outcome == IngestOutcome.DUPLICATE


# ===========================================================================
# PATH CONFINEMENT
# ===========================================================================

class TestPathConfinement:
    def test_deep_traversal_rejected(self, env):
        """A project_id that would escape storage root is rejected."""
        src, _ = _create_test_image(str(env["tmp_path"] / "escape.png"))
        # ../../.. escapes from storage/references/PROJECT_ID/ to above storage root
        result = env["svc"].register_user_reference(
            project_id="../../..", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.outcome == IngestOutcome.INVALID_IMAGE
        assert "confinement" in (result.detail or "").lower()

    def test_valid_nested_path_accepted(self, env):
        src, _ = _create_test_image(str(env["tmp_path"] / "ok.png"))
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.outcome == IngestOutcome.IMPORTED

    def test_path_confinement_function(self):
        """Direct test of _is_path_confined."""
        from film_director.services.reference_service import _is_path_confined
        import tempfile
        root = tempfile.mkdtemp()
        # Inside root: OK
        assert _is_path_confined(os.path.join(root, "sub", "file.png"), root) is True
        # Above root: BLOCKED
        assert _is_path_confined(os.path.join(root, "..", "file.png"), root) is False
        # Root itself: OK (is_relative_to includes self)
        assert _is_path_confined(root, root) is True


# ===========================================================================
# REPOSITORY DEDUP QUERY
# ===========================================================================

class TestRepositoryDedupQuery:
    @pytest.fixture
    def repo(self, tmp_path):
        db = Database(str(tmp_path / "dedup.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        _seed_project(db, "proj-002")
        return ReferenceAssetRepository(db)

    def _save_asset(self, repo, **kw):
        base = dict(
            id=f"ref-{os.urandom(4).hex()}", project_id="proj-001",
            character_id="char-001", shot_id=None,
            kind=ReferenceKind.CHARACTER_FACE, source=ReferenceSource.USER_UPLOAD,
            managed_path="test/path.png", content_sha256="a" * 64,
            source_provenance="test", source_fingerprint=None,
            status=ReferenceStatus.CANDIDATE, source_state=ReferenceSourceState.CURRENT,
            pinned=False, width=100, height=100, created_at="t", updated_at="t",
        )
        base.update(kw)
        asset = ReferenceAsset(**base)
        repo.save(asset)
        return asset

    def test_find_exact_match(self, repo):
        asset = self._save_asset(repo, content_sha256="b" * 64)
        found = repo.find_duplicate(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, content_sha256="b" * 64,
        )
        assert found is not None
        assert found.id == asset.id

    def test_different_character_no_match(self, repo):
        self._save_asset(repo, character_id="char-001", content_sha256="c" * 64)
        found = repo.find_duplicate(
            project_id="proj-001", character_id="char-002",
            kind=ReferenceKind.CHARACTER_FACE, content_sha256="c" * 64,
        )
        assert found is None

    def test_different_kind_no_match(self, repo):
        self._save_asset(repo, kind=ReferenceKind.CHARACTER_FACE, content_sha256="d" * 64)
        found = repo.find_duplicate(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_BODY, content_sha256="d" * 64,
        )
        assert found is None

    def test_different_project_no_match(self, repo):
        self._save_asset(repo, project_id="proj-001", content_sha256="e" * 64)
        found = repo.find_duplicate(
            project_id="proj-002", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, content_sha256="e" * 64,
        )
        assert found is None

    def test_storyboard_match(self, repo):
        asset = self._save_asset(
            repo, kind=ReferenceKind.STORYBOARD, character_id=None,
            shot_id="shot-001", content_sha256="f" * 64,
        )
        found = repo.find_duplicate(
            project_id="proj-001", shot_id="shot-001",
            kind=ReferenceKind.STORYBOARD, content_sha256="f" * 64,
        )
        assert found is not None
        assert found.id == asset.id

    def test_different_shot_no_match(self, repo):
        self._save_asset(
            repo, kind=ReferenceKind.STORYBOARD, character_id=None,
            shot_id="shot-001", content_sha256="f" * 64,
        )
        found = repo.find_duplicate(
            project_id="proj-001", shot_id="shot-002",
            kind=ReferenceKind.STORYBOARD, content_sha256="f" * 64,
        )
        assert found is None
