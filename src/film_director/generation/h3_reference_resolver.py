"""H3ReferenceResolver — minimal M3 reference resolution.

Iterates shot.subjects in declaration order, matches each to a
CharacterReference by character_id, selects the first ref_images path,
validates the file, computes SHA-256, and returns frozen H3ReferenceBinding
instances.  Upload filenames remain empty — M3.G owns the upload transition.
"""
from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

from film_director.errors import ReferenceResolutionError
from film_director.generation.h3_types import H3ReferenceBinding

if TYPE_CHECKING:
    from film_director.models.canonical import CharacterReference, ShotSpecificationV1

_SHA256_BUF = 65_536  # 64 KiB read chunks


class H3ReferenceResolver:
    """Minimal M3 reference resolver — first ref_images path only."""

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


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_SHA256_BUF)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
