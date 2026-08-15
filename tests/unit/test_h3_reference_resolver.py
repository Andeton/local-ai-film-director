"""Tests for H3ReferenceResolver — minimal M3 reference resolution."""
import dataclasses
import hashlib
import os
import tempfile

import pytest

from film_director.errors import ReferenceResolutionError
from film_director.generation.h3_reference_resolver import H3ReferenceResolver
from film_director.generation.h3_types import H3ReferenceBinding
from film_director.models.canonical import (
    CameraIntent,
    CharacterReference,
    GenerationPlan,
    ReferenceRequirements,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _provenance() -> Provenance:
    return Provenance(
        source_system="test",
        source_project_id="proj-1",
        source_asset_id="test-1",
        source_asset_version=1,
        imported_at="2026-01-01T00:00:00",
        source_hash="abc123",
    )


def _char(cid: str, name: str, appearance: str = "dark hair, tall") -> CharacterReference:
    return CharacterReference(
        id=cid,
        project_id="proj-1",
        wc_character_id=f"wc-{cid}",
        name=name,
        description=f"{name} description",
        appearance=appearance,
        provenance=_provenance(),
    )


def _subject(cid: str, name: str, ref_images: list[str] | None = None) -> ShotSubject:
    return ShotSubject(character_id=cid, name=name, ref_images=ref_images or [])


def _shot(subjects: list[ShotSubject], **kw) -> ShotSpecificationV1:
    defaults = dict(
        id="shot-1",
        beat_id="beat-1",
        dramatic_purpose="tension",
        action="walks forward",
        camera=CameraIntent(shot_size="medium"),
        order_index=0,
    )
    defaults.update(kw)
    defaults["subjects"] = subjects
    return ShotSpecificationV1(**defaults)


def _write_file(tmp_path, name: str, content: bytes = b"fake image") -> str:
    p = os.path.join(str(tmp_path), name)
    with open(p, "wb") as f:
        f.write(content)
    return p


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Deterministic subject order
# ---------------------------------------------------------------------------

class TestSubjectOrder:
    def test_single_subject(self, tmp_path):
        img = _write_file(tmp_path, "alice.png")
        shot = _shot([_subject("c1", "Alice", [img])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert len(bindings) == 1
        assert bindings[0].subject_index == 1
        assert bindings[0].picture_index == 1

    def test_two_subjects_preserve_order(self, tmp_path):
        img_a = _write_file(tmp_path, "alice.png", b"alice-data")
        img_b = _write_file(tmp_path, "bob.png", b"bob-data")
        shot = _shot([
            _subject("c1", "Alice", [img_a]),
            _subject("c2", "Bob", [img_b]),
        ])
        chars = [_char("c1", "Alice"), _char("c2", "Bob")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert len(bindings) == 2
        assert bindings[0].subject_index == 1
        assert bindings[0].character_id == "c1"
        assert bindings[0].picture_index == 1
        assert bindings[1].subject_index == 2
        assert bindings[1].character_id == "c2"
        assert bindings[1].picture_index == 2

    def test_reversed_subject_order_changes_bindings(self, tmp_path):
        img_a = _write_file(tmp_path, "alice.png", b"alice-data")
        img_b = _write_file(tmp_path, "bob.png", b"bob-data")
        # Alice first
        shot_ab = _shot([
            _subject("c1", "Alice", [img_a]),
            _subject("c2", "Bob", [img_b]),
        ])
        # Bob first
        shot_ba = _shot([
            _subject("c2", "Bob", [img_b]),
            _subject("c1", "Alice", [img_a]),
        ])
        chars = [_char("c1", "Alice"), _char("c2", "Bob")]
        resolver = H3ReferenceResolver()
        bindings_ab = resolver.resolve(shot_ab, chars, max_refs=9)
        bindings_ba = resolver.resolve(shot_ba, chars, max_refs=9)
        # In AB order: Alice=Subject1, Bob=Subject2
        assert bindings_ab[0].character_id == "c1"
        assert bindings_ab[0].subject_index == 1
        assert bindings_ab[1].character_id == "c2"
        assert bindings_ab[1].subject_index == 2
        # In BA order: Bob=Subject1, Alice=Subject2
        assert bindings_ba[0].character_id == "c2"
        assert bindings_ba[0].subject_index == 1
        assert bindings_ba[1].character_id == "c1"
        assert bindings_ba[1].subject_index == 2


# ---------------------------------------------------------------------------
# Character matching
# ---------------------------------------------------------------------------

class TestCharacterMatching:
    def test_matches_by_character_id(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        shot = _shot([_subject("c1", "Alice", [img])])
        chars = [_char("c1", "Alice", "red dress")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert bindings[0].character_name == "Alice"
        assert bindings[0].appearance == "red dress"

    def test_missing_character_raises(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        shot = _shot([_subject("c-missing", "Ghost", [img])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError, match="c-missing"):
            resolver.resolve(shot, chars, max_refs=9)

    def test_does_not_match_by_name(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        shot = _shot([_subject("c-different-id", "Alice", [img])])
        chars = [_char("c1", "Alice")]  # same name, different id
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError):
            resolver.resolve(shot, chars, max_refs=9)


# ---------------------------------------------------------------------------
# Reference selection — first ref_images path
# ---------------------------------------------------------------------------

class TestReferenceSelection:
    def test_selects_first_ref_image(self, tmp_path):
        img1 = _write_file(tmp_path, "first.png", b"first")
        img2 = _write_file(tmp_path, "second.png", b"second")
        shot = _shot([_subject("c1", "Alice", [img1, img2])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert bindings[0].local_path == img1
        assert bindings[0].content_sha256 == _sha256(b"first")

    def test_empty_ref_images_raises(self, tmp_path):
        shot = _shot([_subject("c1", "Alice", [])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError, match="ref_images"):
            resolver.resolve(shot, chars, max_refs=9)


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------

class TestFileValidation:
    def test_missing_file_raises(self, tmp_path):
        missing = os.path.join(str(tmp_path), "nonexistent.png")
        shot = _shot([_subject("c1", "Alice", [missing])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError, match="exist"):
            resolver.resolve(shot, chars, max_refs=9)

    def test_directory_not_accepted(self, tmp_path):
        d = os.path.join(str(tmp_path), "subdir")
        os.makedirs(d)
        shot = _shot([_subject("c1", "Alice", [d])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError, match="regular file"):
            resolver.resolve(shot, chars, max_refs=9)


# ---------------------------------------------------------------------------
# SHA-256
# ---------------------------------------------------------------------------

class TestSHA256:
    def test_sha256_matches_known_content(self, tmp_path):
        content = b"deterministic test content for sha256"
        expected = hashlib.sha256(content).hexdigest()
        img = _write_file(tmp_path, "known.png", content)
        shot = _shot([_subject("c1", "Alice", [img])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert bindings[0].content_sha256 == expected
        assert len(expected) == 64

    def test_sha256_different_for_different_content(self, tmp_path):
        img1 = _write_file(tmp_path, "a.png", b"content-A")
        img2 = _write_file(tmp_path, "b.png", b"content-B")
        shot = _shot([
            _subject("c1", "Alice", [img1]),
            _subject("c2", "Bob", [img2]),
        ])
        chars = [_char("c1", "Alice"), _char("c2", "Bob")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert bindings[0].content_sha256 != bindings[1].content_sha256


# ---------------------------------------------------------------------------
# Max reference count
# ---------------------------------------------------------------------------

class TestMaxRefs:
    def test_exceeding_max_refs_raises(self, tmp_path):
        imgs = [_write_file(tmp_path, f"ref{i}.png", f"data{i}".encode()) for i in range(3)]
        subjects = [_subject(f"c{i}", f"Char{i}", [imgs[i]]) for i in range(3)]
        chars = [_char(f"c{i}", f"Char{i}") for i in range(3)]
        shot = _shot(subjects)
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError, match="max_refs"):
            resolver.resolve(shot, chars, max_refs=2)

    def test_exactly_at_max_refs_succeeds(self, tmp_path):
        imgs = [_write_file(tmp_path, f"ref{i}.png", f"data{i}".encode()) for i in range(2)]
        subjects = [_subject(f"c{i}", f"Char{i}", [imgs[i]]) for i in range(2)]
        chars = [_char(f"c{i}", f"Char{i}") for i in range(2)]
        shot = _shot(subjects)
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=2)
        assert len(bindings) == 2

    def test_max_refs_below_one_raises(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        shot = _shot([_subject("c1", "Alice", [img])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError, match="max_refs"):
            resolver.resolve(shot, chars, max_refs=0)


# ---------------------------------------------------------------------------
# Empty subjects
# ---------------------------------------------------------------------------

class TestEmptySubjects:
    def test_zero_subjects_raises(self, tmp_path):
        shot = _shot([])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        with pytest.raises(ReferenceResolutionError, match="subject"):
            resolver.resolve(shot, chars, max_refs=9)


# ---------------------------------------------------------------------------
# Returned bindings invariants
# ---------------------------------------------------------------------------

class TestReturnedBindings:
    def test_bindings_are_frozen(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        shot = _shot([_subject("c1", "Alice", [img])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            bindings[0].subject_index = 99  # type: ignore[misc]

    def test_uploaded_filename_empty_after_resolve(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        shot = _shot([_subject("c1", "Alice", [img])])
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert bindings[0].uploaded_filename == ""

    def test_does_not_mutate_shot_subjects(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        subjects = [_subject("c1", "Alice", [img])]
        shot = _shot(subjects)
        original_refs = list(shot.subjects[0].ref_images)
        chars = [_char("c1", "Alice")]
        resolver = H3ReferenceResolver()
        resolver.resolve(shot, chars, max_refs=9)
        assert shot.subjects[0].ref_images == original_refs

    def test_does_not_mutate_characters(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        shot = _shot([_subject("c1", "Alice", [img])])
        chars = [_char("c1", "Alice", "original appearance")]
        resolver = H3ReferenceResolver()
        resolver.resolve(shot, chars, max_refs=9)
        assert chars[0].appearance == "original appearance"

    def test_canonical_character_name_used(self, tmp_path):
        img = _write_file(tmp_path, "ref.png")
        # ShotSubject has nickname "Al", CharacterReference has canonical "Alice"
        shot = _shot([_subject("c1", "Al", [img])])
        chars = [_char("c1", "Alice", "red hair")]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)
        assert bindings[0].character_name == "Alice"
        assert bindings[0].appearance == "red hair"
