"""Persistence restart test — data survives app recreation (M1 exit criterion 3)."""
from fastapi.testclient import TestClient

from film_director.config import Settings
from film_director.main import create_app
from tests.fixtures.wind_comic_fixture import TEST_PROJECT_ID


class TestPersistenceRestart:
    def test_data_survives_app_restart(self, tmp_path, wc_db_path):
        db_path = str(tmp_path / "our.db")

        # --- App A: import ---
        settings_a = Settings(
            _env_file=None,
            database_path=db_path,
            storage_root=str(tmp_path / "storage"),
            wc_database_path=wc_db_path,
        )
        client_a = TestClient(create_app(settings_a))
        imp = client_a.post(f"/imports/wind-comic/{TEST_PROJECT_ID}").json()
        project_id = imp["project_id"]

        # --- App B: new app, same DB file ---
        settings_b = Settings(
            _env_file=None,
            database_path=db_path,
            storage_root=str(tmp_path / "storage"),
            wc_database_path=wc_db_path,
        )
        client_b = TestClient(create_app(settings_b))

        # Project still exists
        r = client_b.get(f"/projects/{project_id}")
        assert r.status_code == 200
        assert r.json()["title"] == "The Abandoned Hospital"

        # Scenes still exist
        scenes = client_b.get(f"/projects/{project_id}/scenes").json()
        assert len(scenes) == 2

        # Characters still exist
        chars = client_b.get(f"/projects/{project_id}/characters").json()
        assert len(chars) == 2
