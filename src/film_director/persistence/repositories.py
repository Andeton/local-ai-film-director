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
from film_director.generation.generation_request import GenerationRequest, Take
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
                 seed, comfyui_prompt_id, status, submitted_at,
                 completed_at, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            request.id, request.shot_id, request.shot_version,
            request.generation_plan_id, request.generation_plan_version,
            request.prompt_artifact_id, request.prompt_artifact_version,
            request.workflow_definition_id, request.workflow_definition_version,
            request.workflow_template_fingerprint, request.take_number,
            json.dumps(request.parameters_snapshot),
            json.dumps(request.reference_snapshot),
            request.seed, request.comfyui_prompt_id,
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
                 audio_path, last_frame_path, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """
        params = (
            take.id, take.shot_id, take.generation_request_id,
            take.seed, take.video_path, take.audio_path,
            take.last_frame_path, take.status, take.created_at,
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
            created_at=row["created_at"],
        )
