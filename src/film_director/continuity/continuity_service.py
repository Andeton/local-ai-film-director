"""ContinuityService — frame validation, predecessor resolution, and state management.

M7.C: resolves continuity inputs for generation, validates upstream frames,
and manages ContinuityState transitions. Does NOT implement downstream
invalidation propagation (M7.D) or API endpoints (M7.E).
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import pathlib
import re

from film_director.continuity.continuity_binding import ContinuityBinding
from film_director.continuity.continuity_models import (
    ContinuityState,
    compute_continuity_fingerprint,
)
from film_director.continuity.continuity_resolver import ContinuityResolver
from film_director.errors import ContinuityError
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ContinuityStateRepository,
    TakeRepository,
)

logger = logging.getLogger(__name__)

_SHA256_BUF = 65_536
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_FRAME_BYTES = 50 * 1024 * 1024  # 50 MB safety limit


@dataclasses.dataclass(frozen=True)
class ContinuityInput:
    """Resolved continuity input for a downstream shot."""
    continuity_state_id: str
    upstream_shot_id: str
    upstream_take_id: str
    upstream_take_number: int
    frame_path: str
    frame_sha256: str
    continuity_revision: int
    continuity_fingerprint: str


class ContinuityService:
    """Resolves and validates continuity inputs for generation."""

    def __init__(self, db: Database, storage_root: str) -> None:
        self._db = db
        self._storage_root = os.path.abspath(storage_root)
        self._continuity_repo = ContinuityStateRepository(db)
        self._take_repo = TakeRepository(db)

    def resolve_for_generation(self, shot_id: str) -> ContinuityInput | None:
        """Resolve the continuity input for a shot.

        Returns None if the shot is a chain head (no predecessor).
        Raises ContinuityError if predecessor exists but input is invalid.
        """
        with self._db.connection() as conn:
            scene_id = ContinuityResolver.get_scene_id_for_shot(shot_id, conn)
            if scene_id is None:
                raise ContinuityError(
                    f"Cannot resolve scene for shot {shot_id}",
                    detail=f"shot_id={shot_id}",
                )

            predecessor_id = ContinuityResolver.resolve_predecessor(
                shot_id, scene_id, conn,
            )

        if predecessor_id is None:
            return None  # chain head — use R2V path

        # Predecessor exists — resolve its approved Take
        approved_take = self._take_repo.get_approved_for_shot(predecessor_id)
        if approved_take is None:
            raise ContinuityError(
                f"Predecessor shot {predecessor_id} has no approved Take",
                detail=f"shot_id={shot_id}, predecessor={predecessor_id}",
            )

        # Validate the approved Take belongs to the predecessor
        if approved_take.shot_id != predecessor_id:
            raise ContinuityError(
                f"Approved Take {approved_take.id} belongs to shot {approved_take.shot_id}, "
                f"not predecessor {predecessor_id}",
                detail=f"take_id={approved_take.id}",
            )

        # Validate the last frame
        frame_path = approved_take.last_frame_path
        if not frame_path:
            raise ContinuityError(
                f"Predecessor approved Take {approved_take.id} has no last_frame_path",
                detail=f"take_id={approved_take.id}",
            )

        validated_path, frame_sha = self._validate_frame(frame_path)

        # Check existing ContinuityState for SHA consistency
        existing_state = self._continuity_repo.get_by_shot(shot_id)
        if existing_state is not None:
            if existing_state.state == "outdated":
                raise ContinuityError(
                    f"ContinuityState for shot {shot_id} is outdated",
                    detail=f"reason={existing_state.invalidation_reason}",
                )
            if (
                existing_state.state == "current"
                and existing_state.upstream_last_frame_sha256 is not None
                and existing_state.upstream_take_id != approved_take.id
            ):
                raise ContinuityError(
                    f"ContinuityState for shot {shot_id} has conflicting current provenance "
                    f"(existing take={existing_state.upstream_take_id}, "
                    f"new take={approved_take.id})",
                    detail=f"shot_id={shot_id}",
                )

        # Resolve take_number from the GenerationRequest
        take_number = self._resolve_take_number(approved_take)

        # Compute fingerprint
        fingerprint = compute_continuity_fingerprint(
            upstream_shot_id=predecessor_id,
            upstream_take_id=approved_take.id,
            upstream_take_number=take_number,
            upstream_last_frame_sha256=frame_sha,
            continuity_revision=existing_state.continuity_revision if existing_state else 0,
        )

        # Build ContinuityState ID
        cs_id = f"cs_{shot_id}"

        return ContinuityInput(
            continuity_state_id=cs_id,
            upstream_shot_id=predecessor_id,
            upstream_take_id=approved_take.id,
            upstream_take_number=take_number,
            frame_path=validated_path,
            frame_sha256=frame_sha,
            continuity_revision=existing_state.continuity_revision if existing_state else 0,
            continuity_fingerprint=fingerprint,
        )

    def rebuild_for_shot(self, shot_id: str) -> ContinuityState:
        """Explicitly repair an OUTDATED continuity state to CURRENT.

        All authoritative reads and the CAS mutation are in one transaction.
        Physical frame validation occurs before the transaction (no external
        I/O inside the DB lock), but verified values are bound to the CAS.

        Returns the committed ContinuityState.
        Raises ContinuityError for invalid states or missing prerequisites.
        """
        from datetime import datetime, timezone

        # --- Phase 1: pre-transaction reads (scene/predecessor structure) ---
        with self._db.connection() as conn:
            scene_id = ContinuityResolver.get_scene_id_for_shot(shot_id, conn)
            if scene_id is None:
                raise ContinuityError(
                    f"Cannot resolve scene for shot {shot_id}",
                    detail=f"shot_id={shot_id}",
                )
            predecessor_id = ContinuityResolver.resolve_predecessor(
                shot_id, scene_id, conn,
            )

        if predecessor_id is None:
            raise ContinuityError(
                "Cannot rebuild continuity for a chain-head shot (no predecessor)",
                detail=f"shot_id={shot_id}",
            )

        # --- Phase 2: predecessor validation (no DB mutation) ---
        approved_take = self._take_repo.get_approved_for_shot(predecessor_id)
        if approved_take is None:
            raise ContinuityError(
                f"Predecessor shot {predecessor_id} has no approved Take",
                detail=f"shot_id={shot_id}, predecessor={predecessor_id}",
            )

        frame_path = approved_take.last_frame_path
        if not frame_path:
            raise ContinuityError(
                f"Predecessor approved Take {approved_take.id} has no last_frame_path",
            )
        validated_path, frame_sha = self._validate_frame(frame_path)
        take_number = self._resolve_take_number(approved_take)

        # --- Phase 3: single atomic transaction for all state reads + CAS ---
        with self._db.connection() as conn:
            # Re-read predecessor continuity inside transaction
            pred_state = self._continuity_repo.get_by_shot(predecessor_id, conn=conn)
            if pred_state is not None and pred_state.state == "outdated":
                raise ContinuityError(
                    f"Predecessor shot {predecessor_id} continuity is outdated — repair it first",
                    detail=f"shot_id={shot_id}, predecessor={predecessor_id}",
                )

            # Read downstream state inside same transaction
            existing = self._continuity_repo.get_by_shot(shot_id, conn=conn)

            if existing is not None and existing.state == "current":
                if (
                    existing.upstream_take_id == approved_take.id
                    and existing.upstream_last_frame_sha256 == frame_sha
                ):
                    return existing  # Idempotent — same provenance
                raise ContinuityError(
                    f"Shot {shot_id} has conflicting current provenance "
                    f"(existing take={existing.upstream_take_id}, "
                    f"new take={approved_take.id})",
                )

            now = datetime.now(timezone.utc).isoformat()

            if existing is not None and existing.state == "outdated":
                # CAS: OUTDATED at expected_revision → CURRENT at revision+1
                expected_revision = existing.continuity_revision
                affected = self._continuity_repo.cas_rebuild_to_current(
                    shot_id=shot_id,
                    expected_revision=expected_revision,
                    upstream_take_id=approved_take.id,
                    upstream_take_number=take_number,
                    upstream_last_frame_path=validated_path,
                    upstream_last_frame_sha256=frame_sha,
                    predecessor_shot_id=predecessor_id,
                    scene_id=scene_id,
                    updated_at=now,
                    conn=conn,
                )
                if affected == 0:
                    # Concurrent rebuild or intervening invalidation
                    reloaded = self._continuity_repo.get_by_shot(shot_id, conn=conn)
                    if reloaded and reloaded.state == "current":
                        if (
                            reloaded.upstream_take_id == approved_take.id
                            and reloaded.upstream_last_frame_sha256 == frame_sha
                        ):
                            return reloaded  # Concurrent rebuild won — idempotent
                    raise ContinuityError(
                        f"Concurrent rebuild conflict for shot {shot_id}",
                        detail=f"expected_revision={expected_revision}",
                    )

                # Read back the committed state
                result = self._continuity_repo.get_by_shot(shot_id, conn=conn)
                return result

            # No existing state — create new CURRENT state
            new_revision = 1
            cs = ContinuityState(
                id=f"cs_{shot_id}",
                shot_id=shot_id,
                scene_id=scene_id,
                predecessor_shot_id=predecessor_id,
                upstream_take_id=approved_take.id,
                upstream_take_number=take_number,
                upstream_last_frame_path=validated_path,
                upstream_last_frame_sha256=frame_sha,
                state="current",
                continuity_revision=new_revision,
                invalidation_reason=None,
                invalidation_source_shot_id=None,
                invalidated_at=None,
                created_at=now,
                updated_at=now,
            )
            self._continuity_repo.save(cs, conn=conn)
            return cs

    def persist_state(
        self, shot_id: str, scene_id: str, continuity_input: ContinuityInput,
    ) -> None:
        """Create or update ContinuityState to CURRENT after successful resolution.

        Uses an atomic CAS to refuse overwriting an OUTDATED state. If an
        intervening replace_approved invalidated the chain during in-flight
        generation, the finalization skips the write. The in-flight Take
        remains preserved but continuity-ineligible.

        All reads and mutations are in one transaction via shared conn.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        cs = ContinuityState(
            id=continuity_input.continuity_state_id,
            shot_id=shot_id,
            scene_id=scene_id,
            predecessor_shot_id=continuity_input.upstream_shot_id,
            upstream_take_id=continuity_input.upstream_take_id,
            upstream_take_number=continuity_input.upstream_take_number,
            upstream_last_frame_path=continuity_input.frame_path,
            upstream_last_frame_sha256=continuity_input.frame_sha256,
            state="current",
            continuity_revision=continuity_input.continuity_revision,
            created_at=now,
            updated_at=now,
        )

        with self._db.connection() as conn:
            ok = self._continuity_repo.cas_persist_if_not_outdated(cs, conn=conn)
            if not ok:
                logger.info(
                    "Skipping persist_state for shot %s: state is OUTDATED "
                    "(intervening invalidation during in-flight generation)",
                    shot_id,
                )

    def _validate_frame(self, frame_path: str) -> tuple[str, str]:
        """Validate and return (resolved_path, sha256) for a last-frame file.

        Raises ContinuityError on any validation failure.
        """
        if not frame_path.strip():
            raise ContinuityError("Frame path is empty")

        # Resolve against storage root
        storage_root = pathlib.Path(self._storage_root).resolve()
        resolved = pathlib.Path(frame_path).resolve()

        # Confinement: resolved path must be under storage root.
        # pathlib.resolve() dereferences symlinks, so this implicitly
        # rejects symlinks that escape the storage boundary.
        try:
            resolved.relative_to(storage_root)
        except ValueError:
            raise ContinuityError(
                f"Frame path escapes storage root",
                detail=f"path={frame_path}",
            )

        path_str = str(resolved)

        # Existence and type
        if not os.path.exists(path_str):
            raise ContinuityError(
                f"Frame file does not exist: {frame_path}",
                detail=f"resolved={path_str}",
            )
        if not os.path.isfile(path_str):
            raise ContinuityError(
                f"Frame path is not a regular file: {frame_path}",
            )

        # Size
        size = os.path.getsize(path_str)
        if size == 0:
            raise ContinuityError(f"Frame file is empty: {frame_path}")
        if size > _MAX_FRAME_BYTES:
            raise ContinuityError(
                f"Frame file exceeds size limit ({size} > {_MAX_FRAME_BYTES}): {frame_path}",
            )

        # PNG magic
        with open(path_str, "rb") as f:
            magic = f.read(8)
        if magic != _PNG_MAGIC:
            raise ContinuityError(
                f"Frame file is not a valid PNG: {frame_path}",
                detail=f"magic={magic[:8].hex()}",
            )

        # SHA-256
        sha = self._compute_sha256(path_str)

        return path_str, sha

    def _resolve_take_number(self, take) -> int:
        """Get take_number from the Take's GenerationRequest."""
        from film_director.persistence.repositories import GenerationRequestRepository
        req_repo = GenerationRequestRepository(self._db)
        req = req_repo.get_request(take.generation_request_id)
        if req is None:
            raise ContinuityError(
                f"GenerationRequest {take.generation_request_id} not found for Take {take.id}",
            )
        return req.take_number

    @staticmethod
    def _compute_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_SHA256_BUF)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
