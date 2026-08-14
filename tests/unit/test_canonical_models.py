"""Tests for canonical Pydantic models."""
import pytest
from pydantic import ValidationError
from film_director.models.canonical import ProductionProject, Sequence, Scene, CharacterReference
from film_director.models.provenance import Provenance


def _prov(**kw) -> Provenance:
    d = dict(
        source_system="wind_comic",
        source_project_id="p",
        source_asset_id="a",
        source_asset_version=1,
        imported_at="t",
        source_hash="a" * 64,
    )
    d.update(kw)
    return Provenance(**d)


def test_project_requires_provenance():
    with pytest.raises(ValidationError):
        ProductionProject(id="p1", wc_project_id="wc1", title="F")


def test_project_with_provenance():
    p = ProductionProject(id="p1", wc_project_id="wc1", title="F", provenance=_prov())
    assert p.status == "draft"
    assert p.provenance.source_system == "wind_comic"


def test_project_valid_statuses():
    for status in ("draft", "active", "outdated"):
        p = ProductionProject(id="p1", wc_project_id="wc1", title="F", provenance=_prov(), status=status)
        assert p.status == status


def test_project_invalid_status():
    with pytest.raises(ValidationError):
        ProductionProject(id="p1", wc_project_id="wc1", title="F", provenance=_prov(), status="unknown")


def test_scene_statuses():
    for status in ("draft", "ready", "outdated"):
        s = Scene(
            id="s1", sequence_id="sq1", wc_scene_id="ws", name="N",
            location="", description="", order_index=0, status=status, provenance=_prov()
        )
        assert s.status == status


def test_scene_invalid_status():
    with pytest.raises(ValidationError):
        Scene(
            id="s1", sequence_id="sq1", wc_scene_id="ws", name="N",
            location="", description="", order_index=0, status="bad", provenance=_prov()
        )


def test_scene_requires_provenance():
    with pytest.raises(ValidationError):
        Scene(id="s1", sequence_id="sq1", wc_scene_id="ws", name="N", location="", description="", order_index=0)


def test_character_statuses():
    for status in ("active", "outdated"):
        c = CharacterReference(
            id="c1", project_id="p1", wc_character_id="wc", name="N",
            description="", appearance="", status=status, provenance=_prov()
        )
        assert c.status == status


def test_character_invalid_status():
    with pytest.raises(ValidationError):
        CharacterReference(
            id="c1", project_id="p1", wc_character_id="wc", name="N",
            description="", appearance="", status="unknown", provenance=_prov()
        )


def test_character_defaults():
    c = CharacterReference(
        id="c1", project_id="p1", wc_character_id="wc", name="N",
        description="", appearance="", provenance=_prov()
    )
    assert c.face_ref_path is None
    assert c.turnaround_paths == []
    assert c.visual_anchors == []
    assert c.status == "active"


def test_character_mutable_default_safety():
    c1 = CharacterReference(id="c1", project_id="p1", wc_character_id="wc", name="N", description="", appearance="", provenance=_prov())
    c2 = CharacterReference(id="c2", project_id="p1", wc_character_id="wc", name="N", description="", appearance="", provenance=_prov())
    # Modifying c1's list should not affect c2's
    c1.turnaround_paths.append("x")
    assert c2.turnaround_paths == []


def test_character_requires_provenance():
    with pytest.raises(ValidationError):
        CharacterReference(id="c1", project_id="p1", wc_character_id="wc", name="N", description="", appearance="")


def test_sequence_no_provenance():
    s = Sequence(id="sq1", project_id="p1", name="Main", order_index=0)
    assert s.order_index == 0


def test_sequence_has_no_provenance_field():
    s = Sequence(id="sq1", project_id="p1", name="Main", order_index=0)
    assert not hasattr(s, "provenance")
