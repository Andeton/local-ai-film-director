"""TakeService — approve, reject, favorite, unfavorite Takes (M6.D).

Transition matrix:
  succeeded → approved (terminal, at most one per shot)
  succeeded → rejected (terminal)
  approved → approved (idempotent)
  rejected → rejected (idempotent)
  approved/rejected → favorite/unfavorite allowed (orthogonal)
  pending/generating/failed → approve/reject/favorite forbidden

is_favorite is independent of status:
  - multiple favorites per shot
  - favorite does not approve
  - unfavorite does not reject
  - approve/reject preserves is_favorite
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from film_director.errors import (
    TakeConflictError,
    TakeLifecycleError,
    TakeNotFoundError,
)
from film_director.generation.generation_request import Take
from film_director.persistence.database import Database
from film_director.persistence.repositories import TakeRepository

_REVIEW_ELIGIBLE = ("succeeded",)
_FAVORITE_ELIGIBLE = ("succeeded", "approved", "rejected")


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

    def get_approved_for_shot(self, shot_id: str) -> Take | None:
        """Return the single approved Take for a shot, or None."""
        return self._repo.get_approved_for_shot(shot_id)
