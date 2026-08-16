"""Tests for ShotSourceFacts, StoryboardParser, and director_context persistence."""
import json
import os

import pytest

from film_director.models.source_facts import (
    DialogueIntent,
    ShotSourceFacts,
    StoryboardParseResult,
    parse_storyboard_description,
)
from film_director.models.canonical import ProductionProject
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import ProjectRepository


# ---------------------------------------------------------------------------
# ShotSourceFacts
# ---------------------------------------------------------------------------

class TestShotSourceFacts:
    def test_valid_construction(self):
        f = ShotSourceFacts(source_project_id="proj-1")
        assert f.source_project_id == "proj-1"
        assert f.action is None
        assert f.characters == []

    def test_frozen_immutability(self):
        f = ShotSourceFacts(source_project_id="proj-1", action="walks")
        with pytest.raises(Exception):
            f.action = "runs"  # type: ignore

    def test_list_default_not_shared(self):
        a = ShotSourceFacts(source_project_id="p1")
        b = ShotSourceFacts(source_project_id="p2")
        assert a.characters is not b.characters

    def test_all_optional_fields(self):
        f = ShotSourceFacts(
            source_project_id="p",
            source_script_shot_number=3,
            source_storyboard_asset_id="sb-x",
            action="walks",
            emotion="tense",
            dialogue_text="Hello",
            characters=["Alice", "Bob"],
            duration_sec=10.0,
            camera_angle="Close-Up",
            camera_movement="slow push",
            lighting="low key",
            storyboard_description="full prompt text",
            storyboard_image_path="/img.png",
        )
        assert f.source_script_shot_number == 3
        assert f.dialogue_text == "Hello"
        assert f.characters == ["Alice", "Bob"]
        assert f.storyboard_description == "full prompt text"

    def test_storyboard_description_preserved_verbatim(self):
        raw = "  cinematic frame, camera angle: Wide, 中文描述  "
        f = ShotSourceFacts(source_project_id="p", storyboard_description=raw)
        assert f.storyboard_description == raw


# ---------------------------------------------------------------------------
# StoryboardParser
# ---------------------------------------------------------------------------

class TestStoryboardParser:
    def test_camera_angle_marker(self):
        desc = "cinematic frame, camera angle: Close-Up, low angle, lighting: dramatic"
        result = parse_storyboard_description(desc)
        assert result.camera_angle is not None
        assert "Close-Up" in result.camera_angle

    def test_lighting_marker(self):
        desc = "scene, lighting: Low-key dramatic lighting, Rembrandt, strong contrast"
        result = parse_storyboard_description(desc)
        assert result.lighting is not None
        assert "Low-key" in result.lighting

    def test_multiple_markers(self):
        desc = "frame, camera angle: Medium Shot, eye level, lighting: Natural window light, color tone: warm"
        result = parse_storyboard_description(desc)
        assert result.camera_angle is not None
        assert "Medium Shot" in result.camera_angle
        assert result.lighting is not None
        assert "Natural" in result.lighting

    def test_camera_movement_detected(self):
        desc = "camera angle: Wide Shot, slow dolly forward, lighting: daylight"
        result = parse_storyboard_description(desc)
        assert result.camera_movement is not None
        assert "dolly" in result.camera_movement.lower()

    def test_missing_markers(self):
        desc = "A beautiful sunset over the ocean"
        result = parse_storyboard_description(desc)
        assert result.camera_angle is None
        assert result.camera_movement is None
        assert result.lighting is None

    def test_partial_markers(self):
        desc = "camera angle: Wide Shot, some text"
        result = parse_storyboard_description(desc)
        assert result.camera_angle is not None
        assert result.lighting is None

    def test_malformed_empty_marker(self):
        desc = "camera angle: , lighting: "
        result = parse_storyboard_description(desc)
        assert result.camera_angle is None
        assert result.lighting is None

    def test_case_insensitive(self):
        desc = "Camera Angle: birds eye view, Lighting: moonlight"
        result = parse_storyboard_description(desc)
        assert result.camera_angle is not None
        assert result.lighting is not None

    def test_empty_description(self):
        result = parse_storyboard_description("")
        assert result.camera_angle is None
        assert result.lighting is None

    def test_source_not_mutated(self):
        desc = "camera angle: Wide, lighting: dim"
        original = desc
        parse_storyboard_description(desc)
        assert desc == original

    def test_real_wc_storyboard_shot1(self):
        """Real WC storyboard description from test project (shot 1)."""
        desc = (
            "cinematic film frame, 镜头从空旷的广角角度开始, "
            "camera angle: Wide Shot, slow crane up, "
            "lighting: Silhouette backlighting with strong rim light, "
            "color tone: cold steel blue"
        )
        result = parse_storyboard_description(desc)
        assert result.camera_angle is not None
        assert "Wide Shot" in result.camera_angle
        assert result.camera_movement is not None
        assert "crane" in result.camera_movement.lower()
        assert result.lighting is not None
        assert "Silhouette" in result.lighting

    def test_real_wc_storyboard_template(self):
        """Template WC storyboard (shots 2-4 in test project)."""
        desc = (
            "cinematic film frame, 电影写实风格，镜头2。动作。角色表情传达情绪。, "
            "camera angle: Medium Shot, eye level, slow push in, "
            "lighting: Low-key dramatic lighting, Rembrandt, strong contrast, "
            "color tone: muted with selective warm highlights"
        )
        result = parse_storyboard_description(desc)
        assert result.camera_angle is not None
        assert "Medium Shot" in result.camera_angle
        assert result.lighting is not None
        assert "Low-key" in result.lighting


