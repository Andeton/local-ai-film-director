"""Repositories for canonical production models.

All save_* methods use UPSERT (INSERT ... ON CONFLICT(id) DO UPDATE SET ...)
to guarantee idempotency. Never uses INSERT OR REPLACE or REPLACE INTO.

Repositories accept an optional ``conn`` parameter so callers can group
multiple repository operations inside a single transaction supplied by
``Database.connection()``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from typing import Generator

from film_director.errors import PersistenceError
from film_director.models.canonical import (
    CharacterReference,
    ProductionProject,
    Scene,
    Sequence,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prov_from_row(row: sqlite3.Row) -> Provenance:
    """Reconstruct a Provenance from flattened DB columns."""
    return Provenance(
        source_system=row["prov_source_system"],
        source_project_id=row["prov_source_project_id"],
        source_asset_id=row["prov_source_asset_id"],
        source_asset_version=row["prov_source_asset_version"],  # may be None
        imported_at=row["prov_imported_at"],
        source_hash=row["prov_source_hash"],
    )


@contextmanager
def _use_conn(db: Database, conn: sqlite3.Connection | None) -> Generator[sqlite3.Connection, None, None]:
    """Use provided connection or open a new one via db.connection()."""
    if conn is not None:
        yield conn
    else:
        with db.connection() as c:
            yield c


# ---------------------------------------------------------------------------
# ProjectRepository
# ---------------------------------------------------------------------------

class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_project(self, project: ProductionProject, conn: sqlite3.Connection | None = None) -> None:
        """Upsert a ProductionProject."""
        prov = project.provenance
        sql = """
            INSERT INTO production_projects
                (id, wc_project_id, title, status, aspect, created_at, updated_at,
                 prov_source_system, prov_source_project_id, prov_source_asset_id,
                 prov_source_asset_version, prov_imported_at, prov_source_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                wc_project_id           = excluded.wc_project_id,
                title                   = excluded.title,
                status                  = excluded.status,
                aspect                  = excluded.aspect,
                created_at              = excluded.created_at,
                updated_at              = excluded.updated_at,
                prov_source_system      = excluded.prov_source_system,
                prov_source_project_id  = excluded.prov_source_project_id,
                prov_source_asset_id    = excluded.prov_source_asset_id,
                prov_source_asset_version = excluded.prov_source_asset_version,
                prov_imported_at        = excluded.prov_imported_at,
                prov_source_hash        = excluded.prov_source_hash
        """
        params = (
            project.id, project.wc_project_id, project.title,
            project.status, project.aspect, project.created_at, project.updated_at,
            prov.source_system, prov.source_project_id, prov.source_asset_id,
            prov.source_asset_version, prov.imported_at, prov.source_hash,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save project", str(exc)) from exc

    def get_project(self, project_id: str, conn: sqlite3.Connection | None = None) -> ProductionProject | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM production_projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_project(row)

    def get_project_by_wc_id(self, wc_project_id: str, conn: sqlite3.Connection | None = None) -> ProductionProject | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM production_projects WHERE wc_project_id = ?", (wc_project_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_project(row)

    def list_projects(self, conn: sqlite3.Connection | None = None) -> list[ProductionProject]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute("SELECT * FROM production_projects ORDER BY created_at").fetchall()
        return [self._row_to_project(r) for r in rows]

    def mark_outdated(self, project_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute(
                "UPDATE production_projects SET status = 'outdated' WHERE id = ?",
                (project_id,),
            )

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> ProductionProject:
        return ProductionProject(
            id=row["id"],
            wc_project_id=row["wc_project_id"],
            title=row["title"],
            status=row["status"],
            aspect=row["aspect"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            provenance=_prov_from_row(row),
        )


# ---------------------------------------------------------------------------
# SequenceRepository
# ---------------------------------------------------------------------------

class SequenceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_sequence(self, sequence: Sequence, conn: sqlite3.Connection | None = None) -> None:
        """Upsert a Sequence."""
        sql = """
            INSERT INTO sequences (id, project_id, name, order_index)
            VALUES (?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                project_id  = excluded.project_id,
                name        = excluded.name,
                order_index = excluded.order_index
        """
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, (sequence.id, sequence.project_id, sequence.name, sequence.order_index))
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save sequence", str(exc)) from exc

    def get_sequences_by_project(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[Sequence]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM sequences WHERE project_id = ? ORDER BY order_index",
                (project_id,),
            ).fetchall()
        return [Sequence(id=r["id"], project_id=r["project_id"], name=r["name"], order_index=r["order_index"]) for r in rows]


# ---------------------------------------------------------------------------
# SceneRepository
# ---------------------------------------------------------------------------

class SceneRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_scene(self, scene: Scene, conn: sqlite3.Connection | None = None) -> None:
        """Upsert a Scene."""
        prov = scene.provenance
        sql = """
            INSERT INTO scenes
                (id, sequence_id, wc_scene_id, name, location, description,
                 order_index, status,
                 prov_source_system, prov_source_project_id, prov_source_asset_id,
                 prov_source_asset_version, prov_imported_at, prov_source_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                sequence_id             = excluded.sequence_id,
                wc_scene_id             = excluded.wc_scene_id,
                name                    = excluded.name,
                location                = excluded.location,
                description             = excluded.description,
                order_index             = excluded.order_index,
                status                  = excluded.status,
                prov_source_system      = excluded.prov_source_system,
                prov_source_project_id  = excluded.prov_source_project_id,
                prov_source_asset_id    = excluded.prov_source_asset_id,
                prov_source_asset_version = excluded.prov_source_asset_version,
                prov_imported_at        = excluded.prov_imported_at,
                prov_source_hash        = excluded.prov_source_hash
        """
        params = (
            scene.id, scene.sequence_id, scene.wc_scene_id,
            scene.name, scene.location, scene.description,
            scene.order_index, scene.status,
            prov.source_system, prov.source_project_id, prov.source_asset_id,
            prov.source_asset_version, prov.imported_at, prov.source_hash,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save scene", str(exc)) from exc

    def get_scene(self, scene_id: str, conn: sqlite3.Connection | None = None) -> Scene | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_scene(row)

    def get_scenes_by_sequence(self, sequence_id: str, conn: sqlite3.Connection | None = None) -> list[Scene]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM scenes WHERE sequence_id = ? ORDER BY order_index",
                (sequence_id,),
            ).fetchall()
        return [self._row_to_scene(r) for r in rows]

    def mark_outdated(self, scene_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute("UPDATE scenes SET status = 'outdated' WHERE id = ?", (scene_id,))

    @staticmethod
    def _row_to_scene(row: sqlite3.Row) -> Scene:
        return Scene(
            id=row["id"],
            sequence_id=row["sequence_id"],
            wc_scene_id=row["wc_scene_id"],
            name=row["name"],
            location=row["location"],
            description=row["description"],
            order_index=row["order_index"],
            status=row["status"],
            provenance=_prov_from_row(row),
        )


# ---------------------------------------------------------------------------
# CharacterRepository
# ---------------------------------------------------------------------------

class CharacterRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_character(self, character: CharacterReference, conn: sqlite3.Connection | None = None) -> None:
        """Upsert a CharacterReference. Lists stored as JSON."""
        prov = character.provenance
        sql = """
            INSERT INTO character_references
                (id, project_id, wc_character_id, name, description, appearance,
                 face_ref_path, turnaround_paths, visual_anchors, status,
                 prov_source_system, prov_source_project_id, prov_source_asset_id,
                 prov_source_asset_version, prov_imported_at, prov_source_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                project_id              = excluded.project_id,
                wc_character_id         = excluded.wc_character_id,
                name                    = excluded.name,
                description             = excluded.description,
                appearance              = excluded.appearance,
                face_ref_path           = excluded.face_ref_path,
                turnaround_paths        = excluded.turnaround_paths,
                visual_anchors          = excluded.visual_anchors,
                status                  = excluded.status,
                prov_source_system      = excluded.prov_source_system,
                prov_source_project_id  = excluded.prov_source_project_id,
                prov_source_asset_id    = excluded.prov_source_asset_id,
                prov_source_asset_version = excluded.prov_source_asset_version,
                prov_imported_at        = excluded.prov_imported_at,
                prov_source_hash        = excluded.prov_source_hash
        """
        params = (
            character.id, character.project_id, character.wc_character_id,
            character.name, character.description, character.appearance,
            character.face_ref_path,
            json.dumps(character.turnaround_paths),
            json.dumps(character.visual_anchors),
            character.status,
            prov.source_system, prov.source_project_id, prov.source_asset_id,
            prov.source_asset_version, prov.imported_at, prov.source_hash,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save character", str(exc)) from exc

    def get_characters_by_project(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[CharacterReference]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM character_references WHERE project_id = ? ORDER BY name",
                (project_id,),
            ).fetchall()
        return [self._row_to_character(r) for r in rows]

    def mark_outdated(self, character_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute(
                "UPDATE character_references SET status = 'outdated' WHERE id = ?",
                (character_id,),
            )

    @staticmethod
    def _row_to_character(row: sqlite3.Row) -> CharacterReference:
        return CharacterReference(
            id=row["id"],
            project_id=row["project_id"],
            wc_character_id=row["wc_character_id"],
            name=row["name"],
            description=row["description"],
            appearance=row["appearance"],
            face_ref_path=row["face_ref_path"],
            turnaround_paths=json.loads(row["turnaround_paths"]),
            visual_anchors=json.loads(row["visual_anchors"]),
            status=row["status"],
            provenance=_prov_from_row(row),
        )
