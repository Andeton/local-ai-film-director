"""Tests for H3PromptBuilder — deterministic prompt assembly from authoritative bindings."""
import hashlib
import os

import pytest

from film_director.errors import ReferenceResolutionError
from film_director.generation.h3_prompt import H3PromptBuilder, H3PromptV1
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

VALID_SHA = "a" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _binding(
    subject_index: int = 1,
    character_id: str = "c1",
    character_name: str = "Alice",
    appearance: str = "dark hair, tall",
    picture_index: int = 1,
    local_path: str = "/refs/alice.png",
    content_sha256: str = VALID_SHA,
) -> H3ReferenceBinding:
    return H3ReferenceBinding(
        subject_index=subject_index,
        character_id=character_id,
        character_name=character_name,
        appearance=appearance,
        picture_index=picture_index,
        local_path=local_path,
        content_sha256=content_sha256,
    )


def _shot(**kw) -> ShotSpecificationV1:
    defaults = dict(
        id="shot-1",
        beat_id="beat-1",
        dramatic_purpose="tension rises",
        action="walks toward the window",
        camera=CameraIntent(shot_size="medium", angle="eye level", movement="slow push"),
        lighting={"key": "low key", "mood": "dramatic"},
        audio_intent={"ambient": "rain on windows", "music": "soft piano"},
        duration_sec=5.0,
        order_index=0,
        version=1,
    )
    defaults.update(kw)
    return ShotSpecificationV1(**defaults)


def _plan(**kw) -> GenerationPlan:
    defaults = dict(
        id="plan-1",
        shot_id="shot-1",
        shot_version=1,
        strategy="REFERENCE_TO_VIDEO",
        reference_requirements=ReferenceRequirements(character_refs=True),
        duration_sec=5.0,
        version=1,
    )
    defaults.update(kw)
    return GenerationPlan(**defaults)


# ---------------------------------------------------------------------------
# Binding validation
# ---------------------------------------------------------------------------

class TestBindingValidation:
    def test_empty_bindings_raises(self):
        builder = H3PromptBuilder()
        with pytest.raises(ReferenceResolutionError, match="binding"):
            builder.build(_shot(), _plan(), [])

    def test_duplicate_subject_index_raises(self):
        builder = H3PromptBuilder()
        bindings = [_binding(subject_index=1, picture_index=1), _binding(subject_index=1, picture_index=2, character_id="c2", character_name="Bob")]
        with pytest.raises(ReferenceResolutionError, match="subject_index"):
            builder.build(_shot(), _plan(), bindings)

    def test_duplicate_picture_index_raises(self):
        builder = H3PromptBuilder()
        bindings = [_binding(subject_index=1, picture_index=1), _binding(subject_index=2, picture_index=1, character_id="c2", character_name="Bob")]
        with pytest.raises(ReferenceResolutionError, match="picture_index"):
            builder.build(_shot(), _plan(), bindings)

    def test_non_sequential_subject_index_raises(self):
        builder = H3PromptBuilder()
        bindings = [_binding(subject_index=1, picture_index=1), _binding(subject_index=3, picture_index=2, character_id="c2", character_name="Bob")]
        with pytest.raises(ReferenceResolutionError, match="subject_index"):
            builder.build(_shot(), _plan(), bindings)


# ---------------------------------------------------------------------------
# Plan/shot version mismatch
# ---------------------------------------------------------------------------

class TestVersionMismatch:
    def test_plan_shot_id_mismatch_raises(self):
        builder = H3PromptBuilder()
        shot = _shot(id="shot-1")
        plan = _plan(shot_id="shot-OTHER")
        with pytest.raises(ReferenceResolutionError, match="mismatch"):
            builder.build(shot, plan, [_binding()])

    def test_plan_shot_version_mismatch_raises(self):
        builder = H3PromptBuilder()
        shot = _shot(version=2)
        plan = _plan(shot_version=1)
        with pytest.raises(ReferenceResolutionError, match="mismatch"):
            builder.build(shot, plan, [_binding()])


# ---------------------------------------------------------------------------
# Subject definitions
# ---------------------------------------------------------------------------

