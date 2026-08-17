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
    continuity_snapshot TEXT,
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
    is_favorite INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    FOREIGN KEY (generation_request_id) REFERENCES generation_requests(id)
);

-- M6.D: at most one approved Take per shot
CREATE UNIQUE INDEX IF NOT EXISTS idx_takes_one_approved_per_shot
    ON takes(shot_id) WHERE status = 'approved';

CREATE TABLE IF NOT EXISTS reference_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    character_id TEXT,
    shot_id TEXT,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    managed_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    source_provenance TEXT NOT NULL,
    source_fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'candidate',
    source_state TEXT NOT NULL DEFAULT 'current',
    pinned INTEGER NOT NULL DEFAULT 0,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES production_projects(id)
);

CREATE INDEX IF NOT EXISTS idx_reference_assets_project ON reference_assets(project_id);
CREATE INDEX IF NOT EXISTS idx_reference_assets_character ON reference_assets(character_id);
CREATE INDEX IF NOT EXISTS idx_reference_assets_shot ON reference_assets(shot_id);
CREATE INDEX IF NOT EXISTS idx_reference_assets_sha ON reference_assets(content_sha256);

CREATE TABLE IF NOT EXISTS reference_generation_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    requested_kind TEXT NOT NULL,
    source_appearance_hash TEXT NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT NOT NULL DEFAULT '',
    workflow_definition_id TEXT NOT NULL,
    workflow_definition_version TEXT NOT NULL,
    workflow_template_fingerprint TEXT NOT NULL,
    parameters_snapshot TEXT NOT NULL DEFAULT '[]',
    seed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES production_projects(id)
);

CREATE INDEX IF NOT EXISTS idx_ref_gen_requests_project ON reference_generation_requests(project_id);
CREATE INDEX IF NOT EXISTS idx_ref_gen_requests_character ON reference_generation_requests(character_id);

CREATE TABLE IF NOT EXISTS reference_generation_executions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    comfyui_prompt_id TEXT,
    output_reference_asset_id TEXT,
    submitted_at TEXT,
    completed_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES reference_generation_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_ref_gen_exec_request ON reference_generation_executions(request_id);

-- M6: Persistent batch idempotency
CREATE TABLE IF NOT EXISTS queue_batches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    takes_count INTEGER NOT NULL,
    base_seed INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES production_projects(id),
    UNIQUE(project_id, idempotency_key)
);

-- M6: Persistent generation queue
CREATE TABLE IF NOT EXISTS generation_queue (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    shot_id TEXT NOT NULL,
    take_number INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    base_seed INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    generation_request_id TEXT,
    take_id TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (batch_id) REFERENCES queue_batches(id),
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    FOREIGN KEY (project_id) REFERENCES production_projects(id),
    UNIQUE(shot_id, take_number)
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON generation_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_shot ON generation_queue(shot_id);
CREATE INDEX IF NOT EXISTS idx_queue_project ON generation_queue(project_id);
CREATE INDEX IF NOT EXISTS idx_queue_batch ON generation_queue(batch_id);

-- M7: Per-shot continuity chain state
CREATE TABLE IF NOT EXISTS continuity_states (
    id                          TEXT PRIMARY KEY,
    shot_id                     TEXT NOT NULL UNIQUE,
    scene_id                    TEXT NOT NULL,
    predecessor_shot_id         TEXT,
    upstream_take_id            TEXT,
    upstream_take_number        INTEGER,
    upstream_last_frame_path    TEXT,
    upstream_last_frame_sha256  TEXT,
    state                       TEXT NOT NULL DEFAULT 'unresolved',
    continuity_revision         INTEGER NOT NULL DEFAULT 0,
    invalidation_reason         TEXT,
    invalidation_source_shot_id TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    invalidated_at              TEXT,
    FOREIGN KEY (shot_id) REFERENCES shots(id),
    FOREIGN KEY (scene_id) REFERENCES scenes(id),
    FOREIGN KEY (predecessor_shot_id) REFERENCES shots(id),
    FOREIGN KEY (upstream_take_id) REFERENCES takes(id)
);

CREATE INDEX IF NOT EXISTS idx_continuity_shot ON continuity_states(shot_id);
CREATE INDEX IF NOT EXISTS idx_continuity_scene ON continuity_states(scene_id);
CREATE INDEX IF NOT EXISTS idx_continuity_predecessor ON continuity_states(predecessor_shot_id);

-- M7.G.A: Visual asset packs and role bindings
CREATE TABLE IF NOT EXISTS visual_asset_packs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    version     INTEGER NOT NULL DEFAULT 1,
    fingerprint TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES production_projects(id)
);

