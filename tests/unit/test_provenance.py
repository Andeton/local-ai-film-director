"""Tests for provenance hashing and source payload builders."""
from film_director.models.provenance import (
    Provenance,
    compute_source_hash,
    build_scene_source_payload,
    build_character_source_payload,
    build_project_source_payload,
)
from film_director.models.wind_comic_dto import WCScene, WCCharacter, WCProject


def test_hash_deterministic():
    d = {"a": 1, "b": 2}
    assert compute_source_hash(d) == compute_source_hash(d)
    assert len(compute_source_hash(d)) == 64


def test_hash_key_order_independent():
    assert compute_source_hash({"z": 1, "a": 2}) == compute_source_hash({"a": 2, "z": 1})


def test_hash_detects_change():
    assert compute_source_hash({"x": 1}) != compute_source_hash({"x": 2})


def test_hash_unicode():
    assert len(compute_source_hash({"n": "探偵"})) == 64


def test_scene_payload_detects_description_change():
    s1 = WCScene(asset_id="s1", project_id="p1", name="X", data={"description": "dark"}, media_urls=[], persistent_url=None, version=1)
    s2 = WCScene(asset_id="s1", project_id="p1", name="X", data={"description": "bright"}, media_urls=[], persistent_url=None, version=1)
    assert compute_source_hash(build_scene_source_payload(s1)) != compute_source_hash(build_scene_source_payload(s2))


def test_scene_payload_detects_name_change():
    s1 = WCScene(asset_id="s1", project_id="p1", name="Ext", data={}, media_urls=[], persistent_url=None, version=1)
    s2 = WCScene(asset_id="s1", project_id="p1", name="Int", data={}, media_urls=[], persistent_url=None, version=1)
    assert compute_source_hash(build_scene_source_payload(s1)) != compute_source_hash(build_scene_source_payload(s2))


def test_scene_payload_detects_persistent_url_change():
    s1 = WCScene(asset_id="s1", project_id="p1", name="X", data={}, media_urls=[], persistent_url="old.png", version=1)
    s2 = WCScene(asset_id="s1", project_id="p1", name="X", data={}, media_urls=[], persistent_url="new.png", version=1)
    assert compute_source_hash(build_scene_source_payload(s1)) != compute_source_hash(build_scene_source_payload(s2))


def test_character_payload_detects_appearance_change():
    c1 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={"appearance": "tall"}, media_urls=[], persistent_url=None, version=1)
    c2 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={"appearance": "short"}, media_urls=[], persistent_url=None, version=1)
    assert compute_source_hash(build_character_source_payload(c1)) != compute_source_hash(build_character_source_payload(c2))


def test_character_payload_detects_media_url_change():
    c1 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=["a.png"], persistent_url=None, version=1)
    c2 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=["b.png"], persistent_url=None, version=1)
    assert compute_source_hash(build_character_source_payload(c1)) != compute_source_hash(build_character_source_payload(c2))


def test_character_payload_detects_persistent_url_change():
    c1 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=[], persistent_url="old.png", version=1)
    c2 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=[], persistent_url="new.png", version=1)
    assert compute_source_hash(build_character_source_payload(c1)) != compute_source_hash(build_character_source_payload(c2))


def test_project_payload_detects_title_change():
    p1 = WCProject(id="p1", title="A", status="active", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    p2 = WCProject(id="p1", title="B", status="active", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    assert compute_source_hash(build_project_source_payload(p1)) != compute_source_hash(build_project_source_payload(p2))


def test_project_payload_ignores_wc_status():
    p1 = WCProject(id="p1", title="F", status="active", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    p2 = WCProject(id="p1", title="F", status="completed", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    assert compute_source_hash(build_project_source_payload(p1)) == compute_source_hash(build_project_source_payload(p2))


def test_project_payload_detects_aspect_change():
    p1 = WCProject(id="p1", title="F", status="active", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    p2 = WCProject(id="p1", title="F", status="active", aspect="9:16", style_id=None, script_data=None, locked_characters=[])
    assert compute_source_hash(build_project_source_payload(p1)) != compute_source_hash(build_project_source_payload(p2))
