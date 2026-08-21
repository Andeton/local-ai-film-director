"""ReferenceLifecycleService + ReferenceSelector — M5.D.

Lifecycle: approve/reject/archive/pin/unpin with invariant enforcement.
Selector: deterministic provider-neutral reference selection per shot subject.

No H3 binding (M5.E). No API (M5.G).
"""
from __future__ import annotations

from film_director.errors import ReferenceLifecycleError, ReferenceNotFoundError, ReferenceResolutionError
from film_director.models.canonical import CharacterReference, ShotSpecificationV1
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.repositories import ReferenceAssetRepository


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
            raise ReferenceNotFoundError(f"Reference not found: {reference_id}")
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

    Validates subjects against canonical project characters before selecting.
    Returns ordered ReferenceAssets matching shot subjects. Does NOT create
    H3ReferenceBinding (M5.E). Does NOT modify inputs.
    """

    def select(
        self,
        shot: ShotSpecificationV1,
        project_id: str,
        kind: ReferenceKind,
        characters: list[CharacterReference],
        assets: list[ReferenceAsset],
    ) -> list[ReferenceAsset]:
        """Select one eligible reference per shot subject, in subject order.

        Validates each subject's character_id against the supplied project
        characters. Raises ReferenceResolutionError if any subject has no
        valid canonical character or no eligible reference. Never returns
        a partial result.
        """
        if not shot.subjects:
            raise ReferenceResolutionError(
                "Shot has no subjects",
                detail=f"shot_id={shot.id}",
            )

        # Build valid character set: only characters belonging to this project
        valid_char_ids = {
            c.id for c in characters
            if c.project_id == project_id
        }

        # Validate all subjects against canonical characters BEFORE selection
        for subject in shot.subjects:
            if subject.character_id not in valid_char_ids:
                raise ReferenceResolutionError(
                    f"Subject character {subject.character_id} is not a valid "
                    f"canonical character in project {project_id}",
                    detail=f"character_id={subject.character_id}, project_id={project_id}",
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
            not a.pinned,
            _invert_ts(a.created_at),
            a.id,
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

    def select_location_ref(
        self,
        location_id: str,
        project_id: str,
        assets: list[ReferenceAsset],
    ) -> ReferenceAsset | None:
        """Select the best eligible ENVIRONMENT ref for a Location.

        Returns one APPROVED + CURRENT ref with pinned priority, or None.
        Same deterministic priority as character selection.
        """
        eligible = [
            a for a in assets
            if a.kind == ReferenceKind.ENVIRONMENT
            and a.status == ReferenceStatus.APPROVED
            and a.source_state == ReferenceSourceState.CURRENT
            and a.project_id == project_id
            and a.location_id == location_id
        ]
        if not eligible:
            return None
        eligible.sort(key=lambda a: (
            not a.pinned,
            _invert_ts(a.created_at),
            a.id,
        ))
        return eligible[0]


def _invert_ts(ts: str) -> str:
    """Invert timestamp string for descending sort within an ASC tuple."""
    return "".join(chr(0xFFFF - ord(c)) for c in ts) if ts else ""