CREATE INDEX IF NOT EXISTS idx_visual_asset_packs_project ON visual_asset_packs(project_id);

CREATE TABLE IF NOT EXISTS asset_role_bindings (
    id                  TEXT PRIMARY KEY,
    pack_id             TEXT NOT NULL,
    reference_asset_id  TEXT NOT NULL,
    role                TEXT NOT NULL,
    order_index         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (pack_id) REFERENCES visual_asset_packs(id),
    FOREIGN KEY (reference_asset_id) REFERENCES reference_assets(id)
);

CREATE INDEX IF NOT EXISTS idx_asset_role_bindings_pack ON asset_role_bindings(pack_id);
CREATE INDEX IF NOT EXISTS idx_asset_role_bindings_asset ON asset_role_bindings(reference_asset_id);
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

        # M6.A: Add is_favorite column to takes
        takes_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(takes)").fetchall()
        }
        if "is_favorite" not in takes_cols:
            conn.execute(
                "ALTER TABLE takes ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0"
            )
            logger.debug("Migration: added is_favorite to takes")

        # M6.D: partial unique index for single-approved-per-shot
        takes_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(takes)").fetchall()
        }
        if "idx_takes_one_approved_per_shot" not in takes_indexes:
            # Verify no duplicate approved Takes exist before creating index
            dups = conn.execute(
                "SELECT shot_id, COUNT(*) as cnt FROM takes "
                "WHERE status = 'approved' GROUP BY shot_id HAVING cnt > 1"
            ).fetchall()
            if dups:
                dup_info = ", ".join(f"{r['shot_id']}({r['cnt']})" for r in dups)
                raise RuntimeError(
                    f"Cannot create single-approved index: duplicate approved Takes: {dup_info}"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_takes_one_approved_per_shot "
                "ON takes(shot_id) WHERE status = 'approved'"
            )
            logger.debug("Migration: added single-approved partial unique index")

        # M6.B: Add base_seed/seed and batch_id to generation_queue
        queue_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "generation_queue" in queue_tables:
            queue_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(generation_queue)").fetchall()
            }
            if "base_seed" not in queue_cols:
                conn.execute(
                    "ALTER TABLE generation_queue ADD COLUMN base_seed INTEGER NOT NULL DEFAULT 0"
                )
                conn.execute(
                    "ALTER TABLE generation_queue ADD COLUMN seed INTEGER NOT NULL DEFAULT 0"
                )
                logger.debug("Migration: added base_seed/seed to generation_queue")
            if "batch_id" not in queue_cols:
                # Add batch_id with default for existing rows
                conn.execute(
                    "ALTER TABLE generation_queue ADD COLUMN batch_id TEXT NOT NULL DEFAULT 'legacy'"
                )
                # Backfill: create legacy batch records for existing jobs
                legacy_projects = {
                    row[0] for row in conn.execute(
                        "SELECT DISTINCT project_id FROM generation_queue WHERE batch_id = 'legacy'"
                    ).fetchall()
                }
                import uuid as _uuid
                for proj_id in legacy_projects:
                    batch_id = f"qb_legacy_{_uuid.uuid4().hex[:8]}"
                    conn.execute(
                        "INSERT OR IGNORE INTO queue_batches "
                        "(id, project_id, scope_type, scope_id, idempotency_key, "
                        "request_fingerprint, takes_count, base_seed, created_at) "
                        "VALUES (?, ?, 'shot', 'legacy', ?, 'legacy', 0, 0, datetime('now'))",
                        (batch_id, proj_id, f"legacy_{proj_id}"),
                    )
                    conn.execute(
                        "UPDATE generation_queue SET batch_id = ? "
                        "WHERE project_id = ? AND batch_id = 'legacy'",
                        (batch_id, proj_id),
                    )
                logger.debug("Migration: added batch_id to generation_queue")

        # M7.A: Add continuity_snapshot column to generation_requests
        gen_req_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "generation_requests" in gen_req_tables:
            gen_req_cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(generation_requests)"
                ).fetchall()
            }
            if "continuity_snapshot" not in gen_req_cols:
                conn.execute(
                    "ALTER TABLE generation_requests "
                    "ADD COLUMN continuity_snapshot TEXT"
                )
                logger.debug("Migration: added continuity_snapshot to generation_requests")

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
