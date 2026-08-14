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
        logger.debug("Schema initialised at %s", self._db_path)

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