class TestSubjectDefinitions:
    def test_single_subject_definition(self):
        builder = H3PromptBuilder()
        bindings = [_binding(character_name="Alice", appearance="red dress, dark hair")]
        result = builder.build(_shot(), _plan(), bindings)
        assert "<Subject 1>" in result.subject_definitions
        assert "<Picture 1>" in result.subject_definitions
        assert "red dress, dark hair" in result.subject_definitions

    def test_two_subject_definitions(self):
        builder = H3PromptBuilder()
        bindings = [
            _binding(subject_index=1, picture_index=1, character_name="Alice", appearance="red dress"),
            _binding(subject_index=2, picture_index=2, character_id="c2", character_name="Bob", appearance="blue suit"),
        ]
        result = builder.build(_shot(), _plan(), bindings)
        assert "<Subject 1>" in result.subject_definitions
        assert "<Picture 1>" in result.subject_definitions
        assert "<Subject 2>" in result.subject_definitions
        assert "<Picture 2>" in result.subject_definitions
        assert "red dress" in result.subject_definitions
        assert "blue suit" in result.subject_definitions


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_contains_action(self):
        builder = H3PromptBuilder()
        shot = _shot(action="runs through the forest")
        result = builder.build(shot, _plan(), [_binding()])
        assert "runs through the forest" in result.summary

    def test_summary_contains_dramatic_purpose(self):
        builder = H3PromptBuilder()
        shot = _shot(dramatic_purpose="reveals betrayal")
        result = builder.build(shot, _plan(), [_binding()])
        assert "reveals betrayal" in result.summary

    def test_summary_contains_character_names(self):
        builder = H3PromptBuilder()
        bindings = [_binding(character_name="Alice")]
        result = builder.build(_shot(), _plan(), bindings)
        assert "Alice" in result.summary


# ---------------------------------------------------------------------------
# Retention analysis
# ---------------------------------------------------------------------------

class TestRetentionAnalysis:
    def test_retention_contains_subject_tag(self):
        builder = H3PromptBuilder()
        result = builder.build(_shot(), _plan(), [_binding()])
        assert "<Subject 1>" in result.retention_analysis

    def test_retention_contains_appearance(self):
        builder = H3PromptBuilder()
        bindings = [_binding(appearance="scarred face, leather jacket")]
        result = builder.build(_shot(), _plan(), bindings)
        assert "scarred face, leather jacket" in result.retention_analysis


# ---------------------------------------------------------------------------
# Detailed description
# ---------------------------------------------------------------------------

class TestDetailedDescription:
    def test_contains_camera_info(self):
        builder = H3PromptBuilder()
        shot = _shot(camera=CameraIntent(shot_size="close_up", angle="low angle"))
        result = builder.build(shot, _plan(), [_binding()])
        assert "close_up" in result.detailed_description

    def test_contains_action(self):
        builder = H3PromptBuilder()
        shot = _shot(action="picks up the phone")
        result = builder.build(shot, _plan(), [_binding()])
        assert "picks up the phone" in result.detailed_description

    def test_contains_duration(self):
        builder = H3PromptBuilder()
        shot = _shot(duration_sec=7.5)
        result = builder.build(shot, _plan(), [_binding()])
        assert "7.5" in result.detailed_description


# ---------------------------------------------------------------------------
# Audio sections
# ---------------------------------------------------------------------------

class TestAudioSections:
    def test_soundscape_from_audio_intent(self):
        builder = H3PromptBuilder()
        shot = _shot(audio_intent={"ambient": "city traffic"})
        result = builder.build(shot, _plan(), [_binding()])
        assert result.overall_soundscape == "city traffic"

    def test_music_from_audio_intent(self):
        builder = H3PromptBuilder()
        shot = _shot(audio_intent={"music": "orchestral swell"})
        result = builder.build(shot, _plan(), [_binding()])
        assert result.non_diegetic_music == "orchestral swell"

    def test_empty_audio_intent(self):
        builder = H3PromptBuilder()
        shot = _shot(audio_intent={})
        result = builder.build(shot, _plan(), [_binding()])
        assert result.overall_soundscape == ""
        assert result.non_diegetic_music == ""

    def test_missing_ambient_key(self):
        builder = H3PromptBuilder()
        shot = _shot(audio_intent={"music": "drums"})
        result = builder.build(shot, _plan(), [_binding()])
        assert result.overall_soundscape == ""
        assert result.non_diegetic_music == "drums"


# ---------------------------------------------------------------------------
# Rendered prompt text
# ---------------------------------------------------------------------------

class TestRenderedPromptText:
    def test_rendered_text_contains_all_sections(self):
        builder = H3PromptBuilder()
        shot = _shot(audio_intent={"ambient": "wind", "music": "piano"})
        result = builder.build(shot, _plan(), [_binding()])
        text = result.rendered_prompt_text
        # Must contain subject definitions, summary, retention, detailed desc
        assert "<Subject 1>" in text
        assert "<Picture 1>" in text
        assert result.summary in text or result.action in text or "walks toward" in text
        assert "wind" in text or result.overall_soundscape in text

    def test_deterministic_same_input_same_output(self):
        builder = H3PromptBuilder()
        shot = _shot()
        plan = _plan()
        bindings = [_binding()]
        r1 = builder.build(shot, plan, bindings)
        r2 = builder.build(shot, plan, bindings)
        assert r1.rendered_prompt_text == r2.rendered_prompt_text

    def test_rendered_text_no_timestamps(self):
        builder = H3PromptBuilder()
        result = builder.build(_shot(), _plan(), [_binding()])
        import re
        # No ISO timestamps
        assert not re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', result.rendered_prompt_text)


