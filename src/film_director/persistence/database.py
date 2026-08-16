"""Our SQLite database — completely separate from Wind Comic's qfmj.db.

Provides schema initialisation and a connection context manager that:
- Enables foreign-key enforcement on every connection
- Enables WAL journal mode for better concurrency
- Commits on success, rolls back on exception, always closes
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS production_projects (
    id                      TEXT PRIMARY KEY,
    wc_project_id           TEXT NOT NULL UNIQUE,
    title                   TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'draft',
    aspect                  TEXT NOT NULL DEFAULT '16:9',
    director_context        TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    prov_source_system      TEXT NOT NULL,
    prov_source_project_id  TEXT NOT NULL,
    prov_source_asset_id    TEXT NOT NULL,
    prov_source_asset_version INTEGER,
    prov_imported_at        TEXT NOT NULL,
    prov_source_hash        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequences (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES production_projects(id)
);

CREATE TABLE IF NOT EXISTS scenes (
    id                      TEXT PRIMARY KEY,
    sequence_id             TEXT NOT NULL,
    wc_scene_id             TEXT NOT NULL,
    name                    TEXT NOT NULL,
    location                TEXT NOT NULL DEFAULT '',
    description             TEXT NOT NULL DEFAULT '',
    order_index             INTEGER NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'draft',
    prov_source_system      TEXT NOT NULL,
    prov_source_project_id  TEXT NOT NULL,
    prov_source_asset_id    TEXT NOT NULL,
    prov_source_asset_version INTEGER,
    prov_imported_at        TEXT NOT NULL,
    prov_source_hash        TEXT NOT NULL,
    UNIQUE(sequence_id, wc_scene_id),
    FOREIGN KEY (sequence_id) REFERENCES sequences(id)
);

CREATE TABLE IF NOT EXISTS character_references (
    id                      TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL,
    wc_character_id         TEXT NOT NULL,
    name                    TEXT NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    appearance              TEXT NOT NULL DEFAULT '',
    face_ref_path           TEXT,
    turnaround_paths        TEXT NOT NULL DEFAULT '[]',
    visual_anchors          TEXT NOT NULL DEFAULT '[]',
    status                  TEXT NOT NULL DEFAULT 'active',
    prov_source_system      TEXT NOT NULL,
    prov_source_project_id  TEXT NOT NULL,
    prov_source_asset_id    TEXT NOT NULL,
    prov_source_asset_version INTEGER,
    prov_imported_at        TEXT NOT NULL,
    prov_source_hash        TEXT NOT NULL,
    UNIQUE(project_id, wc_character_id),
    FOREIGN KEY (project_id) REFERENCES production_projects(id)
);

CREATE TABLE IF NOT EXISTS beats (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL,
    dramatic_action TEXT NOT NULL,
    character_intention TEXT NOT NULL DEFAULT '',
    change TEXT NOT NULL DEFAULT '',
    characters TEXT NOT NULL DEFAULT '[]',
    order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'llm',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (scene_id) REFERENCES scenes(id)
);

CREATE TABLE IF NOT EXISTS shots (
    id TEXT PRIMARY KEY,
    beat_id TEXT NOT NULL,
    wc_storyboard_id TEXT,
    wc_shot_number INTEGER,
    dramatic_purpose TEXT NOT NULL DEFAULT '',
    subjects TEXT NOT NULL DEFAULT '[]',
    action TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '{}',
    camera TEXT NOT NULL DEFAULT '{}',
    lighting TEXT NOT NULL DEFAULT '{}',
    audio_intent TEXT NOT NULL DEFAULT '{}',
    duration_sec REAL NOT NULL DEFAULT 5.0,
    continuity_inputs TEXT NOT NULL DEFAULT '{}',
    storyboard_image_path TEXT,
    order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'generated',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (beat_id) REFERENCES beats(id)
);

CREATE TABLE IF NOT EXISTS generation_plans (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL,
    shot_version INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    reference_requirements TEXT NOT NULL DEFAULT '{}',
    duration_sec REAL NOT NULL,
    resolution_intent TEXT NOT NULL DEFAULT '{}',
    seed_policy TEXT NOT NULL DEFAULT 'random',
    seed INTEGER,
    continuity_mode TEXT NOT NULL DEFAULT 'none',
    selection_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (shot_id) REFERENCES shots(id)
);

CREATE TABLE IF NOT EXISTS h3_prompts (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL,
    generation_plan_id TEXT NOT NULL,
    source_shot_version INTEGER NOT NULL,
    source_generation_plan_version INTEGER NOT NULL,
    subject_definitions TEXT NOT NULL,
    summary TEXT NOT NULL,
    retention_analysis TEXT NOT NULL,
    detailed_description TEXT NOT NULL,
    overall_soundscape TEXT NOT NULL DEFAULT '',
    non_diegetic_music TEXT NOT NULL DEFAULT '',
    rendered_prompt_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'current',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    FOREIGN KEY (generation_plan_id) REFERENCES generation_plans(id)
);

CREATE TABLE IF NOT EXISTS generation_requests (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL,
    shot_version INTEGER NOT NULL,
    generation_plan_id TEXT NOT NULL,
    generation_plan_version INTEGER NOT NULL,
    prompt_artifact_id TEXT NOT NULL,
    prompt_artifact_version INTEGER NOT NULL,
    workflow_definition_id TEXT NOT NULL,
    workflow_definition_version TEXT NOT NULL,
    workflow_template_fingerprint TEXT NOT NULL,
    take_number INTEGER NOT NULL,
    parameters_snapshot TEXT NOT NULL,
    reference_snapshot TEXT NOT NULL,
    seed INTEGER NOT NULL,
    comfyui_prompt_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    submitted_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    error TEXT,
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    FOREIGN KEY (generation_plan_id) REFERENCES generation_plans(id),
    FOREIGN KEY (prompt_artifact_id) REFERENCES h3_prompts(id)
);

CREATE TABLE IF NOT EXISTS takes (
    id TEXT PRIMARY KEY,
    shot_id TEXT NOT NULL,
    generation_request_id TEXT NOT NULL UNIQUE,
    seed INTEGER NOT NULL,
    video_path TEXT NOT NULL,
    audio_path TEXT,
    last_frame_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    FOREIGN KEY (generation_request_id) REFERENCES generation_requests(id)
);
"""


class Database:
    """Lightweight wrapper around our application SQLite database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def init_schema(self) -> None:
        """Create tables if they do not exist. Safe to call multiple times."""
        dir_part = os.path.dirname(self._db_path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
            self._apply_migrations(conn)
        logger.debug("Schema initialised at %s", self._db_path)

    @staticmethod
    def _apply_migrations(conn) -> None:
        """Idempotent schema migrations for existing databases."""
        # M4.B: Add director_context column to production_projects
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(production_projects)").fetchall()
        }
        if "director_context" not in existing:
            conn.execute(
                "ALTER TABLE production_projects ADD COLUMN director_context TEXT NOT NULL DEFAULT '{}'"
            )
            logger.debug("Migration: added director_context to production_projects")

    @contextmanager
    def connection(self):
        """Yield a connection that commits on success and rolls back on error.

        Usage::

            with db.connection() as conn:
                conn.execute("INSERT ...")
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
