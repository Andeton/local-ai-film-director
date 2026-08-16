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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

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
        # Re-open after verify (verify may invalidate the file object)
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


class ReferenceIngestService:
    """Manages reference asset file ingestion into managed storage."""

    def __init__(self, repo: ReferenceAssetRepository, storage_root: str) -> None:
        self._repo = repo
        self._storage_root = storage_root

    def register_user_reference(
        self,
        project_id: str,
        kind: ReferenceKind,
        source_path: str,
        character_id: str | None = None,
        shot_id: str | None = None,
        source_fingerprint: str | None = None,
    ) -> IngestResult:
        """Register a user-provided local file as a reference asset.

        Validates image, computes SHA, copies to managed storage, persists
        ReferenceAsset(CANDIDATE). Idempotent: returns DUPLICATE if same
        project+owner+kind+SHA already exists.
        """
        return self._ingest(
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
        """Ingest a Wind Comic media reference from a URL or local path.

        For M5.B: supports local file paths and WC-served URLs.
        Returns structured outcome per item.
        """
        if not media_url or not media_url.strip():
            return IngestResult(outcome=IngestOutcome.MISSING_SOURCE, detail="empty media_url")

        # Resolve source: local file path or HTTP URL
        if media_url.startswith(("http://", "https://")):
            # HTTP download — deferred to when real WC HTTP media is needed
            # For now, treat as download_failed (M5.B supports local paths)
            return IngestResult(
                outcome=IngestOutcome.DOWNLOAD_FAILED,
                detail=f"HTTP download not yet implemented: {media_url[:100]}",
            )

        # Local file path
        if not os.path.isfile(media_url):
            return IngestResult(
                outcome=IngestOutcome.MISSING_SOURCE,
                detail=f"file not found: {media_url}",
            )

        return self._ingest(
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
    # Internal
    # ------------------------------------------------------------------

    def _ingest(
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

        # 3. Dedup check
        existing = self._find_duplicate(project_id, character_id, shot_id, kind, content_sha256)
        if existing is not None:
            return IngestResult(outcome=IngestOutcome.DUPLICATE, asset=existing)

        # 4. Create asset ID and managed path
        asset_id = f"ref_{uuid.uuid4().hex[:12]}"
        ext = _format_extension(fmt)
        relative_path = os.path.join("references", project_id, asset_id, f"original{ext}")
        absolute_path = os.path.normpath(os.path.join(self._storage_root, relative_path))
        # Path confinement: ensure resolved path stays within storage root
        storage_real = os.path.normpath(self._storage_root)
        if not absolute_path.startswith(storage_real + os.sep) and absolute_path != storage_real:
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail="path confinement violation")

        # 5. Copy to managed storage (preserving original bytes)
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        try:
            shutil.copy2(source_path, absolute_path)
        except OSError as e:
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail=f"copy failed: {e}")

        # 6. Verify managed copy SHA matches
        managed_sha = _compute_sha256(absolute_path)
        if managed_sha != content_sha256:
            # Cleanup failed copy
            try:
                os.remove(absolute_path)
            except OSError:
                pass
            return IngestResult(outcome=IngestOutcome.INVALID_IMAGE, detail="SHA mismatch after copy")

        # 7. Persist ReferenceAsset
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
            # Cleanup managed file on DB failure
            try:
                shutil.rmtree(os.path.dirname(absolute_path), ignore_errors=True)
            except OSError:
                pass
            raise

        return IngestResult(outcome=IngestOutcome.IMPORTED, asset=asset)

    def _find_duplicate(
        self,
        project_id: str,
        character_id: str | None,
        shot_id: str | None,
        kind: ReferenceKind,
        content_sha256: str,
    ) -> ReferenceAsset | None:
        """Find existing asset with same project+owner+kind+SHA."""
        if character_id:
            candidates = self._repo.list_by_character(character_id)
        elif shot_id:
            candidates = self._repo.list_by_shot(shot_id)
        else:
            return None

        for c in candidates:
            if (c.project_id == project_id
                    and c.kind == kind
                    and c.content_sha256 == content_sha256):
                return c
        return None
