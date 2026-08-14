"""M2 human editing integration tests (M2.H).

Tests PUT /beats/{id} and PUT /shots/{id} human edit routes,
stale propagation, and edit→re-enrich lifecycle.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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
    class FakeLLM:
        def __init__(self):
            self._responses = []

        def queue(self, resp):
            self._responses.append(resp)

        def chat(self, messages, expect_json=False):
            return self._responses.pop(0)

        def health(self):
            return True

    return FakeLLM()


@pytest.fixture
def m2_env(tmp_path, wc_db_path):
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


def _import_and_enrich(env):
    c = env["client"]
    llm = env["llm"]

    imp = c.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
    project_id = imp["project_id"]

    llm.queue(_beat_response(1))
    llm.queue(_coverage_response(1))
    llm.queue(_beat_response(1))
    llm.queue(_coverage_response(1))

    c.post(f"/projects/{project_id}/enrich")
    return project_id


# ===========================================================================
# Beat Edit Tests
# ===========================================================================


class TestBeatEdit:
    def test_edit_beat_source_human_version_incremented(self, m2_env):
        """PUT /beats/{id} → source=human, version+1, dependent shots outdated."""
        project_id = _import_and_enrich(m2_env)
        c = m2_env["client"]

        beats = c.get(f"/projects/{project_id}/beats").json()
        beat_id = beats[0]["id"]

        # Get shots before edit
        shots_before = c.get(f"/beats/{beat_id}/shots").json()
        assert len(shots_before) == 1

        r = c.put(f"/beats/{beat_id}", json={"dramatic_action": "Human-written action"})
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "human"
        assert body["version"] == 2
        assert body["dramatic_action"] == "Human-written action"

        # Dependent shots should be outdated
        shots_after = c.get(f"/beats/{beat_id}/shots").json()
        assert len(shots_after) == 0  # all outdated by stale propagation

    def test_edit_beat_empty_body_422(self, m2_env):
        """PUT /beats/{id} with empty body → 422."""
        project_id = _import_and_enrich(m2_env)
        c = m2_env["client"]

        beats = c.get(f"/projects/{project_id}/beats").json()
        beat_id = beats[0]["id"]

        r = c.put(f"/beats/{beat_id}", json={})
        assert r.status_code == 422

    def test_edit_beat_nonexistent_404(self, m2_env):
        """PUT /beats/{id} nonexistent → 404."""
        r = m2_env["client"].put("/beats/nonexistent", json={"dramatic_action": "x"})
        assert r.status_code == 404

    def test_edit_beat_outdated_404(self, m2_env):
        """PUT /beats/{id} outdated → 404."""
        project_id = _import_and_enrich(m2_env)
        c = m2_env["client"]

        beats = c.get(f"/projects/{project_id}/beats").json()
        beat_id = beats[0]["id"]
        scene_id = beats[0]["scene_id"]

        # Re-enrich to make old beat outdated
        m2_env["llm"].queue(_beat_response(1))
        c.post(f"/scenes/{scene_id}/enrich-beats?force=true")

        # Old beat should be outdated; try to edit it
        r = c.put(f"/beats/{beat_id}", json={"dramatic_action": "x"})
        assert r.status_code == 404


# ===========================================================================
# Shot Edit Tests
# ===========================================================================


class TestShotEdit:
    def test_edit_shot_source_human_version_incremented(self, m2_env):
        """PUT /shots/{id} → source=human, version+1, plan outdated."""
        project_id = _import_and_enrich(m2_env)
        c = m2_env["client"]

        shots = c.get(f"/projects/{project_id}/shots").json()
        shot_id = shots[0]["id"]

        r = c.put(f"/shots/{shot_id}", json={"dramatic_purpose": "Human purpose"})
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "human"
        assert body["version"] == 2
        assert body["dramatic_purpose"] == "Human purpose"

    def test_edit_shot_then_plan_404(self, m2_env):
        """PUT /shots/{id} then GET generation-plan → 404 (version mismatch)."""
        project_id = _import_and_enrich(m2_env)
        c = m2_env["client"]

        shots = c.get(f"/projects/{project_id}/shots").json()
        shot_id = shots[0]["id"]

        # Plan exists before edit
        r = c.get(f"/shots/{shot_id}/generation-plan")
        assert r.status_code == 200

        # Edit shot
        c.put(f"/shots/{shot_id}", json={"dramatic_purpose": "Edited"})

        # Plan should no longer match (version mismatch or outdated)
        r = c.get(f"/shots/{shot_id}/generation-plan")
        assert r.status_code == 404

    def test_edit_shot_then_assign_strategies_new_plan(self, m2_env):
        """PUT /shots/{id} then assign-strategies → new plan for new version."""
        project_id = _import_and_enrich(m2_env)
        c = m2_env["client"]

        shots = c.get(f"/projects/{project_id}/shots").json()
        shot_id = shots[0]["id"]

        # Edit shot
        c.put(f"/shots/{shot_id}", json={"dramatic_purpose": "Edited"})

        # Re-assign strategies
        r = c.post(f"/projects/{project_id}/assign-strategies")
        assert r.status_code == 200

        # Plan should exist now for the new shot version
        r = c.get(f"/shots/{shot_id}/generation-plan")
        assert r.status_code == 200
        plan = r.json()
        assert plan["shot_version"] == 2

    def test_edit_shot_nonexistent_404(self, m2_env):
        """PUT /shots/{id} nonexistent → 404."""
        r = m2_env["client"].put("/shots/nonexistent", json={"dramatic_purpose": "x"})
        assert r.status_code == 404


# ===========================================================================
# Full lifecycle test
# ===========================================================================


class TestEditLifecycle:
    def test_edit_beat_then_reenrich_rejected_then_force(self, m2_env):
        """Full lifecycle: edit beat → re-enrich rejected (409) → force → old preserved."""
        project_id = _import_and_enrich(m2_env)
        c = m2_env["client"]

        beats = c.get(f"/projects/{project_id}/beats").json()
        beat_id = beats[0]["id"]
        scene_id = beats[0]["scene_id"]

        # Edit beat to human
        c.put(f"/beats/{beat_id}", json={"dramatic_action": "Human action"})

        # Re-enrich without force → 409
        r = c.post(f"/scenes/{scene_id}/enrich-beats?force=false")
        assert r.status_code == 409

        # Force re-enrich → succeeds
        m2_env["llm"].queue(_beat_response(1))
        r = c.post(f"/scenes/{scene_id}/enrich-beats?force=true")
        assert r.status_code == 200
        new_beats = r.json()
        assert len(new_beats) == 1

        # Old beat should still be in the system but outdated
        # (we can verify via getting all scene beats - the new one is returned)
        current_beats = c.get(f"/scenes/{scene_id}/beats").json()
        assert len(current_beats) == 1
        assert current_beats[0]["id"] != beat_id
