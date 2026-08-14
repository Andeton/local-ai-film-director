"""Tests for ShotSpecBuilder (M2.D) — purely deterministic, no mock LLM needed."""
import copy

import pytest

from film_director.models.canonical import (
    Beat, Scene, CharacterReference, ShotSpecificationV1,
    ShotSubject, CameraIntent,
)
from film_director.models.provenance import Provenance
from film_director.models.wind_comic_dto import WCStoryboardShot
from film_director.enrichment.coverage_planner import CoverageDecision
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provenance() -> Provenance:
    return Provenance(
        source_system="wind_comic",
        source_project_id="proj-001",
        source_asset_id="asset-001",
        source_asset_version=1,
        imported_at="2024-01-01T00:00:00+00:00",
        source_hash="a" * 64,
    )


def _make_scene(**overrides) -> Scene:
    base = dict(
        id="scene-abc123",
        sequence_id="seq-001",
        wc_scene_id="wc-scene-001",
        name="The Confrontation",
        location="Rooftop at night",
        description="Hero faces villain in final showdown",
        order_index=0,
        status="draft",
        provenance=_make_provenance(),
    )
    base.update(overrides)
    return Scene(**base)


def _make_beat(**overrides) -> Beat:
    base = dict(
        id="beat-abc123",
        scene_id="scene-abc123",
        dramatic_action="Hero confronts villain",
        character_intention="Protect the team",
        change="Fear turns to resolve",
        characters=["Hero", "Villain"],
        order_index=0,
        status="draft",
        source="llm",
        version=1,
    )
    base.update(overrides)
    return Beat(**base)


def _make_coverage(**overrides) -> CoverageDecision:
    base = dict(
        shot_type="establishing",
        shot_size="wide",
        angle="high",
        movement="pan_left",
        purpose="Set the scene",
        duration_sec=4.0,
    )
    base.update(overrides)
    return CoverageDecision(**base)


def _make_char_ref(**overrides) -> CharacterReference:
    base = dict(
        id="char-001",
        project_id="proj-001",
        wc_character_id="wc-char-001",
        name="Hero",
        description="The main protagonist",
        appearance="Tall, dark hair",
        face_ref_path="/refs/hero_face.png",
        turnaround_paths=["/refs/hero_front.png", "/refs/hero_side.png"],
        visual_anchors=["scar on left cheek"],
        status="active",
        provenance=_make_provenance(),
    )
    base.update(overrides)
    return CharacterReference(**base)


def _make_storyboard(**overrides) -> WCStoryboardShot:
    base = dict(
        asset_id="sb-001",
        project_id="proj-001",
        shot_number=1,
        data={},
        media_urls=["http://example.com/sb1.png"],
        persistent_url="http://cdn.example.com/sb1.png",
        version=1,
    )
    base.update(overrides)
    return WCStoryboardShot(**base)


# ---------------------------------------------------------------------------
# 1. One coverage -> one shot
# ---------------------------------------------------------------------------

def test_one_coverage_one_shot():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    assert len(result) == 1
    assert isinstance(result[0], ShotSpecificationV1)


# ---------------------------------------------------------------------------
# 2. Multiple coverage -> correct order_index from order_start
# ---------------------------------------------------------------------------

def test_multiple_coverage_order_index():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(), _make_coverage(), _make_coverage()],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
        order_start=5,
    )
    assert len(result) == 3
    assert [s.order_index for s in result] == [5, 6, 7]


# ---------------------------------------------------------------------------
# 3. Unique shot IDs
# ---------------------------------------------------------------------------

def test_unique_shot_ids():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(), _make_coverage()],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    ids = [s.id for s in result]
    assert len(set(ids)) == 2
    for sid in ids:
        assert sid.startswith("shot")


# ---------------------------------------------------------------------------
# 4. dramatic_purpose from coverage.purpose
# ---------------------------------------------------------------------------

def test_dramatic_purpose_from_coverage():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(purpose="Reveal the twist")],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].dramatic_purpose == "Reveal the twist"