# ---------------------------------------------------------------------------
# ProductionProject.director_context + DB migration
# ---------------------------------------------------------------------------

def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


class TestDirectorContext:
    def test_default_empty_dict(self):
        p = ProductionProject(
            id="p1", wc_project_id="wc1", title="Test",
            created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        )
        assert p.director_context == {}

    def test_non_empty_context(self):
        ctx = {"genre": "mystery", "style": "noir", "story_structure": {"acts": 3, "totalShots": 8}}
        p = ProductionProject(
            id="p1", wc_project_id="wc1", title="Test",
            director_context=ctx,
            created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        )
        assert p.director_context["genre"] == "mystery"
        assert p.director_context["story_structure"]["acts"] == 3

    def test_repository_round_trip(self, tmp_path):
        db = Database(os.path.join(str(tmp_path), "test.db"))
        db.init_schema()
        repo = ProjectRepository(db)
        ctx = {"genre": "thriller", "style": "cinematic"}
        p = ProductionProject(
            id="p1", wc_project_id="wc1", title="Test",
            director_context=ctx,
            created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        )
        repo.save_project(p)
        loaded = repo.get_project("p1")
        assert loaded is not None
        assert loaded.director_context == ctx

    def test_empty_context_round_trip(self, tmp_path):
        db = Database(os.path.join(str(tmp_path), "test.db"))
        db.init_schema()
        repo = ProjectRepository(db)
        p = ProductionProject(
            id="p1", wc_project_id="wc1", title="Test",
            created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        )
        repo.save_project(p)
        loaded = repo.get_project("p1")
        assert loaded.director_context == {}

    def test_fresh_db_has_column(self, tmp_path):
        db = Database(os.path.join(str(tmp_path), "test.db"))
        db.init_schema()
        with db.connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(production_projects)").fetchall()}
        assert "director_context" in cols

    def test_migration_adds_column_to_existing_db(self, tmp_path):
        """Pre-M4 DB gets director_context column via migration."""
        db_path = os.path.join(str(tmp_path), "old.db")
        # Create pre-M4 schema (without director_context)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE production_projects (
                id TEXT PRIMARY KEY,
                wc_project_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                aspect TEXT NOT NULL DEFAULT '16:9',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                prov_source_system TEXT NOT NULL,
                prov_source_project_id TEXT NOT NULL,
                prov_source_asset_id TEXT NOT NULL,
                prov_source_asset_version INTEGER,
                prov_imported_at TEXT NOT NULL,
                prov_source_hash TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO production_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p-old", "wc-old", "Old Project", "active", "16:9",
             "2026-01-01", "2026-01-01", "wc", "p1", "a1", 1, "2026-01-01", "h"),
        )
        conn.commit()
        conn.close()

        # Now open with M4 Database
        db = Database(db_path)
        db.init_schema()

        # Column should exist
        with db.connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(production_projects)").fetchall()}
        assert "director_context" in cols

        # Old row should load with default director_context={}
        repo = ProjectRepository(db)
        loaded = repo.get_project("p-old")
        assert loaded is not None
        assert loaded.director_context == {}
        assert loaded.title == "Old Project"

    def test_migration_idempotent(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        db = Database(db_path)
        db.init_schema()
        db.init_schema()  # second call should not fail
        with db.connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(production_projects)").fetchall()}
        assert "director_context" in cols

    def test_existing_row_survives_migration(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        db = Database(db_path)
        db.init_schema()
        repo = ProjectRepository(db)
        p = ProductionProject(
            id="p1", wc_project_id="wc1", title="Survive",
            created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        )
        repo.save_project(p)

        # Re-init (simulating restart)
        db2 = Database(db_path)
        db2.init_schema()
        loaded = ProjectRepository(db2).get_project("p1")
        assert loaded.title == "Survive"
        assert loaded.director_context == {}