# ---------------------------------------------------------------------------
# H3PromptV1 artifact fields
# ---------------------------------------------------------------------------

class TestPromptArtifact:
    def test_source_versions_copied(self):
        builder = H3PromptBuilder()
        shot = _shot(version=3)
        plan = _plan(shot_version=3, version=5)
        result = builder.build(shot, plan, [_binding()])
        assert result.source_shot_version == 3
        assert result.source_generation_plan_version == 5

    def test_generation_plan_id_set(self):
        builder = H3PromptBuilder()
        plan = _plan(id="plan-42")
        result = builder.build(_shot(), plan, [_binding()])
        assert result.generation_plan_id == "plan-42"

    def test_shot_id_set(self):
        builder = H3PromptBuilder()
        shot = _shot(id="shot-99")
        plan = _plan(shot_id="shot-99")
        result = builder.build(shot, plan, [_binding()])
        assert result.shot_id == "shot-99"

    def test_id_is_non_empty_string(self):
        builder = H3PromptBuilder()
        result = builder.build(_shot(), _plan(), [_binding()])
        assert isinstance(result.id, str)
        assert len(result.id) > 0

    def test_version_is_one(self):
        builder = H3PromptBuilder()
        result = builder.build(_shot(), _plan(), [_binding()])
        assert result.version == 1

    def test_status_is_current(self):
        builder = H3PromptBuilder()
        result = builder.build(_shot(), _plan(), [_binding()])
        assert result.status == "current"


# ---------------------------------------------------------------------------
# No LLM, no persistence, no ComfyUI
# ---------------------------------------------------------------------------

class TestNoLeakage:
    def test_builder_does_not_use_bindings_uploaded_filename(self):
        builder = H3PromptBuilder()
        # uploaded_filename="" at resolver stage — builder should not depend on it
        bindings = [_binding()]
        assert bindings[0].uploaded_filename == ""
        result = builder.build(_shot(), _plan(), bindings)
        assert isinstance(result, H3PromptV1)

    def test_returns_h3_prompt_v1(self):
        builder = H3PromptBuilder()
        result = builder.build(_shot(), _plan(), [_binding()])
        assert isinstance(result, H3PromptV1)


# ---------------------------------------------------------------------------
# BLOCKING: single-authority two-subject test
# ---------------------------------------------------------------------------

class TestSingleAuthority:
    """Proves that prompt Subject/Picture numbering comes ONLY from bindings."""

    def _make_two_subject_bindings(self, tmp_path):
        def _write(name, data):
            p = os.path.join(str(tmp_path), name)
            with open(p, "wb") as f:
                f.write(data)
            return p

        img_a = _write("alice.png", b"alice-image-data")
        img_b = _write("bob.png", b"bob-image-data")
        sha_a = hashlib.sha256(b"alice-image-data").hexdigest()
        sha_b = hashlib.sha256(b"bob-image-data").hexdigest()
        return [
            H3ReferenceBinding(
                subject_index=1, character_id="cA", character_name="Alice",
                appearance="blonde, blue dress", picture_index=1,
                local_path=img_a, content_sha256=sha_a,
            ),
            H3ReferenceBinding(
                subject_index=2, character_id="cB", character_name="Bob",
                appearance="tall, grey coat", picture_index=2,
                local_path=img_b, content_sha256=sha_b,
            ),
        ]

    def test_two_subjects_aligned(self, tmp_path):
        bindings = self._make_two_subject_bindings(tmp_path)
        builder = H3PromptBuilder()
        result = builder.build(_shot(), _plan(), bindings)
        text = result.rendered_prompt_text
        # Subject 1 ↔ Picture 1 ↔ Alice
        assert "<Subject 1>" in text
        assert "<Picture 1>" in text
        assert "Alice" in text or "blonde, blue dress" in text
        # Subject 2 ↔ Picture 2 ↔ Bob
        assert "<Subject 2>" in text
        assert "<Picture 2>" in text
        assert "Bob" in text or "tall, grey coat" in text

    def test_reversed_bindings_change_prompt_order(self, tmp_path):
        bindings_ab = self._make_two_subject_bindings(tmp_path)
        # Create reversed bindings (Bob first = Subject 1)
        img_a = bindings_ab[0].local_path
        img_b = bindings_ab[1].local_path
        sha_a = bindings_ab[0].content_sha256
        sha_b = bindings_ab[1].content_sha256
        bindings_ba = [
            H3ReferenceBinding(
                subject_index=1, character_id="cB", character_name="Bob",
                appearance="tall, grey coat", picture_index=1,
                local_path=img_b, content_sha256=sha_b,
            ),
            H3ReferenceBinding(
                subject_index=2, character_id="cA", character_name="Alice",
                appearance="blonde, blue dress", picture_index=2,
                local_path=img_a, content_sha256=sha_a,
            ),
        ]
        builder = H3PromptBuilder()
        r_ab = builder.build(_shot(), _plan(), bindings_ab)
        r_ba = builder.build(_shot(), _plan(), bindings_ba)
        # Different ordering should produce different prompt text
        assert r_ab.rendered_prompt_text != r_ba.rendered_prompt_text


