"""ReferenceLifecycleService + ReferenceSelector — M5.D.

Lifecycle: approve/reject/archive/pin/unpin with invariant enforcement.
Selector: deterministic provider-neutral reference selection per shot subject.

No H3 binding (M5.E). No API (M5.G).
"""
from __future__ import annotations

from film_director.errors import ReferenceResolutionError
from film_director.models.canonical import ShotSpecificationV1
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.repositories import ReferenceAssetRepository


class ReferenceLifecycleError(Exception):
    """Illegal lifecycle operation on a ReferenceAsset."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Lifecycle Service
# ---------------------------------------------------------------------------

class ReferenceLifecycleService:
    """Provider-neutral lifecycle operations for ReferenceAssets."""

    def __init__(self, repo: ReferenceAssetRepository) -> None:
        self._repo = repo

    def _get_or_raise(self, reference_id: str) -> ReferenceAsset:
        asset = self._repo.get(reference_id)
        if asset is None:
            raise ReferenceLifecycleError(f"Reference not found: {reference_id}")
        return asset

    def approve(self, reference_id: str) -> None:
        self._get_or_raise(reference_id)
        self._repo.update_status(reference_id, ReferenceStatus.APPROVED)

    def reject(self, reference_id: str) -> None:
        self._get_or_raise(reference_id)
        self._repo.update_status(reference_id, ReferenceStatus.REJECTED)

    def archive(self, reference_id: str) -> None:
        self._get_or_raise(reference_id)
        self._repo.update_status(reference_id, ReferenceStatus.ARCHIVED)

    def pin(self, reference_id: str) -> None:
        asset = self._get_or_raise(reference_id)
        if asset.status != ReferenceStatus.APPROVED:
            raise ReferenceLifecycleError(
                f"Cannot pin: status is {asset.status.value}, must be APPROVED"
            )
        if asset.source_state != ReferenceSourceState.CURRENT:
            raise ReferenceLifecycleError(
                f"Cannot pin: source_state is {asset.source_state.value}, must be CURRENT"
            )
        self._repo.update_pinned(reference_id, True)

    def unpin(self, reference_id: str) -> None:
        self._get_or_raise(reference_id)
        self._repo.update_pinned(reference_id, False)


# ---------------------------------------------------------------------------
# Deterministic Selector
# ---------------------------------------------------------------------------

class ReferenceSelector:
    """Provider-neutral deterministic reference selection per shot subject.

    Returns ordered ReferenceAssets matching shot subjects. Does NOT create
    H3ReferenceBinding (M5.E). Does NOT modify inputs.
    """

    def select(
        self,
        shot: ShotSpecificationV1,
        project_id: str,
        kind: ReferenceKind,
        assets: list[ReferenceAsset],
    ) -> list[ReferenceAsset]:
        """Select one eligible reference per shot subject, in subject order.

        Raises ReferenceResolutionError if any subject has no eligible reference.
        Never returns a partial result.
        """
        if not shot.subjects:
            raise ReferenceResolutionError(
                "Shot has no subjects",
                detail=f"shot_id={shot.id}",
            )

        # Filter to eligible: APPROVED + CURRENT + correct project + correct kind
        eligible = [
            a for a in assets
            if a.status == ReferenceStatus.APPROVED
            and a.source_state == ReferenceSourceState.CURRENT
            and a.project_id == project_id
            and a.kind == kind
        ]

        # Sort by priority: pinned DESC, created_at DESC, id ASC
        eligible.sort(key=lambda a: (
            not a.pinned,     # False (pinned) sorts before True (not pinned)
            _invert_ts(a.created_at),  # newer first
            a.id,             # ASC for tie-breaking
        ))

        # Select one per subject in subject order
        selected: list[ReferenceAsset] = []
        for subject in shot.subjects:
            char_id = subject.character_id
            match = None
            for a in eligible:
                if a.character_id == char_id:
                    match = a
                    break
            if match is None:
                raise ReferenceResolutionError(
                    f"No eligible reference for character {char_id}",
                    detail=f"character_id={char_id}, kind={kind.value}, project_id={project_id}",
                )
            selected.append(match)

        return selected


def _invert_ts(ts: str) -> str:
    """Invert timestamp string for descending sort.

    Works because ISO 8601 timestamps sort lexicographically.
    Prefixing with a negative-sortable key achieves descending order
    within a tuple that otherwise sorts ascending.
    """
    # For descending: we want newer timestamps to sort first.
    # Since we're in a tuple with ASC sort, we negate by returning
    # a string that sorts inversely. The simplest correct approach:
    # return the complement characters.
    # But actually, the sort key already handles this via tuple ordering.
    # We just need to negate the string sort.
    # Use a simpler approach: return negated ordinals.
    return "".join(chr(0xFFFF - ord(c)) for c in ts) if ts else ""
