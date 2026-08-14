"""M2 API integration tests (M2.H).

Tests all M2 routes via TestClient with fixture WC DB + temp our DB.
Uses FakeLLMProvider to avoid real LLM calls in deterministic tests.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from film_director.config import Settings
from film_director.llm.provider import LLMResponse
from film_director.main import create_app
from tests.fixtures.wind_comic_fixture import TEST_PROJECT_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _beat_response(n: int = 1) -> LLMResponse:
    beats = [
        {
            "dramatic_action": f"Action {i}",
            "character_intention": f"Intent {i}",
            "change": f"Change {i}",
            "characters": ["Detective"],
        }
        for i in range(n)
    ]
    return LLMResponse(content="", parsed={"beats": beats}, model="test")


def _coverage_response(n: int = 1) -> LLMResponse:
    coverage = [
        {
            "shot_type": "establishing",
            "shot_size": "wide",
            "angle": "eye_level",
            "movement": "static",
            "purpose": f"Purpose {i}",
            "duration_sec": 3.0,
        }
        for i in range(n)
    ]
    return LLMResponse(content="", parsed={"coverage": coverage}, model="test")


def _fake_llm_factory():
    """Create a fake LLM provider that can be queued with responses."""
    class FakeLLM:
        def __init__(self):
            self._responses = []
            self.call_count = 0

        def queue(self, resp):
            self._responses.append(resp)

        def chat(self, messages, expect_json=False):
            self.call_count += 1
            return self._responses.pop(0)

        def health(self):
            return True

    return FakeLLM()


@pytest.fixture
def m2_env(tmp_path, wc_db_path):
    """Full-app TestClient + FakeLLM, wired to a WC fixture DB + fresh our DB."""
    fake_llm = _fake_llm_factory()

    s = Settings(
        _env_file=None,
        database_path=str(tmp_path / "our.db"),
        storage_root=str(tmp_path / "storage"),
        wc_database_path=wc_db_path,
    )
    with patch("film_director.main.create_llm_provider", return_value=fake_llm):
        app = create_app(s)
    client = TestClient(app)
    return {"client": client, "llm": fake_llm, "settings": s}


# Need to import TestClient here
from fastapi.testclient import TestClient


def _import_and_enrich(env):
    """Import WC project, then enrich with queued LLM responses."""
    c = env["client"]
    llm = env["llm"]

    imp = c.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
    project_id = imp["project_id"]

    # Queue responses: 2 scenes × 1 beat × 1 coverage = 4 LLM calls
    llm.queue(_beat_response(1))
    llm.queue(_coverage_response(1))
    llm.queue(_beat_response(1))
    llm.queue(_coverage_response(1))

    r = c.post(f"/projects/{project_id}/enrich")
    assert r.status_code == 200, r.text
    return project_id


# ===========================================================================
# Tests
# ===========================================================================


class TestEnrichRoute:
    def test_enrich_creates_beats_shots_plans(self, m2_env):
        """POST /enrich creates beats+shots+plans."""
        project_id = _import_and_enrich(m2_env)
        body = m2_env["client"].post(f"/projects/{project_id}/enrich").json()
        # Second call is idempotent
        assert body["beats_created"] == 0
        assert body["shots_created"] == 0
        assert body["plans_created"] == 0

    def test_enrich_idempotent(self, m2_env):
        """POST /enrich idempotent: second call returns 0/0/0."""
        project_id = _import_and_enrich(m2_env)
        body = m2_env["client"].post(f"/projects/{project_id}/enrich").json()
        assert body["beats_created"] == 0

    def test_enrich_nonexistent_project(self, m2_env):
        """POST /enrich on nonexistent project returns 0 counts (no error)."""
        body = m2_env["client"].post("/projects/nonexistent/enrich").json()
        assert body["beats_created"] == 0


class TestBeatsRoutes:
    def test_get_project_beats(self, m2_env):
        """GET /projects/{id}/beats returns current beats across project."""
        project_id = _import_and_enrich(m2_env)
        r = m2_env["client"].get(f"/projects/{project_id}/beats")
        assert r.status_code == 200
        beats = r.json()
        assert len(beats) == 2  # 2 scenes × 1 beat each

    def test_get_scene_beats(self, m2_env):
        """GET /scenes/{scene_id}/beats returns beats for a scene."""
        project_id = _import_and_enrich(m2_env)
        # Get scenes
        scenes = m2_env["client"].get(f"/projects/{project_id}/scenes").json()
        scene_id = scenes[0]["id"]
        r = m2_env["client"].get(f"/scenes/{scene_id}/beats")
        assert r.status_code == 200
        beats = r.json()
        assert len(beats) == 1

    def test_enrich_scene_beats(self, m2_env):
        """POST /scenes/{scene_id}/enrich-beats creates new beats."""
        c = m2_env["client"]
        llm = m2_env["llm"]

        imp = c.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        project_id = imp["project_id"]

        scenes = c.get(f"/projects/{project_id}/scenes").json()
        scene_id = scenes[0]["id"]

        llm.queue(_beat_response(2))

        r = c.post(f"/scenes/{scene_id}/enrich-beats")
        assert r.status_code == 200
        beats = r.json()
        assert len(beats) == 2

    def test_enrich_beats_force_false_with_human_beat_409(self, m2_env):
        """POST /enrich-beats force=false with human beat → 409."""
        c = m2_env["client"]
        llm = m2_env["llm"]

        imp = c.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        project_id = imp["project_id"]

        scenes = c.get(f"/projects/{project_id}/scenes").json()
        scene_id = scenes[0]["id"]

        # Create initial beats
        llm.queue(_beat_response(1))
        c.post(f"/scenes/{scene_id}/enrich-beats")

        # Get beat id and edit it to human
        beats = c.get(f"/scenes/{scene_id}/beats").json()
        beat_id = beats[0]["id"]
        r = c.put(f"/beats/{beat_id}", json={"dramatic_action": "Human edit"})
        assert r.status_code == 200

        # Try re-enrich without force
        r = c.post(f"/scenes/{scene_id}/enrich-beats?force=false")
        assert r.status_code == 409


class TestShotsRoutes:
    def test_get_project_shots(self, m2_env):
        """GET /projects/{id}/shots returns current shots across project."""
        project_id = _import_and_enrich(m2_env)
        r = m2_env["client"].get(f"/projects/{project_id}/shots")
        assert r.status_code == 200
        shots = r.json()
        assert len(shots) == 2  # 2 scenes × 1 beat × 1 shot each

    def test_get_beat_shots(self, m2_env):
        """GET /beats/{beat_id}/shots returns shots for a beat."""
        project_id = _import_and_enrich(m2_env)
        beats = m2_env["client"].get(f"/projects/{project_id}/beats").json()
        beat_id = beats[0]["id"]
        r = m2_env["client"].get(f"/beats/{beat_id}/shots")
        assert r.status_code == 200
        shots = r.json()
        assert len(shots) == 1

    def test_plan_coverage(self, m2_env):
        """POST /beats/{beat_id}/plan-coverage creates new shots."""
        project_id = _import_and_enrich(m2_env)
        beats = m2_env["client"].get(f"/projects/{project_id}/beats").json()
        beat_id = beats[0]["id"]

        # Re-plan coverage
        m2_env["llm"].queue(_coverage_response(2))
        r = m2_env["client"].post(f"/beats/{beat_id}/plan-coverage?force=true")
        assert r.status_code == 200
        shots = r.json()
        assert len(shots) == 2


class TestGenerationPlanRoutes:
    def test_get_generation_plan(self, m2_env):
        """GET /shots/{shot_id}/generation-plan returns current plan."""
        project_id = _import_and_enrich(m2_env)
        shots = m2_env["client"].get(f"/projects/{project_id}/shots").json()
        shot_id = shots[0]["id"]
        r = m2_env["client"].get(f"/shots/{shot_id}/generation-plan")
        assert r.status_code == 200
        plan = r.json()
        assert "strategy" in plan

    def test_get_generation_plan_404(self, m2_env):
        """GET /shots/{shot_id}/generation-plan → 404 when no plan."""
        r = m2_env["client"].get("/shots/nonexistent/generation-plan")
        assert r.status_code == 404

    def test_assign_strategies(self, m2_env):
        """POST /projects/{id}/assign-strategies creates plans."""
        project_id = _import_and_enrich(m2_env)
        r = m2_env["client"].post(f"/projects/{project_id}/assign-strategies")
        assert r.status_code == 200
        body = r.json()
        assert body["plans_created"] >= 2


class TestApplyChangesM2:
    def test_apply_changes_m1_m2_cascade(self, m2_env, wc_db_path):
        """POST /apply-changes with M1 change detection → M1+M2 cascade."""
        project_id = _import_and_enrich(m2_env)

        # Modify WC data to trigger change detection
        conn = sqlite3.connect(wc_db_path)
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "CHANGED", "location": "X"}),),
        )
        conn.commit()
        conn.close()

        r = m2_env["client"].post(f"/projects/{project_id}/apply-changes")
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] >= 1
        assert "m2_stale_count" in body


class TestRestartPersistence:
    def test_data_survives_restart(self, tmp_path, wc_db_path):
        """Enrich via API, recreate app, GET data survives."""
        fake_llm = _fake_llm_factory()

        s = Settings(
            _env_file=None,
            database_path=str(tmp_path / "our.db"),
            storage_root=str(tmp_path / "storage"),
            wc_database_path=wc_db_path,
        )

        # Session 1: import + enrich
        with patch("film_director.main.create_llm_provider", return_value=fake_llm):
            app1 = create_app(s)
        c1 = TestClient(app1)

        imp = c1.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        project_id = imp["project_id"]

        fake_llm.queue(_beat_response(1))
        fake_llm.queue(_coverage_response(1))
        fake_llm.queue(_beat_response(1))
        fake_llm.queue(_coverage_response(1))
        c1.post(f"/projects/{project_id}/enrich")

        # Session 2: fresh app, same DB
        fake_llm2 = _fake_llm_factory()
        with patch("film_director.main.create_llm_provider", return_value=fake_llm2):
            app2 = create_app(s)
        c2 = TestClient(app2)

        beats = c2.get(f"/projects/{project_id}/beats").json()
        assert len(beats) == 2

        shots = c2.get(f"/projects/{project_id}/shots").json()
        assert len(shots) == 2