# ---------------------------------------------------------------------------
# End-to-end resolver → builder
# ---------------------------------------------------------------------------

class TestResolverToBuilder:
    """Integration: resolver output feeds directly into builder."""

    def _provenance(self):
        return Provenance(
            source_system="test",
            source_project_id="p1",
            source_asset_id="t-1",
            source_asset_version=1,
            imported_at="2026-01-01T00:00:00",
            source_hash="h",
        )

    def test_resolver_output_feeds_builder(self, tmp_path):
        img_a = os.path.join(str(tmp_path), "a.png")
        img_b = os.path.join(str(tmp_path), "b.png")
        with open(img_a, "wb") as f:
            f.write(b"img-a-data")
        with open(img_b, "wb") as f:
            f.write(b"img-b-data")

        shot = _shot(
            subjects=[
                ShotSubject(character_id="cA", name="Alice", ref_images=[img_a]),
                ShotSubject(character_id="cB", name="Bob", ref_images=[img_b]),
            ]
        )
        chars = [
            CharacterReference(
                id="cA", project_id="p1", wc_character_id="wc-a",
                name="Alice", description="d", appearance="blonde hair",
                provenance=self._provenance(),
            ),
            CharacterReference(
                id="cB", project_id="p1", wc_character_id="wc-b",
                name="Bob", description="d", appearance="grey coat",
                provenance=self._provenance(),
            ),
        ]
        resolver = H3ReferenceResolver()
        bindings = resolver.resolve(shot, chars, max_refs=9)

        builder = H3PromptBuilder()
        plan = _plan()
        result = builder.build(shot, plan, bindings)
        assert isinstance(result, H3PromptV1)
        assert "<Subject 1>" in result.rendered_prompt_text
        assert "<Subject 2>" in result.rendered_prompt_text
        assert "<Picture 1>" in result.rendered_prompt_text
        assert "<Picture 2>" in result.rendered_prompt_text

    def test_reversed_resolver_order_changes_prompt(self, tmp_path):
        img_a = os.path.join(str(tmp_path), "a.png")
        img_b = os.path.join(str(tmp_path), "b.png")
        with open(img_a, "wb") as f:
            f.write(b"img-a-data")
        with open(img_b, "wb") as f:
            f.write(b"img-b-data")

        chars = [
            CharacterReference(
                id="cA", project_id="p1", wc_character_id="wc-a",
                name="Alice", description="d", appearance="blonde",
                provenance=self._provenance(),
            ),
            CharacterReference(
                id="cB", project_id="p1", wc_character_id="wc-b",
                name="Bob", description="d", appearance="bald",
                provenance=self._provenance(),
            ),
        ]
        resolver = H3ReferenceResolver()
        builder = H3PromptBuilder()
        plan = _plan()

        # Alice first
        shot_ab = _shot(subjects=[
            ShotSubject(character_id="cA", name="Alice", ref_images=[img_a]),
            ShotSubject(character_id="cB", name="Bob", ref_images=[img_b]),
        ])
        bindings_ab = resolver.resolve(shot_ab, chars, max_refs=9)
        r_ab = builder.build(shot_ab, plan, bindings_ab)

        # Bob first
        shot_ba = _shot(subjects=[
            ShotSubject(character_id="cB", name="Bob", ref_images=[img_b]),
            ShotSubject(character_id="cA", name="Alice", ref_images=[img_a]),
        ])
        bindings_ba = resolver.resolve(shot_ba, chars, max_refs=9)
        r_ba = builder.build(shot_ba, plan, bindings_ba)

        assert r_ab.rendered_prompt_text != r_ba.rendered_prompt_text
        # AB: Subject1=Alice, BA: Subject1=Bob
        assert bindings_ab[0].character_name == "Alice"
        assert bindings_ba[0].character_name == "Bob"
