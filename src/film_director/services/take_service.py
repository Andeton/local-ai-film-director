"""TakeService — approve, reject, favorite, unfavorite, replace-approved Takes.

M6.D transition matrix:
  succeeded → approved (terminal, at most one per shot)
  succeeded → rejected (terminal)
  approved → approved (idempotent)
  rejected → rejected (idempotent)
  approved/rejected → favorite/unfavorite allowed (orthogonal)
  pending/generating/failed → approve/reject/favorite forbidden

M7.D extension:
  approved → superseded (only via replace_approved, terminal)
  superseded is terminal (no further transitions)
  superseded/approved → favorite/unfavorite allowed (orthogonal)

is_favorite is independent of status.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from film_director.continuity.continuity_resolver import ContinuityResolver
from film_director.errors import (
    TakeConflictError,
    TakeLifecycleError,
    TakeNotFoundError,
)
from film_director.generation.generation_request import Take
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ContinuityStateRepository,
    TakeRepository,
)

logger = logging.getLogger(__name__)

_REVIEW_ELIGIBLE = ("succeeded",)
_FAVORITE_ELIGIBLE = ("succeeded", "approved", "rejected", "superseded")


class TakeService:
    """Take review lifecycle operations."""

    def __init__(self, repo: TakeRepository, db: Database, storage_root: str | None = None) -> None:
        self._repo = repo
        self._db = db
        self._storage_root = storage_root

    def _get_or_raise(self, take_id: str, conn=None) -> Take:
        take = self._repo.get_take(take_id, conn=conn)
        if take is None:
            raise TakeNotFoundError(f"Take not found: {take_id}")
        return take

    def approve(self, take_id: str) -> Take:
        """Approve a succeeded Take. At most one approved per shot."""
        with self._db.connection() as conn:
            take = self._get_or_raise(take_id, conn=conn)

            if take.status == "approved":
                return take  # idempotent

            if take.status not in _REVIEW_ELIGIBLE:
                raise TakeLifecycleError(
                    f"Cannot approve Take in status {take.status!r}",
                    detail=f"take_id={take_id}",
                )

            # Validate media exists before approving
            if self._storage_root is not None:
                resolved = Path(take.video_path).resolve()
                root = Path(self._storage_root).resolve()
                if not resolved.is_relative_to(root):
                    raise TakeLifecycleError(
                        f"Video path escapes storage root: {take.video_path}",
                    )
            if not os.path.isfile(take.video_path):
                raise TakeLifecycleError(
                    f"Video file missing: {take.video_path}",
                    detail=f"take_id={take_id}",
                )

            # Check single-approved invariant
            existing = self._repo.get_approved_for_shot(take.shot_id, conn=conn)
            if existing is not None and existing.id != take_id:
                raise TakeConflictError(
                    f"Shot {take.shot_id} already has approved Take {existing.id}",
                    detail=f"existing={existing.id}, requested={take_id}",
                )

            # CAS transition
            try:
                updated = self._repo.update_review_status(
                    take_id, _REVIEW_ELIGIBLE, "approved", conn=conn,
                )
            except sqlite3.IntegrityError:
                raise TakeConflictError(
                    f"Concurrent approval conflict for shot {take.shot_id}",
                )
            if updated == 0:
                raise TakeLifecycleError(
                    f"Take {take_id} status changed concurrently",
                )

        return self._get_or_raise(take_id)

    def reject(self, take_id: str) -> Take:
        """Reject a succeeded Take."""
        with self._db.connection() as conn:
            take = self._get_or_raise(take_id, conn=conn)

            if take.status == "rejected":
                return take  # idempotent

            if take.status not in _REVIEW_ELIGIBLE:
                raise TakeLifecycleError(
                    f"Cannot reject Take in status {take.status!r}",
                    detail=f"take_id={take_id}",
                )

            self._repo.update_review_status(
                take_id, _REVIEW_ELIGIBLE, "rejected", conn=conn,
            )

        return self._get_or_raise(take_id)

    def favorite(self, take_id: str) -> Take:
        """Mark a Take as favorite. Orthogonal to status."""
        with self._db.connection() as conn:
            take = self._get_or_raise(take_id, conn=conn)

            if take.is_favorite:
                return take  # idempotent

            if take.status not in _FAVORITE_ELIGIBLE:
                raise TakeLifecycleError(
                    f"Cannot favorite Take in status {take.status!r}",
                    detail=f"take_id={take_id}",
                )

            self._repo.update_favorite(take_id, True, conn=conn)

        return self._get_or_raise(take_id)

    def unfavorite(self, take_id: str) -> Take:
        """Remove favorite from a Take."""
        with self._db.connection() as conn:
            take = self._get_or_raise(take_id, conn=conn)

            if not take.is_favorite:
                return take  # idempotent

            self._repo.update_favorite(take_id, False, conn=conn)

        return self._get_or_raise(take_id)

    def replace_approved(self, new_take_id: str) -> Take:
        """Atomically replace the approved Take for a shot.

        Demotes the old approved Take to 'superseded', approves the new
        succeeded Take, and invalidates all downstream ContinuityStates
        in the same scene. All mutations in one transaction.
        """
        with self._db.connection() as conn:
            new_take = self._repo.get_take(new_take_id, conn=conn)
            if new_take is None:
                raise TakeNotFoundError(f"Take not found: {new_take_id}")

            if new_take.status == "approved":
                return new_take  # idempotent — already the approved take

            if new_take.status != "succeeded":
                raise TakeLifecycleError(
                    f"Cannot replace-approve Take in status {new_take.status!r}",
                    detail=f"take_id={new_take_id}",
                )

            shot_id = new_take.shot_id

            # Find existing approved Take
            old_take = self._repo.get_approved_for_shot(shot_id, conn=conn)
            if old_take is None:
                raise TakeLifecycleError(
                    f"No existing approved Take to replace for shot {shot_id}",
                    detail=f"Use approve() for first approval",
                )

            if old_take.id == new_take_id:
                return new_take  # shouldn't happen, but defensive

            # Validate replacement media
            self._validate_media_for_replacement(new_take)

            # CAS: demote old approved → superseded
            demoted = self._repo.update_review_status(
                old_take.id, ("approved",), "superseded", conn=conn,
            )
            if demoted == 0:
                raise TakeConflictError(
                    f"Old approved Take {old_take.id} status changed concurrently",
                )

            # CAS: promote new succeeded → approved
            try:
                promoted = self._repo.update_review_status(
                    new_take_id, ("succeeded",), "approved", conn=conn,
                )
            except sqlite3.IntegrityError:
                raise TakeConflictError(
                    f"Concurrent approval conflict for shot {shot_id}",
                )
            if promoted == 0:
                raise TakeLifecycleError(
                    f"New Take {new_take_id} status changed concurrently",
                )

            # Invalidate downstream continuity states
            self._invalidate_downstream(shot_id, conn)

        return self._get_or_raise(new_take_id)

    def _validate_media_for_replacement(self, take: Take) -> None:
        """Validate video and last-frame files for a replacement Take."""
        root = Path(self._storage_root).resolve() if self._storage_root else None

        # Video validation
        if root is not None:
            video_resolved = Path(take.video_path).resolve()
            if not video_resolved.is_relative_to(root):
                raise TakeLifecycleError(
                    f"Video path escapes storage root: {take.video_path}",
                )
        if not os.path.isfile(take.video_path):
            raise TakeLifecycleError(
                f"Video file missing: {take.video_path}",
                detail=f"take_id={take.id}",
            )
        if os.path.getsize(take.video_path) == 0:
            raise TakeLifecycleError(
                f"Video file is empty: {take.video_path}",
            )

        # Last-frame validation (required for continuity)
        if not take.last_frame_path:
            raise TakeLifecycleError(
                f"Replacement Take has no last_frame_path",
                detail=f"take_id={take.id}",
            )
        if root is not None:
            frame_resolved = Path(take.last_frame_path).resolve()
            if not frame_resolved.is_relative_to(root):
                raise TakeLifecycleError(
                    f"Last frame path escapes storage root: {take.last_frame_path}",
                )
        if not os.path.isfile(take.last_frame_path):
            raise TakeLifecycleError(
                f"Last frame file missing: {take.last_frame_path}",
                detail=f"take_id={take.id}",
            )
        if os.path.getsize(take.last_frame_path) == 0:
            raise TakeLifecycleError(
                f"Last frame file is empty: {take.last_frame_path}",
            )
        # PNG magic check
        with open(take.last_frame_path, "rb") as f:
            magic = f.read(8)
        if magic != b"\x89PNG\r\n\x1a\n":
            raise TakeLifecycleError(
                f"Last frame is not a valid PNG: {take.last_frame_path}",
            )

    def _invalidate_downstream(self, shot_id: str, conn: sqlite3.Connection) -> int:
        """Invalidate all downstream ContinuityStates in the same scene.

        Walks every shot after shot_id in canonical scene order.
        CURRENT → OUTDATED. Already OUTDATED stays unchanged.
        Returns count of newly invalidated states.
        """
        scene_id = ContinuityResolver.get_scene_id_for_shot(shot_id, conn)
        if scene_id is None:
            return 0

        downstream_ids = ContinuityResolver.get_downstream_shots(
            shot_id, scene_id, conn,
        )

        now = datetime.now(timezone.utc).isoformat()
        cs_repo = ContinuityStateRepository(self._db)
        count = 0
        for ds_id in downstream_ids:
            affected = cs_repo.mark_outdated(
                ds_id,
                reason="upstream_take_changed",
                source_shot_id=shot_id,
                invalidated_at=now,
                conn=conn,
            )
            count += affected

        return count

    def get_approved_for_shot(self, shot_id: str) -> Take | None:
        """Return the single approved Take for a shot, or None."""
        return self._repo.get_approved_for_shot(shot_id)
