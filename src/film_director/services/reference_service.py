"""ReferenceIngestService — managed file ingestion for reference assets (M5.B).

Handles user-provided and Wind Comic media reference ingestion with actual
image validation, SHA-256 content identity, managed storage, and idempotent
semantic deduplication.

No image generation (M5.C). No approval/selection (M5.D). No API (M5.G).
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import httpx
from PIL import Image

from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.repositories import ReferenceAssetRepository

logger = logging.getLogger(__name__)

_SHA256_BUF = 65_536
_SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP"}
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_HTTP_TIMEOUT = 30.0
_SUPPORTED_SCHEMES = {"http", "https"}


class IngestOutcome(str, Enum):
    IMPORTED = "imported"
    DUPLICATE = "duplicate"
    MISSING_SOURCE = "missing_source"
    DOWNLOAD_FAILED = "download_failed"
    INVALID_IMAGE = "invalid_image"


@dataclass(frozen=True)
class IngestResult:
    outcome: IngestOutcome
    asset: ReferenceAsset | None = None
    detail: str | None = None


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_SHA256_BUF)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _validate_image(path: str) -> tuple[int, int, str] | None:
    """Validate file is a supported image. Returns (width, height, format) or None."""
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            fmt = img.format
            if fmt not in _SUPPORTED_FORMATS:
                return None
            return img.width, img.height, fmt
    except Exception:
        return None


def _format_extension(fmt: str) -> str:
    return {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(fmt, ".bin")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_path_confined(absolute_path: str, storage_root: str) -> bool:
    """Check that absolute_path is strictly inside storage_root using pathlib."""
    try:
        resolved = Path(absolute_path).resolve()
        root = Path(storage_root).resolve()
        return resolved.is_relative_to(root)
    except (ValueError, OSError):
        return False


class ReferenceIngestService:
    """Manages reference asset file ingestion into managed storage."""

    def __init__(
        self,
        repo: ReferenceAssetRepository,
        storage_root: str,
        windcomic_base_url: str = "http://127.0.0.1:3000",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._repo = repo
        self._storage_root = storage_root
        self._wc_base_url = windcomic_base_url.rstrip("/")
        self._http_client = http_client

    def _get_http_client(self) -> httpx.Client:
        if self._http_client is not None:
            return self._http_client
        return httpx.Client(timeout=_HTTP_TIMEOUT)

    def register_user_reference(
        self,
        project_id: str,
        kind: ReferenceKind,
        source_path: str,
        character_id: str | None = None,
        shot_id: str | None = None,
        source_fingerprint: str | None = None,
    ) -> IngestResult:
        """Register a user-provided local file as a reference asset."""
        return self._ingest_from_file(
            project_id=project_id,
            kind=kind,
            source_path=source_path,
            source=ReferenceSource.USER_UPLOAD,
            character_id=character_id,
            shot_id=shot_id,
            source_provenance=f"upload-{uuid.uuid4().hex[:12]}",
            source_fingerprint=source_fingerprint,
        )

    def ingest_wc_reference(
        self,
        project_id: str,
        kind: ReferenceKind,
        media_url: str,
        source_provenance: str,
        character_id: str | None = None,
        shot_id: str | None = None,
        source_fingerprint: str | None = None,
    ) -> IngestResult:
        """Ingest a Wind Comic media reference from a URL or local path."""
        if not media_url or not media_url.strip():
            return IngestResult(outcome=IngestOutcome.MISSING_SOURCE, detail="empty media_url")

        media_url = media_url.strip()

        # Relative URL → resolve against WC base
        if media_url.startswith("/"):
            media_url = f"{self._wc_base_url}{media_url}"

        # HTTP/HTTPS download
        if media_url.startswith(("http://", "https://")):
            return self._ingest_from_http(
                project_id=project_id,
                kind=kind,
                url=media_url,
                source=ReferenceSource.WIND_COMIC,
                character_id=character_id,
                shot_id=shot_id,
                source_provenance=source_provenance,
                source_fingerprint=source_fingerprint,
            )

        # Check for unsupported schemes
        if "://" in media_url:
            scheme = media_url.split("://")[0].lower()
            if scheme not in _SUPPORTED_SCHEMES:
                return IngestResult(
                    outcome=IngestOutcome.DOWNLOAD_FAILED,
                    detail=f"unsupported scheme: {scheme}",
                )

        # Local file path
        if not os.path.isfile(media_url):
            return IngestResult(
                outcome=IngestOutcome.MISSING_SOURCE,
                detail=f"file not found: {media_url}",
            )

        return self._ingest_from_file(
            project_id=project_id,
            kind=kind,
            source_path=media_url,
            source=ReferenceSource.WIND_COMIC,
            character_id=character_id,
            shot_id=shot_id,
            source_provenance=source_provenance,
            source_fingerprint=source_fingerprint,
        )

    # ------------------------------------------------------------------
    # HTTP download
    # ------------------------------------------------------------------

    def _ingest_from_http(
        self,
        project_id: str,
        kind: ReferenceKind,
        url: str,
        source: ReferenceSource,
        character_id: str | None,
        shot_id: str | None,
        source_provenance: str,
        source_fingerprint: str | None,
    ) -> IngestResult:
        """Download image from HTTP/HTTPS URL, then ingest from temp file."""
        tmp_path = None
        try:
            client = self._get_http_client()
            owns_client = self._http_client is None
            try:
                resp = client.get(url)
            except (httpx.HTTPError, httpx.TransportError) as e:
                return IngestResult(
                    outcome=IngestOutcome.DOWNLOAD_FAILED,
                    detail=f"transport error: {type(e).__name__}",
                )
            finally:
                if owns_client:
                    client.close()

            if resp.status_code != 200:
                return IngestResult(
                    outcome=IngestOutcome.DOWNLOAD_FAILED,
                    detail=f"HTTP {resp.status_code}",
                )

            content = resp.content
            if len(content) > _MAX_DOWNLOAD_BYTES:
                return IngestResult(
                    outcome=IngestOutcome.DOWNLOAD_FAILED,
                    detail=f"response exceeds {_MAX_DOWNLOAD_BYTES} bytes",
                )

            if not content:
                return IngestResult(
                    outcome=IngestOutcome.DOWNLOAD_FAILED,
                    detail="empty response body",
                )

            # Write to temp file for image validation
            suffix = os.path.splitext(url.split("?")[0])[-1] or ".tmp"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(content)

            return self._ingest_from_file(
                project_id=project_id,
                kind=kind,
                source_path=tmp_path,
                source=source,
                character_id=character_id,
                shot_id=shot_id,
                source_provenance=source_provenance,
                source_fingerprint=source_fingerprint,
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # File-based ingest (shared by user + local WC + downloaded HTTP)
    # ------------------------------------------------------------------

    def _ingest_from_file(
        self,
        project_id: str,
        kind: ReferenceKind,
        source_path: str,
        source: ReferenceSource,
        character_id: str | None,
        shot_id: str | None,
        source_provenance: str,
        source_fingerprint: str | None,
    ) -> IngestResult:
        # 1. Validate image
        if not os.path.isfile(source_path):
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail="file not found")

        result = _validate_image(source_path)
        if result is None:
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail="not a valid supported image")

        width, height, fmt = result

        # 2. Compute SHA of source bytes
        content_sha256 = _compute_sha256(source_path)

        # 3. Dedup check via dedicated repository query
        existing = self._repo.find_duplicate(
            project_id=project_id,
            character_id=character_id,
            shot_id=shot_id,
            kind=kind,
            content_sha256=content_sha256,
        )
        if existing is not None:
            return IngestResult(outcome=IngestOutcome.DUPLICATE, asset=existing)

        # 4. Create asset ID and managed path
        asset_id = f"ref_{uuid.uuid4().hex[:12]}"
        ext = _format_extension(fmt)
        relative_path = f"references/{project_id}/{asset_id}/original{ext}"
        absolute_path = os.path.join(self._storage_root, relative_path)

        # 5. Path confinement check (pathlib-based)
        if not _is_path_confined(absolute_path, self._storage_root):
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail="path confinement violation")

        # 6. Copy to managed storage (preserving original bytes)
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        try:
            shutil.copy2(source_path, absolute_path)
        except OSError as e:
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail=f"copy failed: {e}")

        # 7. Verify managed copy SHA matches
        managed_sha = _compute_sha256(absolute_path)
        if managed_sha != content_sha256:
            try:
                os.remove(absolute_path)
            except OSError:
                pass
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail="SHA mismatch after copy")

        # 8. Persist ReferenceAsset
        now = _now_iso()
        asset = ReferenceAsset(
            id=asset_id,
            project_id=project_id,
            character_id=character_id,
            shot_id=shot_id,
            kind=kind,
            source=source,
            managed_path=relative_path,
            content_sha256=content_sha256,
            source_provenance=source_provenance,
            source_fingerprint=source_fingerprint,
            status=ReferenceStatus.CANDIDATE,
            source_state=ReferenceSourceState.CURRENT,
            pinned=False,
            width=width,
            height=height,
            created_at=now,
            updated_at=now,
        )

        try:
            self._repo.save(asset)
        except Exception:
            try:
                shutil.rmtree(os.path.dirname(absolute_path), ignore_errors=True)
            except OSError:
                pass
            raise

        return IngestResult(outcome=IngestOutcome.IMPORTED, asset=asset)
