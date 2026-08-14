"""Full API integration tests (M1.H).

Tests all routes via TestClient with fixture WC DB + temp our DB.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from film_director.main import create_app
from tests.fixtures.wind_comic_fixture import TEST_PROJECT_ID


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_lightweight_health(self, api_client):
        r = api_client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "integrations" not in body

    def test_wc_health_available(self, api_client):
        r = api_client.get("/integrations/wind-comic/health")
        assert r.status_code == 200
        assert r.json()["available"] is True

    def test_llm_health_no_crash(self, api_client):
        """LLM health endpoint must not crash even without running Ollama."""
        r = api_client.get("/integrations/llm/health")
        assert r.status_code == 200
        body = r.json()
        assert "available" in body
        assert body["provider"] == "ollama"


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

class TestImport:
    def test_import_valid_project(self, api_client):
        r = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}")
        assert r.status_code == 200
        body = r.json()
        assert body["scenes_imported"] == 2
        assert body["characters_imported"] == 2
        assert "project_id" in body

    def test_import_nonexistent_404(self, api_client):
        r = api_client.post("/imports/wind-comic/no_such_project")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class TestProjects:
    def test_list_empty(self, api_client):
        r = api_client.get("/projects")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_after_import(self, api_client):
        api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}")
        r = api_client.get("/projects")
        assert r.status_code == 200
        projects = r.json()
        assert len(projects) == 1
        assert projects[0]["title"] == "The Abandoned Hospital"

    def test_get_project(self, api_client):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        r = api_client.get(f"/projects/{imp['project_id']}")
        assert r.status_code == 200
        assert r.json()["title"] == "The Abandoned Hospital"

    def test_get_missing_project_404(self, api_client):
        r = api_client.get("/projects/nonexistent")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

class TestScenes:
    def test_scenes_after_import(self, api_client):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        r = api_client.get(f"/projects/{imp['project_id']}/scenes")
        assert r.status_code == 200
        scenes = r.json()
        assert len(scenes) == 2
        names = {s["name"] for s in scenes}
        assert "Hospital Exterior" in names

    def test_scenes_missing_project_404(self, api_client):
        r = api_client.get("/projects/nonexistent/scenes")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

class TestCharacters:
    def test_characters_after_import(self, api_client):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        r = api_client.get(f"/projects/{imp['project_id']}/characters")
        assert r.status_code == 200
        chars = r.json()
        assert len(chars) == 2
        det = next(c for c in chars if c["name"] == "Detective")
        assert "ref/det_front.png" in det["turnaround_paths"]

    def test_characters_missing_project_404(self, api_client):
        r = api_client.get("/projects/nonexistent/characters")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Storyboard
# ---------------------------------------------------------------------------

class TestStoryboard:
    def test_storyboard_via_our_id(self, api_client):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        r = api_client.get(f"/projects/{imp['project_id']}/storyboard")
        assert r.status_code == 200
        shots = r.json()
        assert len(shots) == 2
        # Ordered by shot_number
        assert shots[0]["shot_number"] <= shots[1]["shot_number"]

    def test_storyboard_has_persistent_url(self, api_client):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        shots = api_client.get(f"/projects/{imp['project_id']}/storyboard").json()
        sb2 = next(s for s in shots if s["shot_number"] == 2)
        assert sb2["persistent_url"] == "/persist/sb2.png"

    def test_storyboard_raw_wc_id_404(self, api_client):
        """Raw WC project ID must NOT work as project_id."""
        api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}")
        r = api_client.get(f"/projects/{TEST_PROJECT_ID}/storyboard")
        assert r.status_code == 404

    def test_storyboard_missing_project_404(self, api_client):
        r = api_client.get("/projects/nonexistent/storyboard")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------

class TestChanges:
    def test_no_changes_after_clean_import(self, api_client):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        r = api_client.get(f"/projects/{imp['project_id']}/changes")
        assert r.status_code == 200
        assert r.json() == []

    def test_detects_modification(self, api_client, wc_db_path):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        # Modify WC data
        conn = sqlite3.connect(wc_db_path)
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "CHANGED", "location": "X"}),),
        )
        conn.commit()
        conn.close()
        r = api_client.get(f"/projects/{imp['project_id']}/changes")
        assert r.status_code == 200
        changes = r.json()
        assert len(changes) > 0
        assert any(c["change_type"] == "modified" for c in changes)

    def test_changes_missing_project_404(self, api_client):
        r = api_client.get("/projects/nonexistent/changes")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Apply Changes
# ---------------------------------------------------------------------------

class TestApplyChanges:
    def test_apply_marks_outdated(self, api_client, wc_db_path):
        imp = api_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        # Modify WC data
        conn = sqlite3.connect(wc_db_path)
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "CHANGED", "location": "X"}),),
        )
        conn.commit()
        conn.close()
        r = api_client.post(f"/projects/{imp['project_id']}/apply-changes")
        assert r.status_code == 200
        assert r.json()["applied"] >= 1
        # Verify scene is now outdated
        scenes = api_client.get(f"/projects/{imp['project_id']}/scenes").json()
        assert any(s["status"] == "outdated" for s in scenes)

    def test_apply_changes_missing_project_404(self, api_client):
        r = api_client.post("/projects/nonexistent/apply-changes")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Error handling — MAJOR-1 regression guard
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Verify that raw sqlite3 errors do not leak schema details via HTTP."""

    def test_sqlite_integrity_error_returns_500_not_schema(self, api_client):
        """An unhandled sqlite3.IntegrityError must return 500 with a safe body.

        Simulates a repository raising a raw IntegrityError (e.g. FK violation)
        that bypasses the FilmDirectorError hierarchy. The handler added in
        main.py must catch it and return {"error": "Internal database error"}.
        """
        with patch(
            "film_director.persistence.repositories.ProjectRepository.get_project",
            side_effect=sqlite3.IntegrityError(
                "FOREIGN KEY constraint failed: projects.id references sequences"
            ),
        ):
            r = api_client.get("/projects/some_id")

        assert r.status_code == 500
        body = r.json()
        # Must NOT expose internal schema details
        assert "constraint" not in json.dumps(body).lower()
        assert "sqlite" not in json.dumps(body).lower()
        # Must have the generic safe message
        assert body.get("error") == "Internal database error"
        assert body.get("detail") is None

    def test_sqlite_error_handler_registered(self, api_client):
        """The sqlite3.Error exception handler must be registered on the app."""
        # Verify by checking the exception_handlers mapping on the underlying app
        app = api_client.app
        handler_types = {exc_type for exc_type in app.exception_handlers}
        assert sqlite3.Error in handler_types, (
            "sqlite3.Error handler not registered — raw DB errors may leak schema details"
        )
