"""Unit tests for ContinuityResolver — deterministic predecessor resolution."""
import pytest

from film_director.continuity.continuity_resolver import ContinuityResolver
from film_director.persistence.database import Database


def _setup_scene(conn, scene_id="scene_1", seq_id="seq_1", proj_id="proj_1"):
    """Insert a minimal project→sequence→scene chain."""
    conn.execute(
        "INSERT OR IGNORE INTO production_projects "
        "(id, wc_project_id, title, status, created_at, updated_at, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (proj_id, "wc_1", "Test", "active", "t", "t",
         "wind_comic", "wc_1", "a1", "t", "h1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sequences (id, project_id, name, order_index) VALUES (?,?,?,?)",
        (seq_id, proj_id, "Seq1", 0),
    )
    conn.execute(
        "INSERT OR IGNORE INTO scenes "
        "(id, sequence_id, wc_scene_id, name, order_index, status, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (scene_id, seq_id, "wc_s1", "Scene1", 0, "draft",
         "wind_comic", "wc_1", "a2", "t", "h2"),
    )


def _add_beat(conn, beat_id, scene_id, order_index):
    conn.execute(
        "INSERT INTO beats "
        "(id, scene_id, dramatic_action, order_index, status, source, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (beat_id, scene_id, "action", order_index, "draft", "llm", 1, "t", "t"),
    )


def _add_shot(conn, shot_id, beat_id, order_index, status="draft"):
    conn.execute(
        "INSERT INTO shots "
        "(id, beat_id, dramatic_purpose, order_index, status, source, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (shot_id, beat_id, "purpose", order_index, status, "generated", 1, "t", "t"),
    )


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


class TestSceneShotOrder:
    def test_single_beat_ordered(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b1", 1)
            _add_shot(conn, "s3", "b1", 2)
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("scene_1", conn)
        assert order == ["s1", "s2", "s3"]

    def test_multi_beat_ordered(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_beat(conn, "b2", "scene_1", 1)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b2", 0)
            _add_shot(conn, "s3", "b2", 1)
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("scene_1", conn)
        assert order == ["s1", "s2", "s3"]

    def test_tied_order_index_uses_id(self, db):
        """When order_index ties, ID breaks the tie deterministically."""
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s_beta", "b1", 0)
            _add_shot(conn, "s_alpha", "b1", 0)
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("scene_1", conn)
        assert order == ["s_alpha", "s_beta"]  # alphabetical ID tiebreaker

    def test_tied_beat_order_uses_beat_id(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b_y", "scene_1", 0)
            _add_beat(conn, "b_x", "scene_1", 0)
            _add_shot(conn, "s1", "b_y", 0)
            _add_shot(conn, "s2", "b_x", 0)
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("scene_1", conn)
        assert order == ["s2", "s1"]  # b_x < b_y

    def test_outdated_shots_excluded(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b1", 1, status="outdated")
            _add_shot(conn, "s3", "b1", 2)
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("scene_1", conn)
        assert order == ["s1", "s3"]

    def test_outdated_beats_excluded(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_beat(conn, "b2", "scene_1", 1)
            conn.execute("UPDATE beats SET status = 'outdated' WHERE id = 'b2'")
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b2", 0)
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("scene_1", conn)
        assert order == ["s1"]

    def test_empty_scene(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("scene_1", conn)
        assert order == []

    def test_nonexistent_scene(self, db):
        with db.connection() as conn:
            order = ContinuityResolver.get_scene_shot_order("no_such_scene", conn)
        assert order == []


class TestResolvePredecessor:
    def test_first_shot_no_predecessor(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b1", 1)
        with db.connection() as conn:
            pred = ContinuityResolver.resolve_predecessor("s1", "scene_1", conn)
        assert pred is None

    def test_second_shot_predecessor_is_first(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b1", 1)
        with db.connection() as conn:
            pred = ContinuityResolver.resolve_predecessor("s2", "scene_1", conn)
        assert pred == "s1"

    def test_middle_shot(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b1", 1)
            _add_shot(conn, "s3", "b1", 2)
        with db.connection() as conn:
            pred = ContinuityResolver.resolve_predecessor("s3", "scene_1", conn)
        assert pred == "s2"

    def test_shot_not_in_scene(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
        with db.connection() as conn:
            pred = ContinuityResolver.resolve_predecessor("s_missing", "scene_1", conn)
        assert pred is None

    def test_no_cross_scene(self, db):
        """A shot in scene_2 must not see shots from scene_1 as predecessor."""
        with db.connection() as conn:
            _setup_scene(conn, scene_id="scene_1")
            # scene_2 needs its own unique wc_scene_id
            conn.execute(
                "INSERT INTO scenes "
                "(id, sequence_id, wc_scene_id, name, order_index, status, "
                "prov_source_system, prov_source_project_id, prov_source_asset_id, "
                "prov_imported_at, prov_source_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("scene_2", "seq_1", "wc_s2", "Scene2", 1, "draft",
                 "wind_comic", "wc_1", "a3", "t", "h3"),
            )
            _add_beat(conn, "b1", "scene_1", 0)
            _add_beat(conn, "b2", "scene_2", 0)
            _add_shot(conn, "s1", "b1", 0)  # scene_1
            _add_shot(conn, "s2", "b2", 0)  # scene_2
        with db.connection() as conn:
            pred = ContinuityResolver.resolve_predecessor("s2", "scene_2", conn)
        assert pred is None  # first in its scene


class TestGetDownstreamShots:
    def test_downstream_of_first(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b1", 1)
            _add_shot(conn, "s3", "b1", 2)
        with db.connection() as conn:
            ds = ContinuityResolver.get_downstream_shots("s1", "scene_1", conn)
        assert ds == ["s2", "s3"]

    def test_downstream_of_last(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
            _add_beat(conn, "b1", "scene_1", 0)
            _add_shot(conn, "s1", "b1", 0)
            _add_shot(conn, "s2", "b1", 1)
        with db.connection() as conn:
            ds = ContinuityResolver.get_downstream_shots("s2", "scene_1", conn)
        assert ds == []

    def test_downstream_of_missing(self, db):
        with db.connection() as conn:
            _setup_scene(conn)
        with db.connection() as conn:
            ds = ContinuityResolver.get_downstream_shots("s_missing", "scene_1", conn)
        assert ds == []


class TestGetSceneIdForShot:
    def test_resolves_scene_id(self, db):
        with db.connection() as conn:
            _setup_scene(conn, scene_id="sc_abc")
            _add_beat(conn, "b1", "sc_abc", 0)
            _add_shot(conn, "s1", "b1", 0)
        with db.connection() as conn:
            scene_id = ContinuityResolver.get_scene_id_for_shot("s1", conn)
        assert scene_id == "sc_abc"

    def test_missing_shot_returns_none(self, db):
        with db.connection() as conn:
            scene_id = ContinuityResolver.get_scene_id_for_shot("no_such", conn)
        assert scene_id is None
