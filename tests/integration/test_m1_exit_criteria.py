"""M1 exit criteria verification tests.

Criterion 1: WC adapter returns DTO, full pipeline produces canonical project
Criterion 2: Storyboard read-through works
Criterion 3: Restart persistence (covered in test_persistence_restart.py)
Criterion 4: Provenance change detection
Criterion 5: JSON parsing proof (unit-level; live Ollama tested in test_ollama_live.py)
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.config import Settings
from film_director.llm.provider import parse_llm_json
from film_director.main import create_app
from film_director.models.wind_comic_dto import WCProject
from tests.fixtures.wind_comic_fixture import TEST_PROJECT_ID


@pytest.fixture
def full_client(tmp_path, wc_db_path):
    s = Settings(
        _env_file=None,
        database_path=str(tmp_path / "our.db"),
        storage_root=str(tmp_path / "storage"),
        wc_database_path=wc_db_path,
    )
    return TestClient(create_app(s))


class TestCriterion1_WCAdapterToCanonicalProject:
    """WC adapter returns DTO; full pipeline produces canonical project."""

    def test_adapter_returns_wc_dto(self, wc_db_path):
        adapter = WindComicAdapter(wc_db_path)
        project = adapter.get_project(TEST_PROJECT_ID)
        assert isinstance(project, WCProject)
        assert project.title == "The Abandoned Hospital"

    def test_full_pipeline_produces_canonical_project(self, full_client):
        imp = full_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        project = full_client.get(f"/projects/{imp['project_id']}").json()
        assert project["title"] == "The Abandoned Hospital"
        assert project["provenance"]["source_system"] == "wind_comic"
        assert len(project["provenance"]["source_hash"]) == 64


class TestCriterion2_StoryboardReadThrough:
    """Storyboard read-through works via API."""

    def test_storyboard_read_through(self, full_client):
        imp = full_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        shots = full_client.get(f"/projects/{imp['project_id']}/storyboard").json()
        assert len(shots) == 2
        assert shots[0]["shot_number"] == 1
        assert shots[1]["shot_number"] == 2
        # Check data fields present
        assert "description" in shots[0]["data"]


class TestCriterion3_RestartPersistence:
    """Restart persistence — see test_persistence_restart.py for full test.
    Quick sanity check here."""

    def test_quick_restart_check(self, tmp_path, wc_db_path):
        db_path = str(tmp_path / "our.db")
        s = Settings(
            _env_file=None,
            database_path=db_path,
            storage_root=str(tmp_path / "storage"),
            wc_database_path=wc_db_path,
        )
        c1 = TestClient(create_app(s))
        imp = c1.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()

        s2 = Settings(
            _env_file=None,
            database_path=db_path,
            storage_root=str(tmp_path / "storage"),
            wc_database_path=wc_db_path,
        )
        c2 = TestClient(create_app(s2))
        assert c2.get(f"/projects/{imp['project_id']}").status_code == 200


class TestCriterion4_ProvenanceChangeDetection:
    """Provenance change detection for scenes and characters."""

    def test_scene_data_change_detected(self, full_client, wc_db_path):
        imp = full_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        pid = imp["project_id"]

        # No changes initially
        assert full_client.get(f"/projects/{pid}/changes").json() == []

        # Modify a scene in WC
        conn = sqlite3.connect(wc_db_path)
        conn.execute(
            "UPDATE project_assets SET data=? WHERE id='asset_scene_001'",
            (json.dumps({"description": "MODIFIED", "location": "NEW"}),),
        )
        conn.commit()
        conn.close()

        changes = full_client.get(f"/projects/{pid}/changes").json()
        scene_changes = [c for c in changes if c["entity_type"] == "scene"]
        assert len(scene_changes) >= 1
        assert scene_changes[0]["change_type"] == "modified"

    def test_character_media_url_change_detected(self, full_client, wc_db_path):
        imp = full_client.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        pid = imp["project_id"]

        conn = sqlite3.connect(wc_db_path)
        conn.execute(
            "UPDATE project_assets SET media_urls=? WHERE id='asset_char_001'",
            (json.dumps(["new_ref.png"]),),
        )
        conn.commit()
        conn.close()

        changes = full_client.get(f"/projects/{pid}/changes").json()
        char_changes = [c for c in changes if c["entity_type"] == "character"]
        assert len(char_changes) >= 1
        assert char_changes[0]["change_type"] == "modified"


class TestCriterion5_JSONParsing:
    """JSON parsing proof — unit-level proxy. Live Ollama tested separately."""

    def test_direct_json(self):
        result = parse_llm_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_fenced_json(self):
        raw = 'Here is the result:\n```json\n{"key": "value"}\n```\n'
        result = parse_llm_json(raw)
        assert result == {"key": "value"}

    def test_embedded_json(self):
        raw = 'Some text {"key": "value"} more text'
        result = parse_llm_json(raw)
        assert result == {"key": "value"}
