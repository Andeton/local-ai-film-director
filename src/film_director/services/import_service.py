"""Import / Normalization Service — connects WindComicAdapter to persistence.

Reads WC project bundles atomically, normalizes to canonical models,
and persists within a single transaction. Supports change detection
and reimport without data loss.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.errors import NormalizationError, WindComicNotFoundError
from film_director.persistence.database import Database
from film_director.models.canonical import (
    CharacterReference,
    ProductionProject,
    Scene,
    Sequence,
)
from film_director.models.provenance import (
    Provenance,
    build_character_source_payload,
    build_project_source_payload,
    build_scene_source_payload,
    compute_source_hash,
)
from film_director.persistence.repositories import (
    CharacterRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result / Change DTOs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImportResult:
    project_id: str
    scenes_imported: int
    characters_imported: int


@dataclass(frozen=True)
class ChangeDetection:
    entity_type: Literal["project", "scene", "character"]
    entity_id: str | None  # None for "added" (not yet in our DB)
    source_asset_id: str
    change_type: Literal["added", "modified", "deleted"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gen_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# ImportService
# ---------------------------------------------------------------------------

class ImportService:
    """Orchestrates import from Wind Comic into our persistence layer."""

    def __init__(
        self,
        adapter: WindComicAdapter,
        project_repo: ProjectRepository,
        sequence_repo: SequenceRepository,
        scene_repo: SceneRepository,
        character_repo: CharacterRepository,
        db: Database,
    ) -> None:
        self._adapter = adapter
        self._project_repo = project_repo
        self._sequence_repo = sequence_repo
        self._scene_repo = scene_repo
        self._character_repo = character_repo
        self._db = db

    # ------------------------------------------------------------------
    # import_project
    # ------------------------------------------------------------------

    def import_project(self, wc_project_id: str) -> ImportResult:
        """Import or reimport a WC project atomically.

        On reimport:
        - Existing entities are updated (data + provenance refreshed, status reset)
        - New upstream entities are imported
        - Entities deleted upstream are marked OUTDATED (never physically deleted)
        - Internal IDs are stable across reimports
        """
        bundle = self._adapter.read_project_bundle(wc_project_id)
        now = _now_iso()

        # Use the DB instance from project_repo to get a transaction
        db = self._db

        try:
            with db.connection() as conn:
                # --- Find or create project ---
                existing_project = self._project_repo.get_project_by_wc_id(wc_project_id, conn=conn)
                project_id = existing_project.id if existing_project else _gen_id("proj_")

                proj_payload = build_project_source_payload(bundle.project)
                proj_hash = compute_source_hash(proj_payload)

                # Build director_context from plan if available
                director_context = {}
                if bundle.director_plan is not None:
                    dp = bundle.director_plan
                    director_context = {
                        "genre": dp.genre,
                        "style": dp.style,
                        "story_structure": dp.story_structure,
                    }

                project = ProductionProject(
                    id=project_id,
                    wc_project_id=wc_project_id,
                    title=bundle.project.title,
                    status="active",
                    aspect=bundle.project.aspect,
                    director_context=director_context,
                    created_at=existing_project.created_at if existing_project else now,
                    updated_at=now,
                    provenance=Provenance(
                        source_system="wind_comic",
                        source_project_id=wc_project_id,
                        source_asset_id=wc_project_id,
                        source_asset_version=None,
                        imported_at=now,
                        source_hash=proj_hash,
                    ),
                )
                self._project_repo.save_project(project, conn=conn)

                # --- Ensure default sequence ---
                sequences = self._sequence_repo.get_sequences_by_project(project_id, conn=conn)
                if sequences:
                    seq_id = sequences[0].id
                else:
                    seq_id = _gen_id("seq_")
                    seq = Sequence(id=seq_id, project_id=project_id, name="Main", order_index=0)
                    self._sequence_repo.save_sequence(seq, conn=conn)

                # --- Import scenes ---
                # Build set of upstream asset IDs
                upstream_scene_ids = {s.asset_id for s in bundle.scenes}

                # Get existing scenes for detecting deletions
                existing_scenes = self._scene_repo.get_scenes_by_sequence(seq_id, conn=conn)
                existing_scene_by_wc_id = {s.wc_scene_id: s for s in existing_scenes}

                scenes_imported = 0
                for idx, wc_scene in enumerate(bundle.scenes):
                    existing = existing_scene_by_wc_id.get(wc_scene.asset_id)
                    scene_id = existing.id if existing else _gen_id("scene_")

                    payload = build_scene_source_payload(wc_scene)
                    scene_hash = compute_source_hash(payload)

                    scene = Scene(
                        id=scene_id,
                        sequence_id=seq_id,
                        wc_scene_id=wc_scene.asset_id,
                        name=wc_scene.name,
                        location=wc_scene.data.get("location", ""),
                        description=wc_scene.data.get("description", ""),
                        order_index=idx,
                        status="draft",
                        provenance=Provenance(
                            source_system="wind_comic",
                            source_project_id=wc_project_id,
                            source_asset_id=wc_scene.asset_id,
                            source_asset_version=wc_scene.version,
                            imported_at=now,
                            source_hash=scene_hash,
                        ),
                    )
                    self._scene_repo.save_scene(scene, conn=conn)
                    scenes_imported += 1

                # Mark scenes deleted upstream as OUTDATED
                for wc_sid, existing_scene in existing_scene_by_wc_id.items():
                    if wc_sid not in upstream_scene_ids:
                        self._scene_repo.mark_outdated(existing_scene.id, conn=conn)

                # --- Import characters ---
                upstream_char_ids = {c.asset_id for c in bundle.characters}

                existing_chars = self._character_repo.get_characters_by_project(project_id, conn=conn)
                existing_char_by_wc_id = {c.wc_character_id: c for c in existing_chars}

                chars_imported = 0
                for wc_char in bundle.characters:
                    existing = existing_char_by_wc_id.get(wc_char.asset_id)
                    char_id = existing.id if existing else _gen_id("char_")

                    # Character reference preservation (BINDING)
                    refs = wc_char.media_urls.copy()
                    if wc_char.persistent_url and wc_char.persistent_url.strip():
                        refs.append(wc_char.persistent_url)
                    turnaround_paths = _dedupe_preserve_order(refs)

                    payload = build_character_source_payload(wc_char)
                    char_hash = compute_source_hash(payload)

                    char = CharacterReference(
                        id=char_id,
                        project_id=project_id,
                        wc_character_id=wc_char.asset_id,
                        name=wc_char.name,
                        description=wc_char.data.get("description", ""),
                        appearance=wc_char.data.get("appearance", ""),
                        face_ref_path=None,  # classification deferred to M5
                        turnaround_paths=turnaround_paths,
                        visual_anchors=[],  # no WC source for this in M1
                        status="active",
                        provenance=Provenance(
                            source_system="wind_comic",
                            source_project_id=wc_project_id,
                            source_asset_id=wc_char.asset_id,
                            source_asset_version=wc_char.version,
                            imported_at=now,
                            source_hash=char_hash,
                        ),
                    )
                    self._character_repo.save_character(char, conn=conn)
                    chars_imported += 1

                # Mark characters deleted upstream as OUTDATED
                for wc_cid, existing_char in existing_char_by_wc_id.items():
                    if wc_cid not in upstream_char_ids:
                        self._character_repo.mark_outdated(existing_char.id, conn=conn)

            return ImportResult(
                project_id=project_id,
                scenes_imported=scenes_imported,
                characters_imported=chars_imported,
            )
        except Exception as e:
            # WC errors propagate naturally; wrap unexpected failures
            from film_director.errors import FilmDirectorError
            if isinstance(e, FilmDirectorError):
                raise
            raise NormalizationError(f"Import failed: {e}", detail=str(e)) from e

    # ------------------------------------------------------------------
    # check_for_changes  (SIDE-EFFECT FREE)
    # ------------------------------------------------------------------

    def check_for_changes(self, project_id: str) -> list[ChangeDetection]:
        """Compare stored provenance hashes against current WC source.

        Returns a list of detected changes. Does NOT mutate any state.
        """
        project = self._project_repo.get_project(project_id)
        if project is None:
            return []

        wc_project_id = project.wc_project_id
        try:
            bundle = self._adapter.read_project_bundle(wc_project_id)
        except WindComicNotFoundError:
            return [ChangeDetection(
                entity_type="project",
                entity_id=project.id,
                source_asset_id=wc_project_id,
                change_type="deleted",
            )]

        changes: list[ChangeDetection] = []

        # --- Project-level ---
        proj_payload = build_project_source_payload(bundle.project)
        proj_hash = compute_source_hash(proj_payload)
        if proj_hash != project.provenance.source_hash:
            changes.append(ChangeDetection(
                entity_type="project",
                entity_id=project_id,
                source_asset_id=wc_project_id,
                change_type="modified",
            ))

        # --- Scenes ---
        sequences = self._sequence_repo.get_sequences_by_project(project_id)
        existing_scenes: list[Scene] = []
        for seq in sequences:
            existing_scenes.extend(self._scene_repo.get_scenes_by_sequence(seq.id))
        existing_scene_by_wc_id = {s.wc_scene_id: s for s in existing_scenes}
        upstream_scene_ids = {s.asset_id for s in bundle.scenes}

        for wc_scene in bundle.scenes:
            existing = existing_scene_by_wc_id.get(wc_scene.asset_id)
            if existing is None:
                changes.append(ChangeDetection(
                    entity_type="scene",
                    entity_id=None,
                    source_asset_id=wc_scene.asset_id,
                    change_type="added",
                ))
            else:
                payload = build_scene_source_payload(wc_scene)
                scene_hash = compute_source_hash(payload)
                if scene_hash != existing.provenance.source_hash:
                    changes.append(ChangeDetection(
                        entity_type="scene",
                        entity_id=existing.id,
                        source_asset_id=wc_scene.asset_id,
                        change_type="modified",
                    ))

        for wc_sid, existing_scene in existing_scene_by_wc_id.items():
            if wc_sid not in upstream_scene_ids:
                changes.append(ChangeDetection(
                    entity_type="scene",
                    entity_id=existing_scene.id,
                    source_asset_id=wc_sid,
                    change_type="deleted",
                ))

        # --- Characters ---
        existing_chars = self._character_repo.get_characters_by_project(project_id)
        existing_char_by_wc_id = {c.wc_character_id: c for c in existing_chars}
        upstream_char_ids = {c.asset_id for c in bundle.characters}

        for wc_char in bundle.characters:
            existing = existing_char_by_wc_id.get(wc_char.asset_id)
            if existing is None:
                changes.append(ChangeDetection(
                    entity_type="character",
                    entity_id=None,
                    source_asset_id=wc_char.asset_id,
                    change_type="added",
                ))
            else:
                payload = build_character_source_payload(wc_char)
                char_hash = compute_source_hash(payload)
                if char_hash != existing.provenance.source_hash:
                    changes.append(ChangeDetection(
                        entity_type="character",
                        entity_id=existing.id,
                        source_asset_id=wc_char.asset_id,
                        change_type="modified",
                    ))

        for wc_cid, existing_char in existing_char_by_wc_id.items():
            if wc_cid not in upstream_char_ids:
                changes.append(ChangeDetection(
                    entity_type="character",
                    entity_id=existing_char.id,
                    source_asset_id=wc_cid,
                    change_type="deleted",
                ))

        return changes

    # ------------------------------------------------------------------
    # apply_detected_changes
    # ------------------------------------------------------------------

    def apply_detected_changes(
        self,
        project_id: str,
        changes: list[ChangeDetection],
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Mark entities as OUTDATED based on detected changes.

        For 'added' items, marks parent project OUTDATED.
        For 'modified' / 'deleted' items, marks the specific entity OUTDATED.
        Never deletes. Uses one transaction.

        When *conn* is supplied the caller owns the transaction; this method
        will NOT commit or close the connection.  When *conn* is absent a new
        connection is opened, committed, and closed automatically.
        """
        if conn is not None:
            self._apply_changes(project_id, changes, conn)
        else:
            with self._db.connection() as c:
                self._apply_changes(project_id, changes, c)

    def _apply_changes(
        self,
        project_id: str,
        changes: list[ChangeDetection],
        conn: sqlite3.Connection,
    ) -> None:
        """Inner implementation: apply changes using an already-open connection."""
        for change in changes:
            if change.change_type == "added":
                # Mark parent project outdated
                self._project_repo.mark_outdated(project_id, conn=conn)
            elif change.change_type in ("modified", "deleted"):
                if change.entity_type == "project":
                    self._project_repo.mark_outdated(project_id, conn=conn)
                elif change.entity_type == "scene" and change.entity_id:
                    self._scene_repo.mark_outdated(change.entity_id, conn=conn)
                elif change.entity_type == "character" and change.entity_id:
                    self._character_repo.mark_outdated(change.entity_id, conn=conn)
