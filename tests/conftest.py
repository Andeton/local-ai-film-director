"""Shared test fixtures."""
import pytest
from fastapi.testclient import TestClient

from film_director.config import Settings
from film_director.main import create_app
from tests.fixtures.wind_comic_fixture import create_fixture_db, TEST_PROJECT_ID


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "test.db"),
        storage_root=str(tmp_path / "storage"),
        wc_database_path=str(tmp_path / "wc_test.db"),
    )


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def wc_db_path(tmp_path):
    p = tmp_path / "qfmj.db"
    create_fixture_db(p)
    return str(p)


@pytest.fixture
def wc_project_id():
    return TEST_PROJECT_ID
