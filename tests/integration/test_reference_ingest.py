"""Integration tests for M5.B — managed reference file ingestion.

Tests user-provided and WC media reference ingestion with real image
validation, SHA-256, managed storage, deduplication, and structured outcomes.
No live WC/ComfyUI dependency — uses temp files and fake HTTP.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil

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
    IngestResult,
    ReferenceIngestService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_image(path, fmt="PNG", width=256, height=384):
    """Create a valid test image file."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    img.save(path, fmt)
    return path


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


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


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    _seed_project(db, "proj-001")
    _seed_project(db, "proj-002")
    repo = ReferenceAssetRepository(db)
    storage_root = str(tmp_path / "storage")
    svc = ReferenceIngestService(repo=repo, storage_root=storage_root,
                                 windcomic_base_url="http://wc.local:3000")
    return dict(db=db, repo=repo, svc=svc, tmp_path=tmp_path, storage_root=storage_root)


# ===========================================================================
# USER INGEST — valid images
# ===========================================================================

class TestUserIngestValid:
    def test_png_registration(self, env):
        src = _create_test_image(str(env["tmp_path"] / "face.png"), "PNG")
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.outcome == IngestOutcome.IMPORTED
        assert result.asset is not None
        assert result.asset.source == ReferenceSource.USER_UPLOAD
        assert result.asset.kind == ReferenceKind.CHARACTER_FACE

    def test_jpeg_registration(self, env):
        src = _create_test_image(str(env["tmp_path"] / "body.jpg"), "JPEG")
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_BODY, source_path=src,
        )
        assert result.outcome == IngestOutcome.IMPORTED

    def test_webp_registration(self, env):
        src = _create_test_image(str(env["tmp_path"] / "ref.webp"), "WEBP")
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.outcome == IngestOutcome.IMPORTED

    def test_dimensions_correct(self, env):
        src = _create_test_image(str(env["tmp_path"] / "dim.png"), width=512, height=768)
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.asset.width == 512
        assert result.asset.height == 768

    def test_sha_correct(self, env):
        src = _create_test_image(str(env["tmp_path"] / "sha.png"))
        expected_sha = _file_sha256(src)
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.asset.content_sha256 == expected_sha

    def test_managed_copy_exists(self, env):
        src = _create_test_image(str(env["tmp_path"] / "copy.png"))
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        managed = os.path.join(env["storage_root"], result.asset.managed_path)
        assert os.path.isfile(managed)

    def test_managed_bytes_equal_original(self, env):
        src = _create_test_image(str(env["tmp_path"] / "fidelity.png"))
        original_bytes = open(src, "rb").read()
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        managed = os.path.join(env["storage_root"], result.asset.managed_path)
        managed_bytes = open(managed, "rb").read()
        assert managed_bytes == original_bytes

    def test_default_status_candidate(self, env):
        src = _create_test_image(str(env["tmp_path"] / "status.png"))
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.asset.status == ReferenceStatus.CANDIDATE
        assert result.asset.source_state == ReferenceSourceState.CURRENT
        assert result.asset.pinned is False

    def test_storyboard_with_shot_id(self, env):
        src = _create_test_image(str(env["tmp_path"] / "sb.png"))
        result = env["svc"].register_user_reference(
            project_id="proj-001", shot_id="shot-001",
            kind=ReferenceKind.STORYBOARD, source_path=src,
        )
        assert result.outcome == IngestOutcome.IMPORTED
        assert result.asset.shot_id == "shot-001"
        assert result.asset.character_id is None


# ===========================================================================
# USER INGEST — invalid input
# ===========================================================================

