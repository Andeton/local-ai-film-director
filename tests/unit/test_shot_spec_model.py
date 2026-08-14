"""Tests for ShotSubject, CameraIntent, ShotSpecificationV1 canonical models (M2)."""
import pytest
from pydantic import ValidationError

from film_director.models.canonical import CameraIntent, ShotSpecificationV1, ShotSubject


def _minimal_camera(**overrides) -> dict:
    base = {"shot_size": "medium"}
    base.update(overrides)
    return base


def _minimal_shot(**overrides) -> dict:
    base = {
        "id": "shot-1",
        "beat_id": "beat-1",
        "dramatic_purpose": "Establish tension",
        "action": "Character walks into frame",
        "camera": CameraIntent(**_minimal_camera()),
        "order_index": 0,
    }
    base.update(overrides)
    return base


def test_shot_subject_valid():
    subj = ShotSubject(character_id="char-1", name="Alice", ref_images=["img1.png"])
    assert subj.character_id == "char-1"
    assert subj.name == "Alice"
    assert subj.ref_images == ["img1.png"]


def test_shot_subject_ref_images_non_lossy():
    images = ["img1.png", "img2.png", "img3.png"]
    subj = ShotSubject(character_id="char-1", name="Alice", ref_images=images)
    assert subj.ref_images == images, "All ref images must be preserved (non-lossy)"


def test_shot_subject_default_ref_images():
    subj_a = ShotSubject(character_id="char-1", name="Alice")
    subj_b = ShotSubject(character_id="char-2", name="Bob")
    assert subj_a.ref_images == []
    subj_a.ref_images.append("img.png")
    assert subj_b.ref_images == [], "ref_images list must be independent per instance"


def test_camera_intent_valid_shot_sizes():
    valid_sizes = [
        "extreme_wide",
        "wide",
        "medium_wide",
        "medium",
        "medium_close",
        "close_up",
        "extreme_close",
    ]
    for size in valid_sizes:
        cam = CameraIntent(shot_size=size)
        assert cam.shot_size == size


def test_camera_intent_invalid_shot_size():
    with pytest.raises(ValidationError):
        CameraIntent(shot_size="zoom")


def test_shot_spec_minimal():
    shot = ShotSpecificationV1(**_minimal_shot())
    assert shot.status == "draft"
    assert shot.source == "generated"
    assert shot.version == 1
    assert shot.duration_sec == 5.0
    assert shot.subjects == []
    assert shot.environment == {}
    assert shot.wc_storyboard_id is None
    assert shot.wc_shot_number is None
    assert shot.storyboard_image_path is None


def test_shot_spec_with_subjects():
    subjects = [
        ShotSubject(character_id="char-1", name="Alice", ref_images=["a.png"]),
        ShotSubject(character_id="char-2", name="Bob"),
    ]
    shot = ShotSpecificationV1(**_minimal_shot(subjects=subjects))
    assert len(shot.subjects) == 2
    assert shot.subjects[0].name == "Alice"
    assert shot.subjects[1].name == "Bob"


def test_shot_spec_valid_statuses():
    for status in ("draft", "ready", "outdated"):
        shot = ShotSpecificationV1(**_minimal_shot(status=status))
        assert shot.status == status


def test_shot_spec_invalid_status():
    with pytest.raises(ValidationError):
        ShotSpecificationV1(**_minimal_shot(status="approved"))


def test_shot_spec_valid_sources():
    for source in ("generated", "human"):
        shot = ShotSpecificationV1(**_minimal_shot(source=source))
        assert shot.source == source


def test_shot_spec_mutable_default_safety():
    shot_a = ShotSpecificationV1(**_minimal_shot())
    shot_b = ShotSpecificationV1(**_minimal_shot())
    shot_a.subjects.append(ShotSubject(character_id="c", name="X"))
    assert shot_b.subjects == [], "subjects list must be independent per instance"
    shot_a.environment["key"] = "val"
    assert shot_b.environment == {}, "environment dict must be independent per instance"