# ---------------------------------------------------------------------------
# 5. action from beat.dramatic_action
# ---------------------------------------------------------------------------

def test_action_from_beat():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(dramatic_action="Hero leaps"),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].action == "Hero leaps"


# ---------------------------------------------------------------------------
# 6. CameraIntent correct
# ---------------------------------------------------------------------------

def test_camera_intent_correct():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(shot_size="close_up", angle="low", movement="dolly_in")],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    cam = result[0].camera
    assert isinstance(cam, CameraIntent)
    assert cam.shot_size == "close_up"
    assert cam.angle == "low"
    assert cam.movement == "dolly_in"


# ---------------------------------------------------------------------------
# 7. environment from scene
# ---------------------------------------------------------------------------

def test_environment_from_scene():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(location="Dark alley", description="Rain-soaked streets"),
    )
    env = result[0].environment
    assert env["location"] == "Dark alley"
    assert env["description"] == "Rain-soaked streets"


# ---------------------------------------------------------------------------
# 8. duration from coverage.duration_sec
# ---------------------------------------------------------------------------

def test_duration_from_coverage():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(duration_sec=7.5)],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].duration_sec == 7.5


# ---------------------------------------------------------------------------
# 9. Character name resolved (casefold + strip match)
# ---------------------------------------------------------------------------

def test_character_resolved_casefold():
    builder = ShotSpecBuilder()
    char = _make_char_ref(id="char-hero", name="Hero")
    result = builder.build_shots(
        beat=_make_beat(characters=["  hero  "]),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[char],
        scene=_make_scene(),
    )
    subjects = result[0].subjects
    assert len(subjects) == 1
    assert subjects[0].character_id == "char-hero"
    assert subjects[0].name == "Hero"


# ---------------------------------------------------------------------------
# 10. ALL turnaround_paths in ShotSubject.ref_images
# ---------------------------------------------------------------------------

def test_all_turnaround_paths_in_ref_images():
    builder = ShotSpecBuilder()
    paths = ["/ref/front.png", "/ref/side.png", "/ref/back.png"]
    char = _make_char_ref(id="char-hero", name="Hero", turnaround_paths=paths)
    result = builder.build_shots(
        beat=_make_beat(characters=["Hero"]),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[char],
        scene=_make_scene(),
    )
    assert result[0].subjects[0].ref_images == paths


# ---------------------------------------------------------------------------
# 11. Duplicate beat character name -> single ShotSubject
# ---------------------------------------------------------------------------

def test_duplicate_beat_character_single_subject():
    builder = ShotSpecBuilder()
    char = _make_char_ref(id="char-hero", name="Hero")
    result = builder.build_shots(
        beat=_make_beat(characters=["Hero", "hero", "HERO"]),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[char],
        scene=_make_scene(),
    )
    assert len(result[0].subjects) == 1


# ---------------------------------------------------------------------------
# 12. Unresolved character -> omitted, no fake ID
# ---------------------------------------------------------------------------

def test_unresolved_character_omitted():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(characters=["Unknown"]),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    assert len(result[0].subjects) == 0


# ---------------------------------------------------------------------------
# 13. Ambiguous duplicate CharacterReference names -> unresolved
# ---------------------------------------------------------------------------

def test_ambiguous_character_refs_omitted():
    builder = ShotSpecBuilder()
    char1 = _make_char_ref(id="char-a", name="Hero")
    char2 = _make_char_ref(id="char-b", name="hero")
    result = builder.build_shots(
        beat=_make_beat(characters=["Hero"]),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[char1, char2],
        scene=_make_scene(),
    )
    assert len(result[0].subjects) == 0


# ---------------------------------------------------------------------------
# 14. Storyboard paired positionally
# ---------------------------------------------------------------------------

def test_storyboard_paired_positionally():
    builder = ShotSpecBuilder()
    sb = _make_storyboard(asset_id="sb-42", shot_number=7)
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=[sb],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].wc_storyboard_id == "sb-42"
    assert result[0].wc_shot_number == 7


