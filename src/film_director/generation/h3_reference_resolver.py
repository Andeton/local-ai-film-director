"""H3ReferenceResolver — reference resolution for H3 R2V generation.

M3 legacy: resolve() iterates shot.subjects, selects first ref_images path.
M5 production: resolve_from_assets() builds bindings from ReferenceSelector
output (ReferenceAsset list) with full asset provenance.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

from film_director.errors import ReferenceResolutionError
from film_director.generation.h3_types import H3ReferenceBinding

if TYPE_CHECKING:
    from film_director.models.canonical import CharacterReference, ShotSpecificationV1
    from film_director.models.reference import ReferenceAsset

_SHA256_BUF = 65_536  # 64 KiB read chunks


class H3ReferenceResolver:
    """Reference resolution for H3 R2V generation.

    M3 legacy path: resolve() reads from shot.subjects[].ref_images.
    M5 production path: resolve_from_assets() uses ReferenceSelector output
    with managed-path confinement + SHA re-verification.
    """

    def __init__(self, storage_root: str | None = None) -> None:
        self._storage_root = storage_root

    def resolve(
        self,
        shot: ShotSpecificationV1,
        characters: list[CharacterReference],
        max_refs: int,
    ) -> list[H3ReferenceBinding]:
        if max_refs < 1:
            raise ReferenceResolutionError(
                "max_refs must be >= 1",
                detail=f"max_refs={max_refs}",
            )
        if not shot.subjects:
            raise ReferenceResolutionError(
                "Shot has no subjects — at least one subject required for R2V",
            )
        if len(shot.subjects) > max_refs:
            raise ReferenceResolutionError(
                f"Shot has {len(shot.subjects)} subjects but max_refs={max_refs}",
                detail=f"max_refs={max_refs}, subjects={len(shot.subjects)}",
            )

        char_map: dict[str, CharacterReference] = {c.id: c for c in characters}
        bindings: list[H3ReferenceBinding] = []

        for idx, subject in enumerate(shot.subjects, start=1):
            char = char_map.get(subject.character_id)
            if char is None:
                raise ReferenceResolutionError(
                    f"No CharacterReference for character_id={subject.character_id!r}",
                    detail=f"character_id={subject.character_id}",
                )

            if not subject.ref_images:
                raise ReferenceResolutionError(
                    f"Subject {subject.character_id!r} has empty ref_images",
                    detail=f"character_id={subject.character_id}",
                )

            local_path = subject.ref_images[0]

            if not os.path.exists(local_path):
                raise ReferenceResolutionError(
                    f"Reference file does not exist: {local_path}",
                    detail=f"local_path={local_path}",
                )
            if not os.path.isfile(local_path):
                raise ReferenceResolutionError(
                    f"Reference path is not a regular file: {local_path}",
                    detail=f"local_path={local_path}",
                )

            content_sha256 = _compute_sha256(local_path)

            bindings.append(
                H3ReferenceBinding(
                    subject_index=idx,
                    character_id=char.id,
                    character_name=char.name,
                    appearance=char.appearance,
                    picture_index=idx,
                    local_path=local_path,
                    content_sha256=content_sha256,
                )
            )

        return bindings

    def resolve_from_assets(
        self,
        shot: ShotSpecificationV1,
        selected_assets: list[ReferenceAsset],
        characters: list[CharacterReference],
    ) -> list[H3ReferenceBinding]:
        """Build H3ReferenceBindings from ReferenceSelector output.

        selected_assets must be in subject order (one per shot subject),
        as returned by ReferenceSelector.select(). Each asset's managed_path
        is verified to exist and its SHA-256 is re-verified against the stored
        content_sha256.

        Returns frozen bindings with reference_asset_id + reference_kind set.
        """
        if not shot.subjects:
            raise ReferenceResolutionError(
                "Shot has no subjects — at least one subject required for R2V",
            )
        if len(selected_assets) != len(shot.subjects):
            raise ReferenceResolutionError(
                f"Expected {len(shot.subjects)} asset(s) for {len(shot.subjects)} "
                f"subject(s), got {len(selected_assets)}",
                detail=f"subjects={len(shot.subjects)}, assets={len(selected_assets)}",
            )

        char_map: dict[str, CharacterReference] = {c.id: c for c in characters}
        bindings: list[H3ReferenceBinding] = []

        for idx, (subject, asset) in enumerate(
            zip(shot.subjects, selected_assets), start=1,
        ):
            # Validate asset belongs to the correct character
            if asset.character_id != subject.character_id:
                raise ReferenceResolutionError(
                    f"Asset {asset.id} character_id={asset.character_id!r} does not "
                    f"match subject character_id={subject.character_id!r}",
                    detail=f"asset_id={asset.id}",
                )

            char = char_map.get(subject.character_id)
            if char is None:
                raise ReferenceResolutionError(
                    f"No CharacterReference for character_id={subject.character_id!r}",
                    detail=f"character_id={subject.character_id}",
                )

            local_path = asset.managed_path

            # Path confinement: reject traversal, symlink escapes, outside-root paths
            if self._storage_root is not None:
                resolved = Path(local_path).resolve()
                root = Path(self._storage_root).resolve()
                if not resolved.is_relative_to(root):
                    raise ReferenceResolutionError(
                        f"Managed path escapes storage root: {local_path}",
                        detail=f"asset_id={asset.id}, resolved={resolved}, root={root}",
                    )

            if not os.path.isfile(local_path):
                raise ReferenceResolutionError(
                    f"Managed reference file does not exist: {local_path}",
                    detail=f"asset_id={asset.id}, managed_path={local_path}",
                )

            # Re-verify SHA-256 against stored value
            actual_sha = _compute_sha256(local_path)
            if actual_sha != asset.content_sha256:
                raise ReferenceResolutionError(
                    f"SHA-256 mismatch for asset {asset.id}: "
                    f"stored={asset.content_sha256}, actual={actual_sha}",
                    detail=f"asset_id={asset.id}",
                )

            bindings.append(
                H3ReferenceBinding(
                    reference_asset_id=asset.id,
                    reference_kind=asset.kind.value,
                    subject_index=idx,
                    character_id=char.id,
                    character_name=char.name,
                    appearance=char.appearance,
                    picture_index=idx,
                    local_path=local_path,
                    content_sha256=asset.content_sha256,
                )
            )

        return bindings


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_SHA256_BUF)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
