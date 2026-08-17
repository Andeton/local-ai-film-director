"""IdentityResolver — deterministic identity-context resolution for FLF prompts.

M7.F fix: resolves authoritative identity text for each subject in a
downstream continuity shot, using the immutable provenance chain:

Priority:
  1. Approved CURRENT ReferenceAsset's immutable generation prompt (GENERATED source)
  2. Explicit canonical CharacterReference.appearance
  3. Canonical CharacterReference.description
  4. Subject name only (last fallback)

Does NOT upload images, call LLMs, or translate text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from film_director.models.reference import (
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubjectIdentityContext:
    """Resolved identity text for one subject in an FLF continuity shot."""
    character_id: str
    character_name: str
    identity_text: str
    identity_text_source: str  # "reference_generation_prompt", "appearance", "description", "name_only"
    negative_identity_text: str
    reference_asset_id: str | None
    reference_kind: str | None
    reference_source: str | None
    reference_content_sha256: str | None
    reference_generation_request_id: str | None


class IdentityResolver:
    """Resolves deterministic identity context for FLF prompt subjects."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def resolve_for_subjects(
        self,
        subjects: list,
        project_id: str,
        kind: ReferenceKind = ReferenceKind.CHARACTER_BODY,
    ) -> list[SubjectIdentityContext]:
        """Resolve identity context for each subject in shot order.

        Returns one SubjectIdentityContext per subject.
        Uses the same approved+current+pinned-first selection as ReferenceSelector.
        """
        results = []
        for subject in subjects:
            char_id = subject.character_id if hasattr(subject, "character_id") else subject.get("character_id")
            char_name = subject.name if hasattr(subject, "name") else subject.get("name", "Unknown")
            if not char_id:
                results.append(SubjectIdentityContext(
                    character_id="", character_name=char_name,
                    identity_text=char_name, identity_text_source="name_only",
                    negative_identity_text="", reference_asset_id=None,
                    reference_kind=None, reference_source=None,
                    reference_content_sha256=None, reference_generation_request_id=None,
                ))
                continue

            ctx = self._resolve_for_character(char_id, char_name, project_id, kind)
            results.append(ctx)

        return results

    def _resolve_for_character(
        self,
        character_id: str,
        character_name: str,
        project_id: str,
        kind: ReferenceKind,
    ) -> SubjectIdentityContext:
        """Resolve identity for a single character using priority chain."""
        with self._db.connection() as conn:
            # Find best approved+current reference asset
            asset_row = conn.execute(
                """SELECT id, kind, source, content_sha256, status, source_state
                   FROM reference_assets
                   WHERE project_id = ? AND character_id = ? AND kind = ?
                     AND status = 'approved' AND source_state = 'current'
                   ORDER BY pinned DESC, created_at DESC, id ASC
                   LIMIT 1""",
                (project_id, character_id, kind.value),
            ).fetchone()

            ref_asset_id = None
            ref_kind = None
            ref_source = None
            ref_sha = None
            ref_gen_req_id = None
            identity_text = ""
            negative_text = ""
            source_type = "name_only"

            if asset_row:
                ref_asset_id = asset_row["id"]
                ref_kind = asset_row["kind"]
                ref_source = asset_row["source"]
                ref_sha = asset_row["content_sha256"]

                # Priority 1: If GENERATED, trace to immutable generation prompt
                if ref_source == ReferenceSource.GENERATED.value:
                    exec_row = conn.execute(
                        """SELECT request_id FROM reference_generation_executions
                           WHERE output_reference_asset_id = ? AND status = 'succeeded'
                           LIMIT 1""",
                        (ref_asset_id,),
                    ).fetchone()
                    if exec_row:
                        req_row = conn.execute(
                            """SELECT id, prompt, negative_prompt
                               FROM reference_generation_requests WHERE id = ?""",
                            (exec_row["request_id"],),
                        ).fetchone()
                        if req_row and req_row["prompt"].strip():
                            identity_text = req_row["prompt"].strip()
                            negative_text = (req_row["negative_prompt"] or "").strip()
                            ref_gen_req_id = req_row["id"]
                            source_type = "reference_generation_prompt"

            # Priority 2: Canonical appearance
            if not identity_text:
                char_row = conn.execute(
                    "SELECT appearance, description FROM character_references WHERE id = ?",
                    (character_id,),
                ).fetchone()
                if char_row:
                    if char_row["appearance"] and char_row["appearance"].strip():
                        identity_text = char_row["appearance"].strip()
                        source_type = "appearance"
                    elif char_row["description"] and char_row["description"].strip():
                        # Priority 3: Description (may be Chinese)
                        identity_text = char_row["description"].strip()
                        source_type = "description"

            # Priority 4: Name only
            if not identity_text:
                identity_text = character_name
                source_type = "name_only"

        return SubjectIdentityContext(
            character_id=character_id,
            character_name=character_name,
            identity_text=identity_text,
            identity_text_source=source_type,
            negative_identity_text=negative_text,
            reference_asset_id=ref_asset_id,
            reference_kind=ref_kind,
            reference_source=ref_source,
            reference_content_sha256=ref_sha,
            reference_generation_request_id=ref_gen_req_id,
        )
