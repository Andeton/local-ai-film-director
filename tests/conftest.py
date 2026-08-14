"""Shared test fixtures."""
import pytest
from fastapi.testclient import TestClient

from film_director.config import Settings
from film_director.main import create_app


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
