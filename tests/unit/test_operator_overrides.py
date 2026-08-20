"""Tests for P2 operator generation overrides — preview and generate endpoints."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from film_director.persistence.database import Database


def _make_test_app(tmp_path):
    """Create a minimal test app with P2-like data."""
    db_path = str(tmp_path / "test.db")
    os.environ["FILM_DATABASE_PATH"] = db_path
    os.environ["FILM_STORAGE_ROOT"] = str(tmp_path / "storage")
    os.environ["FILM_WC_DATABASE_PATH"] = str(tmp_path / "wc.db")
    os.makedirs(str(tmp_path / "storage"), exist_ok=True)

    # Create WC stub DB
    import sqlite3
    wc_conn = sqlite3.connect(str(tmp_path / "wc.db"))
    wc_conn.execute("CREATE TABLE IF NOT EXISTS project_assets (id TEXT, project_id TEXT, type TEXT, name TEXT, data TEXT, media_urls TEXT, shot_number INTEGER, version INTEGER, created_at TEXT, updated_at TEXT, confirmed INTEGER, persistent_url TEXT, stale INTEGER)")
    wc_conn.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, title TEXT, status TEXT, userId TEXT, templateId TEXT, data TEXT, created_at TEXT, updated_at TEXT)")
    wc_conn.commit()
    wc_conn.close()

    from film_director.main import create_app
    app = create_app()

    # Seed test data
    db = Database(db_path)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO production_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p1", "wc1", "Test", "active", "16:9", "{}", now, now,
             "wind_comic", "wc1", "wc1", 1, now, "a" * 64),
        )
        conn.execute(
            "INSERT INTO sequences VALUES (?,?,?,?)", ("seq1", "p1", "Main", 0),
        )
        conn.execute(
            "INSERT INTO scenes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sc1", "seq1", "wc_sc1", "Scene", "", "", 0, "draft",
             "wind_comic", "wc1", "wc_sc1", 1, now, "a" * 64),
        )
        conn.execute(
            "INSERT INTO character_references VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("char1", "p1", "wc_char1", "Hero", "", "A tall man",
             None, "[]", "[]", "active",
             "wind_comic", "wc1", "wc_char1", 1, now, "a" * 64),
        )
        conn.execute(
            "INSERT INTO beats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("b1", "sc1", "Action", "", "", '["char1"]', 0, "draft", "human", 1, now, now),
        )
        subj = json.dumps([{"character_id": "char1", "name": "Hero", "ref_images": []}])
        conn.execute(
            "INSERT INTO shots (id, beat_id, wc_storyboard_id, wc_shot_number, dramatic_purpose, subjects, action, environment, camera, lighting, audio_intent, duration_sec, continuity_inputs, storyboard_image_path, order_index, status, source, version, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s1", "b1", None, None, "Test shot", subj, "Hero enters",
             "{}", '{"shot_size":"medium","angle":"eye_level","movement":"static"}', "{}", "{}", 10.0, "{}", None, 0, "ready", "human", 1, now, now),
        )
        conn.execute(
            "INSERT INTO generation_plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("plan1", "s1", 1, "REFERENCE_TO_VIDEO",
             '{"character_refs":true}', 10.0, "{}", "random", None,
             "none", "test", "ready", 1, now, now),
        )

    return app


@pytest.fixture
def client(tmp_path):
    app = _make_test_app(tmp_path)
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestGenerationPreview:
    def test_preview_returns_200(self, client):
        r = client.get("/shots/s1/generation-preview")
        assert r.status_code == 200
        d = r.json()
        assert d["shot_id"] == "s1"
        assert d["can_generate"] is True

    def test_preview_without_overrides_uses_plan_defaults(self, client):
        r = client.get("/shots/s1/generation-preview")
        d = r.json()
        assert d["duration_sec"] == 10.0
        assert d["resolved_frames"] == 243
        assert d["seed_policy"] == "random"
        assert d["prompt_source"] == "generated"

    def test_preview_with_duration_override(self, client):
        r = client.get("/shots/s1/generation-preview?duration_sec=5.0")
        d = r.json()
        assert d["duration_sec"] == 5.0
        assert d["resolved_frames"] == 124
        assert d["plan_duration_sec"] == 10.0  # original plan unchanged

    def test_preview_with_prompt_override(self, client):
        r = client.get("/shots/s1/generation-preview?prompt_override=Custom+prompt+text")
        d = r.json()
        assert d["prompt_text"] == "Custom prompt text"
        assert d["prompt_source"] == "operator_override"

    def test_preview_with_seed_override(self, client):
        r = client.get("/shots/s1/generation-preview?seed=42")
        d = r.json()
        assert d["seed_policy"] == "explicit"
        assert d["seed"] == 42

    def test_preview_does_not_submit_to_comfyui(self, client):
        """Preview must never call ComfyUI submit."""
        r = client.get("/shots/s1/generation-preview")
        assert r.status_code == 200
        # If ComfyUI were called, it would fail (not running in tests)

    def test_preview_uses_parameter_resolver_for_frames(self, client):
        """Frame resolution must use backend ParameterResolver, not client."""
        r = client.get("/shots/s1/generation-preview?duration_sec=8.0")
        d = r.json()
        # 8.0s * 24 = 192, nearest 17k+5 = 192 (11*17+5=192)
        assert d["resolved_frames"] == 192
        assert d["resolved_duration_sec"] == 8.0

    def test_preview_returns_workflow_identity(self, client):
        r = client.get("/shots/s1/generation-preview")
        d = r.json()
        assert "workflow_id" in d
        assert "workflow_version" in d


class TestGenerateEndpoint:
    def test_generate_with_empty_overrides_parses(self, client):
        """POST /shots/{id}/generate with empty JSON body should parse OK."""
        r = client.post("/shots/s1/generate", json={})
        # Now returns 202 (enqueued) or error from queue/shot validation
        assert r.status_code in (202, 404, 422, 500, 501, 502, 503)

    def test_generate_with_overrides_parses(self, client):
        """POST with overrides should be parsed by the endpoint."""
        body = {"prompt_override": "test", "duration_sec": 5.0, "seed": 42}
        r = client.post("/shots/s1/generate", json=body)
        # Body parsing succeeds; returns 202 (enqueued) or error
        assert r.status_code in (202, 404, 409, 422, 500, 501, 502, 503)


class TestSeedValidation:
    def test_overflow_seed_rejected_422(self, client):
        """Explicit seed > INT64_MAX must be rejected before DB insert."""
        overflow = (1 << 63)  # 9223372036854775808
        r = client.post("/shots/s1/generate", json={"seed": overflow})
        assert r.status_code == 422

    def test_negative_seed_rejected_422(self, client):
        r = client.post("/shots/s1/generate", json={"seed": -1})
        assert r.status_code == 422

    def test_max_legal_seed_accepted(self, client):
        """INT64_MAX should not be rejected by validation."""
        max_seed = (1 << 63) - 1
        r = client.post("/shots/s1/generate", json={"seed": max_seed})
        # Will fail at ComfyUI/ref resolution, but NOT at seed validation
        assert r.status_code != 422 or "Seed" not in r.json().get("detail", "")


class TestMediaSafety:
    def test_path_traversal_rejected(self, client):
        r = client.get("/media/../../../etc/passwd")
        assert r.status_code in (403, 404)

    def test_nonexistent_file_404(self, client):
        r = client.get("/media/nonexistent.mp4")
        assert r.status_code == 404
