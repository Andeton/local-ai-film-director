"""Tests for WindComicAdapter — TDD: write first, run RED, then implement."""
import sqlite3
import pytest

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.errors import (
    WindComicUnavailableError,
    WindComicNotFoundError,
    WindComicArtifactMalformedError,
    WindComicSchemaError,
)


class TestHealth:
    def test_available(self, wc_db_path):
        assert WindComicAdapter(wc_db_path).health().available is True

    def test_missing_db(self, tmp_path):
        assert WindComicAdapter(str(tmp_path / "no.db")).health().available is False


class TestReadOnly:
    def test_write_rejected(self, wc_db_path):
        conn = WindComicAdapter(wc_db_path)._connect()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO projects (id, title) VALUES ('x','x')")
        conn.close()


class TestSchemaError:
    def test_missing_table_raises_schema_error(self, tmp_path):
        db = str(tmp_path / "bad_schema.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other (x TEXT)")
        conn.commit()
        conn.close()
        with pytest.raises(WindComicSchemaError):
            WindComicAdapter(db).get_project("any")

    def test_missing_column_raises_schema_error(self, tmp_path):
        db = str(tmp_path / "bad_cols.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT)")  # missing columns
        conn.execute("INSERT INTO projects VALUES ('p1', 'T')")
        conn.commit()
        conn.close()
        with pytest.raises(WindComicSchemaError):
            WindComicAdapter(db).get_project("p1")


class TestGetProject:
    def test_returns_dto(self, wc_db_path, wc_project_id):
        p = WindComicAdapter(wc_db_path).get_project(wc_project_id)
        assert p.id == wc_project_id
        assert p.title == "The Abandoned Hospital"

    def test_not_found(self, wc_db_path):
        with pytest.raises(WindComicNotFoundError):
            WindComicAdapter(wc_db_path).get_project("nope")


class TestGetScenes:
    def test_returns_scenes(self, wc_db_path, wc_project_id):
        scenes = WindComicAdapter(wc_db_path).get_scenes(wc_project_id)
        assert len(scenes) == 2

    def test_persistent_url_read(self, wc_db_path, wc_project_id):
        scenes = WindComicAdapter(wc_db_path).get_scenes(wc_project_id)
        lobby = next(s for s in scenes if s.name == "Hospital Lobby")
        assert lobby.persistent_url == "/images/lobby.png"

    def test_empty_project(self, wc_db_path):
        assert WindComicAdapter(wc_db_path).get_scenes("nope") == []


class TestGetCharacters:
    def test_returns_characters(self, wc_db_path, wc_project_id):
        chars = WindComicAdapter(wc_db_path).get_characters(wc_project_id)
        assert len(chars) == 2

    def test_persistent_url_read(self, wc_db_path, wc_project_id):
        chars = WindComicAdapter(wc_db_path).get_characters(wc_project_id)
        det = next(c for c in chars if c.name == "Detective")
        assert det.persistent_url == "/persist/det.png"
        assert det.media_urls == ["ref/det_front.png"]


class TestGetStoryboard:
    def test_ordered_shots(self, wc_db_path, wc_project_id):
        shots = WindComicAdapter(wc_db_path).get_storyboard(wc_project_id)
        assert len(shots) == 2
        assert shots[0].shot_number == 1

    def test_persistent_url_in_storyboard(self, wc_db_path, wc_project_id):
        shots = WindComicAdapter(wc_db_path).get_storyboard(wc_project_id)
        assert shots[1].persistent_url == "/persist/sb2.png"

    def test_empty_project(self, wc_db_path):
        assert WindComicAdapter(wc_db_path).get_storyboard("nope") == []


class TestMalformedData:
    def _make_db_with_bad_scene(self, db_path, data_value):
        conn = sqlite3.connect(db_path)
        conn.executescript(
            "CREATE TABLE projects ("
            "id TEXT PRIMARY KEY, user_id TEXT, title TEXT, status TEXT, script_data TEXT, "
            "director_notes TEXT, style_id TEXT, aspect TEXT, locked_characters TEXT, "
            "primary_character_ref TEXT, mode TEXT"
            "); "
            "CREATE TABLE project_assets ("
            "id TEXT PRIMARY KEY, project_id TEXT, type TEXT, name TEXT, data TEXT, "
            "media_urls TEXT, persistent_url TEXT, shot_number INTEGER, version INTEGER, "
            "confirmed INTEGER, stale INTEGER"
            ");"
        )
        conn.execute(
            "INSERT INTO projects VALUES ('p1','u','T','active',NULL,NULL,NULL,'16:9','[]',NULL,'cinematic')"
        )
        conn.execute(
            "INSERT INTO project_assets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("a1", "p1", "scene", "S", data_value, "[]", None, None, 1, 0, 0),
        )
        conn.commit()
        conn.close()

    def test_corrupt_json(self, tmp_path):
        db = str(tmp_path / "bad.db")
        self._make_db_with_bad_scene(db, "{bad json")
        with pytest.raises(WindComicArtifactMalformedError):
            WindComicAdapter(db).get_scenes("p1")

    def test_wrong_json_type_array_instead_of_dict(self, tmp_path):
        """data field contains a JSON array instead of object -> WindComicArtifactMalformedError."""
        import json
        db = str(tmp_path / "wrong_type.db")
        self._make_db_with_bad_scene(db, json.dumps([1, 2, 3]))
        with pytest.raises(WindComicArtifactMalformedError):
            WindComicAdapter(db).get_scenes("p1")
