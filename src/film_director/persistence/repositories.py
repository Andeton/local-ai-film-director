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
from datetime import datetime, timezone
from typing import Generator

from film_director.continuity.continuity_models import ContinuityState
from film_director.errors import PersistenceError
from film_director.generation.generation_request import GenerationRequest, Take
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceGenerationExecution,
    ReferenceGenerationRequest,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.generation.h3_prompt import H3PromptV1
from film_director.models.canonical import (
    Beat,
    CameraIntent,
    CharacterReference,
    GenerationPlan,
    ProductionProject,
    ReferenceRequirements,
    Scene,
    Sequence,
    ShotSpecificationV1,
    ShotSubject,
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
                (id, wc_project_id, title, status, aspect, director_context,
                 created_at, updated_at,
                 prov_source_system, prov_source_project_id, prov_source_asset_id,
                 prov_source_asset_version, prov_imported_at, prov_source_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                wc_project_id           = excluded.wc_project_id,
                title                   = excluded.title,
                status                  = excluded.status,
                aspect                  = excluded.aspect,
                director_context        = excluded.director_context,
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
            project.status, project.aspect, json.dumps(project.director_context),
            project.created_at, project.updated_at,
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
            director_context=json.loads(row["director_context"]),
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

    def get_character(self, character_id: str, conn: sqlite3.Connection | None = None) -> CharacterReference | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute("SELECT * FROM character_references WHERE id = ?", (character_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_character(row)

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


# ---------------------------------------------------------------------------
# BeatRepository
# ---------------------------------------------------------------------------

class BeatRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_beat(self, beat: Beat, conn: sqlite3.Connection | None = None) -> None:
        """Upsert a Beat. characters stored as JSON."""
        sql = """
            INSERT INTO beats
                (id, scene_id, dramatic_action, character_intention, change,
                 characters, order_index, status, source, version,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                scene_id            = excluded.scene_id,
                dramatic_action     = excluded.dramatic_action,
                character_intention = excluded.character_intention,
                change              = excluded.change,
                characters          = excluded.characters,
                order_index         = excluded.order_index,
                status              = excluded.status,
                source              = excluded.source,
                version             = excluded.version,
                created_at          = excluded.created_at,
                updated_at          = excluded.updated_at
        """
        params = (
            beat.id, beat.scene_id, beat.dramatic_action,
            beat.character_intention, beat.change,
            json.dumps(beat.characters),
            beat.order_index, beat.status, beat.source, beat.version,
            beat.created_at, beat.updated_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save beat", str(exc)) from exc

    def get_beat(self, beat_id: str, conn: sqlite3.Connection | None = None) -> Beat | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute("SELECT * FROM beats WHERE id = ?", (beat_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_beat(row)

    def get_beats_by_scene(self, scene_id: str, conn: sqlite3.Connection | None = None) -> list[Beat]:
        """Return ALL beats for a scene (current + historical)."""
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM beats WHERE scene_id = ? ORDER BY order_index, id",
                (scene_id,),
            ).fetchall()
        return [self._row_to_beat(r) for r in rows]

    def get_current_beats_by_scene(self, scene_id: str, conn: sqlite3.Connection | None = None) -> list[Beat]:
        """Return non-outdated beats for a scene."""
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM beats WHERE scene_id = ? AND status != 'outdated' ORDER BY order_index, id",
                (scene_id,),
            ).fetchall()
        return [self._row_to_beat(r) for r in rows]

    def mark_outdated(self, beat_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute("UPDATE beats SET status = 'outdated' WHERE id = ?", (beat_id,))

    def mark_beats_outdated_by_scene(self, scene_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute("UPDATE beats SET status = 'outdated' WHERE scene_id = ?", (scene_id,))

    @staticmethod
    def _row_to_beat(row: sqlite3.Row) -> Beat:
        return Beat(
            id=row["id"],
            scene_id=row["scene_id"],
            dramatic_action=row["dramatic_action"],
            character_intention=row["character_intention"],
            change=row["change"],
            characters=json.loads(row["characters"]),
            order_index=row["order_index"],
            status=row["status"],
            source=row["source"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# ShotRepository
# ---------------------------------------------------------------------------

class ShotRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_shot(self, shot: ShotSpecificationV1, conn: sqlite3.Connection | None = None) -> None:
        """Upsert a ShotSpecificationV1. Complex fields stored as JSON."""
        sql = """
            INSERT INTO shots
                (id, beat_id, wc_storyboard_id, wc_shot_number,
                 dramatic_purpose, subjects, action, environment, camera,
                 lighting, audio_intent, duration_sec, continuity_inputs,
                 storyboard_image_path, order_index, status, source, version,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                beat_id               = excluded.beat_id,
                wc_storyboard_id      = excluded.wc_storyboard_id,
                wc_shot_number        = excluded.wc_shot_number,
                dramatic_purpose      = excluded.dramatic_purpose,
                subjects              = excluded.subjects,
                action                = excluded.action,
                environment           = excluded.environment,
                camera                = excluded.camera,
                lighting              = excluded.lighting,
                audio_intent          = excluded.audio_intent,
                duration_sec          = excluded.duration_sec,
                continuity_inputs     = excluded.continuity_inputs,
                storyboard_image_path = excluded.storyboard_image_path,
                order_index           = excluded.order_index,
                status                = excluded.status,
                source                = excluded.source,
                version               = excluded.version,
                created_at            = excluded.created_at,
                updated_at            = excluded.updated_at
        """
        params = (
            shot.id, shot.beat_id, shot.wc_storyboard_id, shot.wc_shot_number,
            shot.dramatic_purpose,
            json.dumps([s.model_dump() for s in shot.subjects]),
            shot.action,
            json.dumps(shot.environment),
            json.dumps(shot.camera.model_dump()),
            json.dumps(shot.lighting),
            json.dumps(shot.audio_intent),
            shot.duration_sec,
            json.dumps(shot.continuity_inputs),
            shot.storyboard_image_path,
            shot.order_index, shot.status, shot.source, shot.version,
            shot.created_at, shot.updated_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save shot", str(exc)) from exc

    def get_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> ShotSpecificationV1 | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_shot(row)

    def get_shots_by_beat(self, beat_id: str, conn: sqlite3.Connection | None = None) -> list[ShotSpecificationV1]:
        """Return ALL shots for a beat (current + historical)."""
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM shots WHERE beat_id = ? ORDER BY order_index, id",
                (beat_id,),
            ).fetchall()
        return [self._row_to_shot(r) for r in rows]

    def get_current_shots_by_beat(self, beat_id: str, conn: sqlite3.Connection | None = None) -> list[ShotSpecificationV1]:
        """Return non-outdated shots for a beat."""
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM shots WHERE beat_id = ? AND status != 'outdated' ORDER BY order_index, id",
                (beat_id,),
            ).fetchall()
        return [self._row_to_shot(r) for r in rows]

    def get_current_shots_by_project(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[ShotSpecificationV1]:
        """Return current shots across the whole project, excluding outdated shots AND shots on outdated beats."""
        sql = """
            SELECT shots.* FROM shots
            JOIN beats ON shots.beat_id = beats.id
            JOIN scenes ON beats.scene_id = scenes.id
            JOIN sequences ON scenes.sequence_id = sequences.id
            WHERE sequences.project_id = ?
              AND shots.status != 'outdated'
              AND beats.status != 'outdated'
            ORDER BY shots.order_index, shots.id
        """
        with _use_conn(self._db, conn) as c:
            rows = c.execute(sql, (project_id,)).fetchall()
        return [self._row_to_shot(r) for r in rows]

    def mark_outdated(self, shot_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute("UPDATE shots SET status = 'outdated' WHERE id = ?", (shot_id,))

    def mark_shots_outdated_by_beat(self, beat_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute("UPDATE shots SET status = 'outdated' WHERE beat_id = ?", (beat_id,))

    @staticmethod
    def _row_to_shot(row: sqlite3.Row) -> ShotSpecificationV1:
        return ShotSpecificationV1(
            id=row["id"],
            beat_id=row["beat_id"],
            wc_storyboard_id=row["wc_storyboard_id"],
            wc_shot_number=row["wc_shot_number"],
            dramatic_purpose=row["dramatic_purpose"],
            subjects=[ShotSubject(**s) for s in json.loads(row["subjects"])],
            action=row["action"],
            environment=json.loads(row["environment"]),
            camera=CameraIntent(**json.loads(row["camera"])),
            lighting=json.loads(row["lighting"]),
            audio_intent=json.loads(row["audio_intent"]),
            duration_sec=row["duration_sec"],
            continuity_inputs=json.loads(row["continuity_inputs"]),
            storyboard_image_path=row["storyboard_image_path"],
            order_index=row["order_index"],
            status=row["status"],
            source=row["source"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# GenerationPlanRepository
# ---------------------------------------------------------------------------

class GenerationPlanRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save_plan(self, plan: GenerationPlan, conn: sqlite3.Connection | None = None) -> None:
        """Upsert a GenerationPlan. reference_requirements and resolution_intent stored as JSON."""
        sql = """
            INSERT INTO generation_plans
                (id, shot_id, shot_version, strategy, reference_requirements,
                 duration_sec, resolution_intent, seed_policy, seed,
                 continuity_mode, selection_reason, status, version,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                shot_id                = excluded.shot_id,
                shot_version           = excluded.shot_version,
                strategy               = excluded.strategy,
                reference_requirements = excluded.reference_requirements,
                duration_sec           = excluded.duration_sec,
                resolution_intent      = excluded.resolution_intent,
                seed_policy            = excluded.seed_policy,
                seed                   = excluded.seed,
                continuity_mode        = excluded.continuity_mode,
                selection_reason       = excluded.selection_reason,
                status                 = excluded.status,
                version                = excluded.version,
                created_at             = excluded.created_at,
                updated_at             = excluded.updated_at
        """
        params = (
            plan.id, plan.shot_id, plan.shot_version, plan.strategy,
            json.dumps(plan.reference_requirements.model_dump()),
            plan.duration_sec,
            json.dumps(plan.resolution_intent),
            plan.seed_policy, plan.seed,
            plan.continuity_mode, plan.selection_reason,
            plan.status, plan.version,
            plan.created_at, plan.updated_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save generation plan", str(exc)) from exc

    def get_plans_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> list[GenerationPlan]:
        """Return ALL plans for a shot (current + historical)."""
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM generation_plans WHERE shot_id = ? ORDER BY version, id",
                (shot_id,),
            ).fetchall()
        return [self._row_to_plan(r) for r in rows]

    def get_current_plan_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> GenerationPlan | None:
        """Return the non-outdated plan whose shot_version matches the current shot version."""
        sql = """
            SELECT gp.* FROM generation_plans gp
            JOIN shots s ON gp.shot_id = s.id
            WHERE gp.shot_id = ? AND gp.status != 'outdated' AND gp.shot_version = s.version
        """
        with _use_conn(self._db, conn) as c:
            row = c.execute(sql, (shot_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_plan(row)

    def mark_outdated(self, plan_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute("UPDATE generation_plans SET status = 'outdated' WHERE id = ?", (plan_id,))

    def mark_plan_outdated_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute("UPDATE generation_plans SET status = 'outdated' WHERE shot_id = ?", (shot_id,))

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> GenerationPlan:
        return GenerationPlan(
            id=row["id"],
            shot_id=row["shot_id"],
            shot_version=row["shot_version"],
            strategy=row["strategy"],
            reference_requirements=ReferenceRequirements(**json.loads(row["reference_requirements"])),
            duration_sec=row["duration_sec"],
            resolution_intent=json.loads(row["resolution_intent"]),
            seed_policy=row["seed_policy"],
            seed=row["seed"],
            continuity_mode=row["continuity_mode"],
            selection_reason=row["selection_reason"],
            status=row["status"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# H3PromptRepository
# ---------------------------------------------------------------------------

_SENTINEL = object()


class H3PromptRepository:
    """Immutable H3PromptV1 persistence. Plain INSERT — no UPSERT."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save_prompt(self, prompt: H3PromptV1, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO h3_prompts
                (id, shot_id, generation_plan_id,
                 source_shot_version, source_generation_plan_version,
                 subject_definitions, summary, retention_analysis,
                 detailed_description, overall_soundscape, non_diegetic_music,
                 rendered_prompt_text, status, version, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            prompt.id, prompt.shot_id, prompt.generation_plan_id,
            prompt.source_shot_version, prompt.source_generation_plan_version,
            prompt.subject_definitions, prompt.summary, prompt.retention_analysis,
            prompt.detailed_description, prompt.overall_soundscape,
            prompt.non_diegetic_music, prompt.rendered_prompt_text,
            prompt.status, prompt.version, prompt.created_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save H3 prompt", str(exc)) from exc

    def get_prompt(self, prompt_id: str, conn: sqlite3.Connection | None = None) -> H3PromptV1 | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute("SELECT * FROM h3_prompts WHERE id = ?", (prompt_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_prompt(row)

    def get_current_prompt(
        self,
        shot_id: str,
        shot_version: int,
        generation_plan_id: str,
        generation_plan_version: int,
        conn: sqlite3.Connection | None = None,
    ) -> H3PromptV1 | None:
        sql = """
            SELECT * FROM h3_prompts
            WHERE shot_id = ?
              AND source_shot_version = ?
              AND generation_plan_id = ?
              AND source_generation_plan_version = ?
              AND status = 'current'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                sql, (shot_id, shot_version, generation_plan_id, generation_plan_version)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_prompt(row)

    def mark_stale(self, prompt_id: str, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute(
                "UPDATE h3_prompts SET status = 'stale' WHERE id = ?",
                (prompt_id,),
            )

    @staticmethod
    def _row_to_prompt(row: sqlite3.Row) -> H3PromptV1:
        return H3PromptV1(
            id=row["id"],
            shot_id=row["shot_id"],
            generation_plan_id=row["generation_plan_id"],
            source_shot_version=row["source_shot_version"],
            source_generation_plan_version=row["source_generation_plan_version"],
            subject_definitions=row["subject_definitions"],
            summary=row["summary"],
            retention_analysis=row["retention_analysis"],
            detailed_description=row["detailed_description"],
            overall_soundscape=row["overall_soundscape"],
            non_diegetic_music=row["non_diegetic_music"],
            rendered_prompt_text=row["rendered_prompt_text"],
            status=row["status"],
            version=row["version"],
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# GenerationRequestRepository
# ---------------------------------------------------------------------------

class GenerationRequestRepository:
    """INSERT-ONLY GenerationRequest persistence. Input fields are immutable."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_request(self, request: GenerationRequest, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO generation_requests
                (id, shot_id, shot_version, generation_plan_id,
                 generation_plan_version, prompt_artifact_id,
                 prompt_artifact_version, workflow_definition_id,
                 workflow_definition_version, workflow_template_fingerprint,
                 take_number, parameters_snapshot, reference_snapshot,
                 seed, continuity_snapshot, comfyui_prompt_id, status,
                 submitted_at, completed_at, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            request.id, request.shot_id, request.shot_version,
            request.generation_plan_id, request.generation_plan_version,
            request.prompt_artifact_id, request.prompt_artifact_version,
            request.workflow_definition_id, request.workflow_definition_version,
            request.workflow_template_fingerprint, request.take_number,
            json.dumps(request.parameters_snapshot),
            json.dumps(request.reference_snapshot),
            request.seed,
            json.dumps(request.continuity_snapshot) if request.continuity_snapshot is not None else None,
            request.comfyui_prompt_id,
            request.status, request.submitted_at, request.completed_at,
            request.error,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to create generation request", str(exc)) from exc

    def get_request(self, request_id: str, conn: sqlite3.Connection | None = None) -> GenerationRequest | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM generation_requests WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    def get_requests_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> list[GenerationRequest]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM generation_requests WHERE shot_id = ? ORDER BY take_number, id",
                (shot_id,),
            ).fetchall()
        return [self._row_to_request(r) for r in rows]

    def update_status(
        self,
        request_id: str,
        status: str,
        comfyui_prompt_id: object = _SENTINEL,
        submitted_at: object = _SENTINEL,
        completed_at: object = _SENTINEL,
        error: object = _SENTINEL,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        sets = ["status = ?"]
        params: list = [status]
        if comfyui_prompt_id is not _SENTINEL:
            sets.append("comfyui_prompt_id = ?")
            params.append(comfyui_prompt_id)
        if submitted_at is not _SENTINEL:
            sets.append("submitted_at = ?")
            params.append(submitted_at)
        if completed_at is not _SENTINEL:
            sets.append("completed_at = ?")
            params.append(completed_at)
        if error is not _SENTINEL:
            sets.append("error = ?")
            params.append(error)
        params.append(request_id)
        sql = f"UPDATE generation_requests SET {', '.join(sets)} WHERE id = ?"
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to update request status", str(exc)) from exc

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> GenerationRequest:
        raw_cs = row["continuity_snapshot"] if "continuity_snapshot" in row.keys() else None
        return GenerationRequest(
            id=row["id"],
            shot_id=row["shot_id"],
            shot_version=row["shot_version"],
            generation_plan_id=row["generation_plan_id"],
            generation_plan_version=row["generation_plan_version"],
            prompt_artifact_id=row["prompt_artifact_id"],
            prompt_artifact_version=row["prompt_artifact_version"],
            workflow_definition_id=row["workflow_definition_id"],
            workflow_definition_version=row["workflow_definition_version"],
            workflow_template_fingerprint=row["workflow_template_fingerprint"],
            take_number=row["take_number"],
            parameters_snapshot=json.loads(row["parameters_snapshot"]),
            reference_snapshot=json.loads(row["reference_snapshot"]),
            seed=row["seed"],
            continuity_snapshot=json.loads(raw_cs) if raw_cs is not None else None,
            comfyui_prompt_id=row["comfyui_prompt_id"],
            status=row["status"],
            submitted_at=row["submitted_at"],
            completed_at=row["completed_at"],
            error=row["error"],
        )


# ---------------------------------------------------------------------------
# TakeRepository
# ---------------------------------------------------------------------------

class TakeRepository:
    """Take persistence. Plain INSERT. UNIQUE(generation_request_id) enforced by DB."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save_take(self, take: Take, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO takes
                (id, shot_id, generation_request_id, seed, video_path,
                 audio_path, last_frame_path, status, is_favorite, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            take.id, take.shot_id, take.generation_request_id,
            take.seed, take.video_path, take.audio_path,
            take.last_frame_path, take.status, int(take.is_favorite),
            take.created_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save take", str(exc)) from exc

    def get_take(self, take_id: str, conn: sqlite3.Connection | None = None) -> Take | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute("SELECT * FROM takes WHERE id = ?", (take_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_take(row)

    def get_takes_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> list[Take]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM takes WHERE shot_id = ? ORDER BY created_at, id",
                (shot_id,),
            ).fetchall()
        return [self._row_to_take(r) for r in rows]

    def update_review_status(
        self,
        take_id: str,
        expected_statuses: tuple[str, ...],
        new_status: str,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Compare-and-set status transition. Returns rows updated (0 or 1)."""
        placeholders = ",".join("?" for _ in expected_statuses)
        sql = (
            f"UPDATE takes SET status = ? WHERE id = ? "
            f"AND status IN ({placeholders})"
        )
        params = [new_status, take_id, *expected_statuses]
        with _use_conn(self._db, conn) as c:
            cursor = c.execute(sql, params)
            return cursor.rowcount

    def update_favorite(
        self, take_id: str, value: bool, conn: sqlite3.Connection | None = None,
    ) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute(
                "UPDATE takes SET is_favorite = ? WHERE id = ?",
                (int(value), take_id),
            )

    def get_approved_for_shot(
        self, shot_id: str, conn: sqlite3.Connection | None = None,
    ) -> Take | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM takes WHERE shot_id = ? AND status = 'approved' LIMIT 1",
                (shot_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_take(row)

    def count_approved_for_shot(
        self, shot_id: str, conn: sqlite3.Connection | None = None,
    ) -> int:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT COUNT(*) as cnt FROM takes WHERE shot_id = ? AND status = 'approved'",
                (shot_id,),
            ).fetchone()
        return row["cnt"]

    @staticmethod
    def _row_to_take(row: sqlite3.Row) -> Take:
        return Take(
            id=row["id"],
            shot_id=row["shot_id"],
            generation_request_id=row["generation_request_id"],
            seed=row["seed"],
            video_path=row["video_path"],
            audio_path=row["audio_path"],
            last_frame_path=row["last_frame_path"],
            status=row["status"],
            is_favorite=bool(row["is_favorite"]),
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# ReferenceAssetRepository (M5)
# ---------------------------------------------------------------------------

class ReferenceAssetRepository:
    """UPSERT-based persistence for ReferenceAsset."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, asset: ReferenceAsset, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO reference_assets
                (id, project_id, character_id, shot_id, kind, source,
                 managed_path, content_sha256, source_provenance,
                 source_fingerprint, status, source_state, pinned,
                 width, height, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                project_id         = excluded.project_id,
                character_id       = excluded.character_id,
                shot_id            = excluded.shot_id,
                kind               = excluded.kind,
                source             = excluded.source,
                managed_path       = excluded.managed_path,
                content_sha256     = excluded.content_sha256,
                source_provenance  = excluded.source_provenance,
                source_fingerprint = excluded.source_fingerprint,
                status             = excluded.status,
                source_state       = excluded.source_state,
                pinned             = excluded.pinned,
                width              = excluded.width,
                height             = excluded.height,
                created_at         = excluded.created_at,
                updated_at         = excluded.updated_at
        """
        params = (
            asset.id, asset.project_id, asset.character_id, asset.shot_id,
            asset.kind.value, asset.source.value, asset.managed_path,
            asset.content_sha256, asset.source_provenance, asset.source_fingerprint,
            asset.status.value, asset.source_state.value, int(asset.pinned),
            asset.width, asset.height, asset.created_at, asset.updated_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save reference asset", str(exc)) from exc

    def get(self, asset_id: str, conn: sqlite3.Connection | None = None) -> ReferenceAsset | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute("SELECT * FROM reference_assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_asset(row)

    def list_by_project(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[ReferenceAsset]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM reference_assets WHERE project_id = ? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        return [self._row_to_asset(r) for r in rows]

    def list_by_character(self, character_id: str, conn: sqlite3.Connection | None = None) -> list[ReferenceAsset]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM reference_assets WHERE character_id = ? ORDER BY created_at, id",
                (character_id,),
            ).fetchall()
        return [self._row_to_asset(r) for r in rows]

    def list_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> list[ReferenceAsset]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM reference_assets WHERE shot_id = ? ORDER BY created_at, id",
                (shot_id,),
            ).fetchall()
        return [self._row_to_asset(r) for r in rows]

    def update_status(self, asset_id: str, status: ReferenceStatus, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute(
                "UPDATE reference_assets SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status.value, asset_id),
            )

    def update_source_state(self, asset_id: str, state: ReferenceSourceState, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute(
                "UPDATE reference_assets SET source_state = ?, updated_at = datetime('now') WHERE id = ?",
                (state.value, asset_id),
            )

    def find_duplicate(
        self,
        project_id: str,
        kind: ReferenceKind,
        content_sha256: str,
        character_id: str | None = None,
        shot_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> ReferenceAsset | None:
        """Find existing asset with same project+owner+kind+SHA."""
        if character_id:
            sql = """SELECT * FROM reference_assets
                     WHERE project_id = ? AND character_id = ? AND kind = ? AND content_sha256 = ?
                     LIMIT 1"""
            params = (project_id, character_id, kind.value, content_sha256)
        elif shot_id:
            sql = """SELECT * FROM reference_assets
                     WHERE project_id = ? AND shot_id = ? AND kind = ? AND content_sha256 = ?
                     LIMIT 1"""
            params = (project_id, shot_id, kind.value, content_sha256)
        else:
            return None
        with _use_conn(self._db, conn) as c:
            row = c.execute(sql, params).fetchone()
        if row is None:
            return None
        return self._row_to_asset(row)

    def update_pinned(self, asset_id: str, pinned: bool, conn: sqlite3.Connection | None = None) -> None:
        with _use_conn(self._db, conn) as c:
            c.execute(
                "UPDATE reference_assets SET pinned = ?, updated_at = datetime('now') WHERE id = ?",
                (int(pinned), asset_id),
            )

    def mark_generated_stale_for_character(
        self,
        project_id: str,
        character_id: str,
        current_fingerprint: str,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Mark GENERATED + CURRENT refs as STALE when fingerprint mismatches.

        Scoped by project_id + character_id + source=GENERATED + source_state=CURRENT.
        Refs with source_fingerprint != current_fingerprint (or NULL) become STALE.
        Already-STALE refs are not touched. USER_UPLOAD and WIND_COMIC are not touched.
        Preserves status, pinned, SHA, path, provenance, dimensions, created_at.
        Returns count of rows transitioned.
        """
        sql = """
            UPDATE reference_assets
            SET source_state = 'stale', updated_at = datetime('now')
            WHERE project_id = ?
              AND character_id = ?
              AND source = 'generated'
              AND source_state = 'current'
              AND (source_fingerprint IS NULL OR source_fingerprint != ?)
        """
        with _use_conn(self._db, conn) as c:
            cursor = c.execute(sql, (project_id, character_id, current_fingerprint))
            return cursor.rowcount

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> ReferenceAsset:
        return ReferenceAsset(
            id=row["id"],
            project_id=row["project_id"],
            character_id=row["character_id"],
            shot_id=row["shot_id"],
            kind=ReferenceKind(row["kind"]),
            source=ReferenceSource(row["source"]),
            managed_path=row["managed_path"],
            content_sha256=row["content_sha256"],
            source_provenance=row["source_provenance"],
            source_fingerprint=row["source_fingerprint"],
            status=ReferenceStatus(row["status"]),
            source_state=ReferenceSourceState(row["source_state"]),
            pinned=bool(row["pinned"]),
            width=row["width"],
            height=row["height"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# ReferenceGenerationRequestRepository (M5) — INSERT-ONLY
# ---------------------------------------------------------------------------

class ReferenceGenerationRequestRepository:
    """INSERT-ONLY persistence for immutable reference generation requests."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, request: ReferenceGenerationRequest, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO reference_generation_requests
                (id, project_id, character_id, requested_kind,
                 source_appearance_hash, prompt, negative_prompt,
                 workflow_definition_id, workflow_definition_version,
                 workflow_template_fingerprint, parameters_snapshot,
                 seed, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            request.id, request.project_id, request.character_id,
            request.requested_kind.value, request.source_appearance_hash,
            request.prompt, request.negative_prompt,
            request.workflow_definition_id, request.workflow_definition_version,
            request.workflow_template_fingerprint,
            json.dumps(request.parameters_snapshot),
            request.seed, request.created_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to create ref gen request", str(exc)) from exc

    def get(self, request_id: str, conn: sqlite3.Connection | None = None) -> ReferenceGenerationRequest | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM reference_generation_requests WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        return ReferenceGenerationRequest(
            id=row["id"],
            project_id=row["project_id"],
            character_id=row["character_id"],
            requested_kind=ReferenceKind(row["requested_kind"]),
            source_appearance_hash=row["source_appearance_hash"],
            prompt=row["prompt"],
            negative_prompt=row["negative_prompt"],
            workflow_definition_id=row["workflow_definition_id"],
            workflow_definition_version=row["workflow_definition_version"],
            workflow_template_fingerprint=row["workflow_template_fingerprint"],
            parameters_snapshot=json.loads(row["parameters_snapshot"]),
            seed=row["seed"],
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# ReferenceGenerationExecutionRepository (M5)
# ---------------------------------------------------------------------------

class ReferenceGenerationExecutionRepository:
    """Persistence for reference generation executions. INSERT + lifecycle updates."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, execution: ReferenceGenerationExecution, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO reference_generation_executions
                (id, request_id, status, comfyui_prompt_id,
                 output_reference_asset_id, submitted_at, completed_at,
                 error, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            execution.id, execution.request_id, execution.status,
            execution.comfyui_prompt_id, execution.output_reference_asset_id,
            execution.submitted_at, execution.completed_at, execution.error,
            execution.created_at, execution.updated_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to create ref gen execution", str(exc)) from exc

    def get(self, execution_id: str, conn: sqlite3.Connection | None = None) -> ReferenceGenerationExecution | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM reference_generation_executions WHERE id = ?", (execution_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_execution(row)

    def list_by_request(self, request_id: str, conn: sqlite3.Connection | None = None) -> list[ReferenceGenerationExecution]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM reference_generation_executions WHERE request_id = ? ORDER BY created_at, id",
                (request_id,),
            ).fetchall()
        return [self._row_to_execution(r) for r in rows]

    def update_status(
        self, execution_id: str, *,
        status: str,
        comfyui_prompt_id: str | None = None,
        output_reference_asset_id: str | None = None,
        submitted_at: str | None = None,
        completed_at: str | None = None,
        error: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        sets = ["status = ?", "updated_at = datetime('now')"]
        params: list = [status]
        if comfyui_prompt_id is not None:
            sets.append("comfyui_prompt_id = ?")
            params.append(comfyui_prompt_id)
        if output_reference_asset_id is not None:
            sets.append("output_reference_asset_id = ?")
            params.append(output_reference_asset_id)
        if submitted_at is not None:
            sets.append("submitted_at = ?")
            params.append(submitted_at)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(execution_id)
        sql = f"UPDATE reference_generation_executions SET {', '.join(sets)} WHERE id = ?"
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to update ref gen execution", str(exc)) from exc

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> ReferenceGenerationExecution:
        return ReferenceGenerationExecution(
            id=row["id"],
            request_id=row["request_id"],
            status=row["status"],
            comfyui_prompt_id=row["comfyui_prompt_id"],
            output_reference_asset_id=row["output_reference_asset_id"],
            submitted_at=row["submitted_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# QueueJobRepository (M6)
# ---------------------------------------------------------------------------

from film_director.generation.queue_models import QueueBatch, QueueJob  # noqa: E402


class QueueJobRepository:
    """Persistent generation queue operations."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, job: QueueJob, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO generation_queue
                (id, batch_id, shot_id, take_number, project_id, base_seed, seed,
                 status, generation_request_id, take_id, priority,
                 attempt_count, max_attempts, error,
                 created_at, updated_at, claimed_at, completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            job.id, job.batch_id, job.shot_id, job.take_number, job.project_id,
            job.base_seed, job.seed, job.status,
            job.generation_request_id, job.take_id, job.priority,
            job.attempt_count, job.max_attempts, job.error,
            job.created_at, job.updated_at, job.claimed_at, job.completed_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to insert queue job", str(exc)) from exc

    def get(self, job_id: str, conn: sqlite3.Connection | None = None) -> QueueJob | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM generation_queue WHERE id = ?", (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> list[QueueJob]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM generation_queue WHERE shot_id = ? "
                "ORDER BY take_number ASC, id ASC",
                (shot_id,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_by_project(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[QueueJob]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM generation_queue WHERE project_id = ? "
                "ORDER BY priority DESC, created_at ASC, id ASC",
                (project_id,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_by_status(
        self, status: str, limit: int = 50, conn: sqlite3.Connection | None = None,
    ) -> list[QueueJob]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM generation_queue WHERE status = ? "
                "ORDER BY priority DESC, created_at ASC, id ASC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def count_by_status(self, conn: sqlite3.Connection | None = None) -> dict[str, int]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT status, COUNT(*) as cnt FROM generation_queue GROUP BY status",
            ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    def max_take_number_for_shot(
        self, shot_id: str, conn: sqlite3.Connection | None = None,
    ) -> int:
        """Return the highest take_number across queue + takes for this shot, or 0."""
        with _use_conn(self._db, conn) as c:
            q_row = c.execute(
                "SELECT MAX(take_number) as m FROM generation_queue WHERE shot_id = ?",
                (shot_id,),
            ).fetchone()
            t_row = c.execute(
                "SELECT MAX(gr.take_number) as m FROM takes t "
                "JOIN generation_requests gr ON t.generation_request_id = gr.id "
                "WHERE t.shot_id = ?",
                (shot_id,),
            ).fetchone()
        q_max = q_row["m"] if q_row and q_row["m"] is not None else 0
        t_max = t_row["m"] if t_row and t_row["m"] is not None else 0
        return max(q_max, t_max)

    def update_status(
        self, job_id: str, status: str,
        error: str | None = None,
        generation_request_id: str | None = None,
        take_id: str | None = None,
        completed_at: str | None = None,
        claimed_at: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        sets = ["status = ?", "updated_at = datetime('now')"]
        params: list = [status]
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if generation_request_id is not None:
            sets.append("generation_request_id = ?")
            params.append(generation_request_id)
        if take_id is not None:
            sets.append("take_id = ?")
            params.append(take_id)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        if claimed_at is not None:
            sets.append("claimed_at = ?")
            params.append(claimed_at)
        params.append(job_id)
        sql = f"UPDATE generation_queue SET {', '.join(sets)} WHERE id = ?"
        with _use_conn(self._db, conn) as c:
            c.execute(sql, params)

    def claim_next(self, conn: sqlite3.Connection) -> QueueJob | None:
        """Atomically claim the highest-priority pending job.

        Must be called within a caller-owned transaction (conn).
        Uses UPDATE with subquery — SQLite WAL serializes writers.
        Returns the claimed job, or None if queue is empty.
        """
        now = datetime.now(timezone.utc).isoformat()
        # Atomic: UPDATE the single best candidate in one statement
        conn.execute(
            """UPDATE generation_queue
               SET status = 'claimed',
                   claimed_at = ?,
                   updated_at = ?,
                   attempt_count = attempt_count + 1
               WHERE id = (
                   SELECT id FROM generation_queue
                   WHERE status = 'pending' AND attempt_count < max_attempts
                   ORDER BY priority DESC, created_at ASC, id ASC
                   LIMIT 1
               )""",
            (now, now),
        )
        # Find the just-claimed row
        row = conn.execute(
            "SELECT * FROM generation_queue WHERE status = 'claimed' AND claimed_at = ? "
            "ORDER BY id LIMIT 1",
            (now,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_claimed(self, conn: sqlite3.Connection | None = None) -> list[QueueJob]:
        """Return all claimed jobs (for restart recovery)."""
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM generation_queue WHERE status = 'claimed' "
                "ORDER BY created_at ASC, id ASC",
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_by_batch(self, batch_id: str, conn: sqlite3.Connection | None = None) -> list[QueueJob]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM generation_queue WHERE batch_id = ? "
                "ORDER BY shot_id ASC, take_number ASC, id ASC",
                (batch_id,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> QueueJob:
        return QueueJob(
            id=row["id"],
            batch_id=row["batch_id"],
            shot_id=row["shot_id"],
            take_number=row["take_number"],
            project_id=row["project_id"],
            base_seed=row["base_seed"],
            seed=row["seed"],
            status=row["status"],
            generation_request_id=row["generation_request_id"],
            take_id=row["take_id"],
            priority=row["priority"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            claimed_at=row["claimed_at"],
            completed_at=row["completed_at"],
        )


# ---------------------------------------------------------------------------
# QueueBatchRepository (M6)
# ---------------------------------------------------------------------------

class QueueBatchRepository:
    """Persistent batch idempotency records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, batch: QueueBatch, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO queue_batches
                (id, project_id, scope_type, scope_id, idempotency_key,
                 request_fingerprint, takes_count, base_seed, priority, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            batch.id, batch.project_id, batch.scope_type, batch.scope_id,
            batch.idempotency_key, batch.request_fingerprint,
            batch.takes_count, batch.base_seed, batch.priority, batch.created_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to insert queue batch", str(exc)) from exc

    def get_by_key(
        self, project_id: str, idempotency_key: str,
        conn: sqlite3.Connection | None = None,
    ) -> QueueBatch | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM queue_batches WHERE project_id = ? AND idempotency_key = ?",
                (project_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_batch(row)

    def get(self, batch_id: str, conn: sqlite3.Connection | None = None) -> QueueBatch | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM queue_batches WHERE id = ?", (batch_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_batch(row)

    @staticmethod
    def _row_to_batch(row: sqlite3.Row) -> QueueBatch:
        return QueueBatch(
            id=row["id"],
            project_id=row["project_id"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            takes_count=row["takes_count"],
            base_seed=row["base_seed"],
            priority=row["priority"],
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# ContinuityStateRepository (M7.A)
# ---------------------------------------------------------------------------

class ContinuityStateRepository:
    """UPSERT by shot_id. Tracks per-shot continuity chain membership."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, state: ContinuityState, conn: sqlite3.Connection | None = None) -> None:
        sql = """
            INSERT INTO continuity_states
                (id, shot_id, scene_id, predecessor_shot_id,
                 upstream_take_id, upstream_take_number,
                 upstream_last_frame_path, upstream_last_frame_sha256,
                 state, continuity_revision,
                 invalidation_reason, invalidation_source_shot_id,
                 created_at, updated_at, invalidated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(shot_id) DO UPDATE SET
                scene_id                    = excluded.scene_id,
                predecessor_shot_id         = excluded.predecessor_shot_id,
                upstream_take_id            = excluded.upstream_take_id,
                upstream_take_number        = excluded.upstream_take_number,
                upstream_last_frame_path    = excluded.upstream_last_frame_path,
                upstream_last_frame_sha256  = excluded.upstream_last_frame_sha256,
                state                       = excluded.state,
                continuity_revision         = excluded.continuity_revision,
                invalidation_reason         = excluded.invalidation_reason,
                invalidation_source_shot_id = excluded.invalidation_source_shot_id,
                updated_at                  = excluded.updated_at,
                invalidated_at              = excluded.invalidated_at
        """
        params = (
            state.id, state.shot_id, state.scene_id,
            state.predecessor_shot_id,
            state.upstream_take_id, state.upstream_take_number,
            state.upstream_last_frame_path, state.upstream_last_frame_sha256,
            state.state, state.continuity_revision,
            state.invalidation_reason, state.invalidation_source_shot_id,
            state.created_at, state.updated_at, state.invalidated_at,
        )
        try:
            with _use_conn(self._db, conn) as c:
                c.execute(sql, params)
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Failed to save continuity state", str(exc)) from exc

    def get_by_shot(self, shot_id: str, conn: sqlite3.Connection | None = None) -> ContinuityState | None:
        with _use_conn(self._db, conn) as c:
            row = c.execute(
                "SELECT * FROM continuity_states WHERE shot_id = ?",
                (shot_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_state(row)

    def get_by_scene(self, scene_id: str, conn: sqlite3.Connection | None = None) -> list[ContinuityState]:
        with _use_conn(self._db, conn) as c:
            rows = c.execute(
                "SELECT * FROM continuity_states WHERE scene_id = ? ORDER BY id",
                (scene_id,),
            ).fetchall()
        return [self._row_to_state(r) for r in rows]

    def mark_outdated(
        self,
        shot_id: str,
        reason: str,
        source_shot_id: str,
        invalidated_at: str,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Mark a shot's continuity state as outdated. Returns rows affected."""
        sql = """
            UPDATE continuity_states
            SET state = 'outdated',
                invalidation_reason = ?,
                invalidation_source_shot_id = ?,
                invalidated_at = ?,
                updated_at = ?
            WHERE shot_id = ? AND state != 'outdated'
        """
        with _use_conn(self._db, conn) as c:
            cursor = c.execute(sql, (reason, source_shot_id, invalidated_at, invalidated_at, shot_id))
            return cursor.rowcount

    def resolve_current(
        self,
        shot_id: str,
        upstream_take_id: str,
        upstream_take_number: int,
        frame_path: str,
        frame_sha256: str,
        updated_at: str,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Transition a shot's continuity state to current, bumping revision. Returns rows affected."""
        sql = """
            UPDATE continuity_states
            SET state = 'current',
                upstream_take_id = ?,
                upstream_take_number = ?,
                upstream_last_frame_path = ?,
                upstream_last_frame_sha256 = ?,
                continuity_revision = continuity_revision + 1,
                invalidation_reason = NULL,
                invalidation_source_shot_id = NULL,
                invalidated_at = NULL,
                updated_at = ?
            WHERE shot_id = ?
        """
        with _use_conn(self._db, conn) as c:
            cursor = c.execute(sql, (
                upstream_take_id, upstream_take_number,
                frame_path, frame_sha256, updated_at, shot_id,
            ))
            return cursor.rowcount

    def cas_rebuild_to_current(
        self,
        shot_id: str,
        expected_revision: int,
        upstream_take_id: str,
        upstream_take_number: int,
        upstream_last_frame_path: str,
        upstream_last_frame_sha256: str,
        predecessor_shot_id: str,
        scene_id: str,
        updated_at: str,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """CAS: OUTDATED at expected_revision → CURRENT at revision+1.

        Returns rows affected (0 or 1). 0 means the state was not OUTDATED
        or revision has changed (concurrent rebuild or intervening invalidation).
        """
        sql = """
            UPDATE continuity_states
            SET state = 'current',
                upstream_take_id = ?,
                upstream_take_number = ?,
                upstream_last_frame_path = ?,
                upstream_last_frame_sha256 = ?,
                predecessor_shot_id = ?,
                continuity_revision = ? + 1,
                invalidation_reason = NULL,
                invalidation_source_shot_id = NULL,
                invalidated_at = NULL,
                updated_at = ?
            WHERE shot_id = ?
              AND state = 'outdated'
              AND continuity_revision = ?
        """
        with _use_conn(self._db, conn) as c:
            cursor = c.execute(sql, (
                upstream_take_id, upstream_take_number,
                upstream_last_frame_path, upstream_last_frame_sha256,
                predecessor_shot_id,
                expected_revision, updated_at,
                shot_id, expected_revision,
            ))
            return cursor.rowcount

    def cas_persist_if_not_outdated(
        self,
        state: ContinuityState,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """Atomically persist a CURRENT state only if the existing state is NOT outdated.

        Uses INSERT ... ON CONFLICT for new states, or a guarded UPDATE
        for existing states. Returns True if the write succeeded, False if
        an OUTDATED state was found (in-flight race).
        """
        with _use_conn(self._db, conn) as c:
            # Check if row exists
            existing = c.execute(
                "SELECT state, upstream_take_id, upstream_last_frame_sha256 "
                "FROM continuity_states WHERE shot_id = ?",
                (state.shot_id,),
            ).fetchone()

            if existing is None:
                # New state — insert
                self.save(state, conn=c)
                return True

            if existing["state"] == "outdated":
                return False  # Do not reactivate

            if existing["state"] == "current":
                # Same provenance — idempotent
                if (
                    existing["upstream_take_id"] == state.upstream_take_id
                    and existing["upstream_last_frame_sha256"] == state.upstream_last_frame_sha256
                ):
                    return True  # Already current with same provenance

            # New or different UNRESOLVED/CURRENT state — upsert
            self.save(state, conn=c)
            return True

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> ContinuityState:
        return ContinuityState(
            id=row["id"],
            shot_id=row["shot_id"],
            scene_id=row["scene_id"],
            predecessor_shot_id=row["predecessor_shot_id"],
            upstream_take_id=row["upstream_take_id"],
            upstream_take_number=row["upstream_take_number"],
            upstream_last_frame_path=row["upstream_last_frame_path"],
            upstream_last_frame_sha256=row["upstream_last_frame_sha256"],
            state=row["state"],
            continuity_revision=row["continuity_revision"],
            invalidation_reason=row["invalidation_reason"],
            invalidation_source_shot_id=row["invalidation_source_shot_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            invalidated_at=row["invalidated_at"],
        )
