"""Safe local media serving for operator UI.

Restricts file serving to managed storage roots only.
Prevents path traversal attacks.
Supports MP4 video and PNG/JPEG images.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

_ALLOWED_EXTENSIONS = {".mp4", ".png", ".jpg", ".jpeg", ".webp"}

def _create_media_router(storage_root: str) -> APIRouter:
    """Create a media router bound to a specific storage root."""
    router = APIRouter(prefix="/media", tags=["media"])
    resolved_root = Path(storage_root).resolve()
    # Storage prefix used in DB paths (e.g. "storage\\takes\\...")
    _storage_prefix = os.path.basename(storage_root)

    @router.get("/{path:path}")
    def serve_media(path: str) -> FileResponse:
        """Serve a file from managed storage. Path-traversal safe.

        Accepts both paths relative to storage root and paths that
        include the storage directory name as prefix.
        """
        # Strip storage prefix if present (DB stores "storage\\takes\\...")
        clean = path.replace("\\", "/")
        if clean.startswith(_storage_prefix + "/"):
            clean = clean[len(_storage_prefix) + 1:]

        # Resolve and confine
        full = (resolved_root / clean).resolve()
        if not full.is_relative_to(resolved_root):
            raise HTTPException(status_code=403, detail="Path traversal rejected")
        if not full.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        ext = full.suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=403, detail=f"File type {ext} not allowed")

        media_types = {
            ".mp4": "video/mp4",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        return FileResponse(str(full), media_type=media_types.get(ext, "application/octet-stream"))

    return router