# ---------------------------------------------------------------------------
# 15. storyboard_image_path: persistent_url preferred, then media_urls[0], then None
# ---------------------------------------------------------------------------

def test_storyboard_image_path_persistent_preferred():
    builder = ShotSpecBuilder()
    sb = _make_storyboard(
        persistent_url="http://cdn/persist.png",
        media_urls=["http://cdn/media.png"],
    )
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=[sb],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].storyboard_image_path == "http://cdn/persist.png"


def test_storyboard_image_path_media_fallback():
    builder = ShotSpecBuilder()
    sb = _make_storyboard(
        persistent_url=None,
        media_urls=["http://cdn/media.png"],
    )
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=[sb],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].storyboard_image_path == "http://cdn/media.png"


def test_storyboard_image_path_none():
    builder = ShotSpecBuilder()
    sb = _make_storyboard(persistent_url=None, media_urls=[])
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=[sb],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].storyboard_image_path is None


# ---------------------------------------------------------------------------
# 16. Storyboard duration overrides coverage duration
# ---------------------------------------------------------------------------

def test_storyboard_duration_overrides_coverage():
    builder = ShotSpecBuilder()
    sb = _make_storyboard(data={"duration": 9.0})
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(duration_sec=4.0)],
        storyboard_shots=[sb],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].duration_sec == 9.0


def test_storyboard_non_positive_duration_ignored():
    builder = ShotSpecBuilder()
    sb = _make_storyboard(data={"duration": -1})
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(duration_sec=4.0)],
        storyboard_shots=[sb],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].duration_sec == 4.0


# ---------------------------------------------------------------------------
# 17. Fewer storyboard than coverage -> remaining shots no WC linkage
# ---------------------------------------------------------------------------

def test_fewer_storyboard_than_coverage():
    builder = ShotSpecBuilder()
    sb = _make_storyboard(asset_id="sb-1", shot_number=1)
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage(), _make_coverage()],
        storyboard_shots=[sb],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].wc_storyboard_id == "sb-1"
    assert result[1].wc_storyboard_id is None
    assert result[1].wc_shot_number is None
    assert result[1].storyboard_image_path is None


# ---------------------------------------------------------------------------
# 18. More storyboard than coverage -> no extra shots
# ---------------------------------------------------------------------------

def test_more_storyboard_than_coverage():
    builder = ShotSpecBuilder()
    sbs = [_make_storyboard(asset_id=f"sb-{i}", shot_number=i) for i in range(5)]
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=sbs,
        characters=[],
        scene=_make_scene(),
    )
    assert len(result) == 1
    assert result[0].wc_storyboard_id == "sb-0"


# ---------------------------------------------------------------------------
# 19. lighting={}, audio_intent={}, continuity_inputs={} (not fabricated)
# ---------------------------------------------------------------------------

def test_empty_dicts_not_fabricated():
    builder = ShotSpecBuilder()
    result = builder.build_shots(
        beat=_make_beat(),
        coverage=[_make_coverage()],
        storyboard_shots=[],
        characters=[],
        scene=_make_scene(),
    )
    assert result[0].lighting == {}
    assert result[0].audio_intent == {}
    assert result[0].continuity_inputs == {}


# ---------------------------------------------------------------------------
# 20. status=draft, source=generated, version=1, no input mutation
# ---------------------------------------------------------------------------

def test_status_source_version_and_no_mutation():
    builder = ShotSpecBuilder()
    beat = _make_beat()
    scene = _make_scene()
    coverage = [_make_coverage()]
    beat_copy = copy.deepcopy(beat)
    scene_copy = copy.deepcopy(scene)

    result = builder.build_shots(
        beat=beat,
        coverage=coverage,
        storyboard_shots=[],
        characters=[],
        scene=scene,
    )
    assert result[0].status == "draft"
    assert result[0].source == "generated"
    assert result[0].version == 1

    # No input mutation
    assert beat.id == beat_copy.id
    assert beat.dramatic_action == beat_copy.dramatic_action
    assert scene.name == scene_copy.name
    assert scene.location == scene_copy.location