class TestUserIngestInvalid:
    def test_missing_file(self, env):
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path="/nonexistent/file.png",
        )
        assert result.outcome == IngestOutcome.INVALID_IMAGE

    def test_zero_byte_file(self, env):
        src = str(env["tmp_path"] / "empty.png")
        open(src, "w").close()
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.outcome == IngestOutcome.INVALID_IMAGE

    def test_corrupt_image(self, env):
        src = str(env["tmp_path"] / "corrupt.png")
        with open(src, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\ngarbage")
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.outcome == IngestOutcome.INVALID_IMAGE

    def test_text_with_png_suffix(self, env):
        src = str(env["tmp_path"] / "fake.png")
        with open(src, "w") as f:
            f.write("this is not an image")
        result = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert result.outcome == IngestOutcome.INVALID_IMAGE


# ===========================================================================
# DEDUP
# ===========================================================================

class TestDedup:
    def test_exact_repeat_returns_same_asset(self, env):
        src = _create_test_image(str(env["tmp_path"] / "dup.png"))
        r1 = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        r2 = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert r1.outcome == IngestOutcome.IMPORTED
        assert r2.outcome == IngestOutcome.DUPLICATE
        assert r2.asset.id == r1.asset.id

    def test_no_duplicate_db_row(self, env):
        src = _create_test_image(str(env["tmp_path"] / "dup2.png"))
        env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        refs = env["repo"].list_by_character("char-001")
        assert len(refs) == 1

    def test_same_bytes_different_character_unique(self, env):
        src = _create_test_image(str(env["tmp_path"] / "shared.png"))
        r1 = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        r2 = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-002",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert r1.outcome == IngestOutcome.IMPORTED
        assert r2.outcome == IngestOutcome.IMPORTED
        assert r1.asset.id != r2.asset.id

    def test_same_bytes_different_kind_unique(self, env):
        src = _create_test_image(str(env["tmp_path"] / "kind.png"))
        r1 = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        r2 = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_BODY, source_path=src,
        )
        assert r1.outcome == IngestOutcome.IMPORTED
        assert r2.outcome == IngestOutcome.IMPORTED
        assert r1.asset.id != r2.asset.id

    def test_same_bytes_different_project_unique(self, env):
        src = _create_test_image(str(env["tmp_path"] / "proj.png"))
        r1 = env["svc"].register_user_reference(
            project_id="proj-001", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        r2 = env["svc"].register_user_reference(
            project_id="proj-002", character_id="char-001",
            kind=ReferenceKind.CHARACTER_FACE, source_path=src,
        )
        assert r1.outcome == IngestOutcome.IMPORTED
        assert r2.outcome == IngestOutcome.IMPORTED


# ===========================================================================
# WC MEDIA INGEST
# ===========================================================================

class TestWCMediaIngest:
    def test_local_file_ingest(self, env):
        """WC media_url pointing to an accessible local file."""
        src = _create_test_image(str(env["tmp_path"] / "wc_char.png"))
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-wc",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url=src,
            source_provenance="wc-asset-001",
        )
        assert result.outcome == IngestOutcome.IMPORTED
        assert result.asset.source == ReferenceSource.WIND_COMIC
        assert result.asset.source_provenance == "wc-asset-001"

    def test_missing_url(self, env):
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-wc",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="",
            source_provenance="wc-asset-002",
        )
        assert result.outcome == IngestOutcome.MISSING_SOURCE

    def test_nonexistent_file(self, env):
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-wc",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url="/does/not/exist.png",
            source_provenance="wc-asset-003",
        )
        assert result.outcome in (IngestOutcome.DOWNLOAD_FAILED, IngestOutcome.MISSING_SOURCE)

    def test_invalid_image_file(self, env):
        src = str(env["tmp_path"] / "bad_wc.png")
        with open(src, "w") as f:
            f.write("not an image")
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-wc",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url=src,
            source_provenance="wc-asset-004",
        )
        assert result.outcome == IngestOutcome.INVALID_IMAGE

    def test_storyboard_ingest(self, env):
        src = _create_test_image(str(env["tmp_path"] / "wc_sb.png"))
        result = env["svc"].ingest_wc_reference(
            project_id="proj-001", shot_id="shot-wc",
            kind=ReferenceKind.STORYBOARD,
            media_url=src,
            source_provenance="wc-sb-001",
        )
        assert result.outcome == IngestOutcome.IMPORTED
        assert result.asset.kind == ReferenceKind.STORYBOARD
        assert result.asset.shot_id == "shot-wc"

    def test_duplicate_wc_ingest(self, env):
        src = _create_test_image(str(env["tmp_path"] / "wc_dup.png"))
        r1 = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-wc",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url=src, source_provenance="wc-asset-005",
        )
        r2 = env["svc"].ingest_wc_reference(
            project_id="proj-001", character_id="char-wc",
            kind=ReferenceKind.CHARACTER_FACE,
            media_url=src, source_provenance="wc-asset-005",
        )
        assert r1.outcome == IngestOutcome.IMPORTED
        assert r2.outcome == IngestOutcome.DUPLICATE
