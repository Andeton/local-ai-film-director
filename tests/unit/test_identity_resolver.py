"""Tests for M7.F identity-context resolution and identity-locked FLF prompts."""
import json
import os
import pytest

from film_director.continuity.identity_resolver import IdentityResolver, SubjectIdentityContext
from film_director.generation.h3_prompt import H3PromptBuilder
from film_director.models.canonical import (
    CameraIntent, GenerationPlan, ReferenceRequirements, ShotSpecificationV1, ShotSubject,
)
from film_director.models.reference import ReferenceKind
from film_director.persistence.database import Database

_FP = "a" * 64


def _setup_project(conn):
    conn.execute(
        "INSERT OR IGNORE INTO production_projects "
        "(id, wc_project_id, title, status, created_at, updated_at, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("proj_1", "wc_1", "Test", "active", "t", "t",
         "wind_comic", "wc_1", "a1", "t", "h1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO character_references "
        "(id, project_id, wc_character_id, name, description, appearance, status, "
        "prov_source_system, prov_source_project_id, prov_source_asset_id, "
        "prov_imported_at, prov_source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("char_1", "proj_1", "wc_c1", "John", "Tall European man with dark hair", "",
         "active", "wind_comic", "wc_1", "c1", "t", "h_c1"),
    )


def _add_generated_ref(conn, asset_id="ref_1", char_id="char_1",
                        prompt="white European man, 40 years old, dark suit",
                        negative="East Asian, different person"):
    conn.execute(
        "INSERT OR IGNORE INTO reference_assets "
        "(id, project_id, character_id, kind, source, managed_path, content_sha256, "
        "source_provenance, status, source_state, pinned, width, height, "
        "created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (asset_id, "proj_1", char_id, "character_body", "generated",
         "refs/ref.png", _FP, "{}", "approved", "current", 0,
         1024, 1024, "t", "t"),
    )
    req_id = f"rgreq_{asset_id}"
    conn.execute(
        "INSERT OR IGNORE INTO reference_generation_requests "
        "(id, project_id, character_id, requested_kind, source_appearance_hash, "
        "prompt, negative_prompt, workflow_definition_id, workflow_definition_version, "
        "workflow_template_fingerprint, parameters_snapshot, seed, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (req_id, "proj_1", char_id, "character_body", "hash_1",
         prompt, negative,
         "z_image_turbo_v1", "1.0.0", _FP, "[]", 42, "t"),
    )
    exec_id = f"rgexe_{asset_id}"
    conn.execute(
        "INSERT OR IGNORE INTO reference_generation_executions "
        "(id, request_id, status, output_reference_asset_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (exec_id, req_id, "succeeded", asset_id, "t", "t"),
    )


def _add_user_upload_ref(conn, asset_id="ref_upload", char_id="char_1"):
    conn.execute(
        "INSERT OR IGNORE INTO reference_assets "
        "(id, project_id, character_id, kind, source, managed_path, content_sha256, "
        "source_provenance, status, source_state, pinned, width, height, "
        "created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (asset_id, "proj_1", char_id, "character_body", "user_upload",
         "refs/upload.png", _FP, "{}", "approved", "current", 0,
         1024, 1024, "t", "t"),
    )


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


# ---------------------------------------------------------------------------
# IdentityResolver
# ---------------------------------------------------------------------------

class TestIdentityPriority:
    def test_generated_ref_prompt_has_priority(self, db):
        """Approved generated ref prompt overrides empty appearance."""
        with db.connection() as conn:
            _setup_project(conn)
            _add_generated_ref(conn, prompt="white European man, 40y, dark suit, angular face")
        resolver = IdentityResolver(db)
        subjects = [ShotSubject(character_id="char_1", name="John")]
        ctxs = resolver.resolve_for_subjects(subjects, "proj_1")
        assert len(ctxs) == 1
        assert ctxs[0].identity_text_source == "reference_generation_prompt"
        assert "white European" in ctxs[0].identity_text
        assert "angular face" in ctxs[0].identity_text

    def test_negative_prompt_preserved(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            _add_generated_ref(conn, negative="East Asian, different person")
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        assert "East Asian" in ctxs[0].negative_identity_text

    def test_user_upload_falls_back_to_description(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            _add_user_upload_ref(conn)
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        assert ctxs[0].identity_text_source == "description"
        assert "European" in ctxs[0].identity_text

    def test_appearance_has_priority_over_description(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            conn.execute(
                "UPDATE character_references SET appearance = 'tall blond man' WHERE id = 'char_1'",
            )
            _add_user_upload_ref(conn)
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        assert ctxs[0].identity_text_source == "appearance"
        assert "blond" in ctxs[0].identity_text

    def test_name_only_fallback(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            # No reference asset at all
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        # Falls back to description (which has content)
        assert ctxs[0].identity_text_source == "description"

    def test_no_char_no_ref_name_only(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            conn.execute(
                "UPDATE character_references SET description='', appearance='' WHERE id='char_1'",
            )
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        assert ctxs[0].identity_text_source == "name_only"
        assert ctxs[0].identity_text == "John"

    def test_character_name_never_sole_when_ref_exists(self, db):
        """Chinese name must not be sole context when approved ref has English prompt."""
        with db.connection() as conn:
            _setup_project(conn)
            conn.execute(
                "UPDATE character_references SET name = '陆砚' WHERE id = 'char_1'",
            )
            _add_generated_ref(conn, prompt="white European man in dark suit")
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="陆砚")], "proj_1",
        )
        assert ctxs[0].identity_text_source == "reference_generation_prompt"
        assert "白" not in ctxs[0].identity_text  # no Chinese chars in identity text
        assert "陆砚" not in ctxs[0].identity_text

    def test_rejected_ref_not_used(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            _add_generated_ref(conn, prompt="good prompt")
            conn.execute("UPDATE reference_assets SET status = 'rejected' WHERE id = 'ref_1'")
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        # Falls back to description since ref is rejected
        assert ctxs[0].identity_text_source == "description"

    def test_stale_ref_not_used(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            _add_generated_ref(conn)
            conn.execute("UPDATE reference_assets SET source_state = 'stale' WHERE id = 'ref_1'")
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        assert ctxs[0].identity_text_source == "description"

    def test_cross_project_ref_not_used(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            conn.execute(
                "INSERT INTO production_projects "
                "(id, wc_project_id, title, status, created_at, updated_at, "
                "prov_source_system, prov_source_project_id, prov_source_asset_id, "
                "prov_imported_at, prov_source_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("other_proj", "wc_2", "Other", "active", "t", "t",
                 "wind_comic", "wc_2", "a2", "t", "h2"),
            )
            _add_generated_ref(conn)
            conn.execute("UPDATE reference_assets SET project_id = 'other_proj' WHERE id = 'ref_1'")
        resolver = IdentityResolver(db)
        ctxs = resolver.resolve_for_subjects(
            [ShotSubject(character_id="char_1", name="John")], "proj_1",
        )
        assert ctxs[0].identity_text_source == "description"

    def test_multi_subject_order(self, db):
        with db.connection() as conn:
            _setup_project(conn)
            conn.execute(
                "INSERT INTO character_references "
                "(id, project_id, wc_character_id, name, description, appearance, status, "
                "prov_source_system, prov_source_project_id, prov_source_asset_id, "
                "prov_imported_at, prov_source_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("char_2", "proj_1", "wc_c2", "Jane", "Short woman", "petite woman with red hair",
                 "active", "wind_comic", "wc_1", "c2", "t", "h_c2"),
            )
        resolver = IdentityResolver(db)
        subjects = [
            ShotSubject(character_id="char_1", name="John"),
            ShotSubject(character_id="char_2", name="Jane"),
        ]
        ctxs = resolver.resolve_for_subjects(subjects, "proj_1")
        assert len(ctxs) == 2
        assert ctxs[0].character_id == "char_1"
        assert ctxs[1].character_id == "char_2"
        assert ctxs[1].identity_text_source == "appearance"
        assert "red hair" in ctxs[1].identity_text


# ---------------------------------------------------------------------------
# FLF Prompt with Identity Context
# ---------------------------------------------------------------------------

class TestIdentityLockedPrompt:
    def _make_shot(self):
        return ShotSpecificationV1(
            id="shot_2", beat_id="b1", dramatic_purpose="test",
            subjects=[ShotSubject(character_id="char_1", name="John")],
            action="walks forward silently", camera=CameraIntent(shot_size="medium"),
            environment={"location": "Neutral studio"},
            order_index=1, version=1, created_at="t", updated_at="t",
        )

    def _make_plan(self):
        return GenerationPlan(
            id="plan_1", shot_id="shot_2", shot_version=1,
            strategy="FIRST_LAST_FRAME",
            reference_requirements=ReferenceRequirements(prev_frame=True),
            duration_sec=5.0, version=1, created_at="t", updated_at="t",
        )

    def test_identity_text_in_prompt(self):
        ctx = SubjectIdentityContext(
            character_id="char_1", character_name="John",
            identity_text="white European man, 40y, dark suit, angular face",
            identity_text_source="reference_generation_prompt",
            negative_identity_text="East Asian",
            reference_asset_id="ref_1", reference_kind="character_body",
            reference_source="generated", reference_content_sha256=_FP,
            reference_generation_request_id="rgreq_1",
        )
        builder = H3PromptBuilder()
        prompt = builder.build_continuity_prompt(self._make_shot(), self._make_plan(), [ctx])
        assert "white European man" in prompt.rendered_prompt_text
        assert "angular face" in prompt.rendered_prompt_text
        assert "same recognizable face" in prompt.rendered_prompt_text
        assert "ethnicity" in prompt.rendered_prompt_text

    def test_negative_in_retention(self):
        ctx = SubjectIdentityContext(
            character_id="char_1", character_name="John",
            identity_text="European man", identity_text_source="reference_generation_prompt",
            negative_identity_text="East Asian, different person",
            reference_asset_id=None, reference_kind=None,
            reference_source=None, reference_content_sha256=None,
            reference_generation_request_id=None,
        )
        builder = H3PromptBuilder()
        prompt = builder.build_continuity_prompt(self._make_shot(), self._make_plan(), [ctx])
        assert "East Asian, different person" in prompt.rendered_prompt_text
        assert "identity change" in prompt.rendered_prompt_text

    def test_no_picture_tags(self):
        ctx = SubjectIdentityContext(
            character_id="char_1", character_name="John",
            identity_text="European man", identity_text_source="reference_generation_prompt",
            negative_identity_text="", reference_asset_id=None,
            reference_kind=None, reference_source=None,
            reference_content_sha256=None, reference_generation_request_id=None,
        )
        builder = H3PromptBuilder()
        prompt = builder.build_continuity_prompt(self._make_shot(), self._make_plan(), [ctx])
        assert "<Picture" not in prompt.rendered_prompt_text
        assert "<Subject" not in prompt.rendered_prompt_text

    def test_environment_retention(self):
        builder = H3PromptBuilder()
        shot = self._make_shot()
        prompt = builder.build_continuity_prompt(shot, self._make_plan(), [])
        assert "same room" in prompt.rendered_prompt_text.lower() or "same room" in prompt.rendered_prompt_text
        assert "furniture" in prompt.rendered_prompt_text.lower()
        assert "Neutral studio" in prompt.rendered_prompt_text

    def test_environment_change_not_blocked(self):
        """Explicit shot environment is included, not suppressed."""
        builder = H3PromptBuilder()
        shot = ShotSpecificationV1(
            id="shot_2", beat_id="b1", dramatic_purpose="test",
            subjects=[ShotSubject(character_id="char_1", name="John")],
            action="walks outside", camera=CameraIntent(shot_size="wide"),
            environment={"location": "City street"},
            order_index=1, version=1, created_at="t", updated_at="t",
        )
        plan = self._make_plan()
        prompt = builder.build_continuity_prompt(shot, plan, [])
        assert "City street" in prompt.rendered_prompt_text

    def test_backward_compat_without_identity_contexts(self):
        """build_continuity_prompt without identity_contexts still works."""
        builder = H3PromptBuilder()
        prompt = builder.build_continuity_prompt(self._make_shot(), self._make_plan())
        assert "first frame" in prompt.rendered_prompt_text.lower()

    def test_historical_snapshot_without_identity_deserializes(self):
        """Old continuity_snapshot without identity_context field is valid."""
        from film_director.generation.generation_request import GenerationRequest
        old_snap = {
            "continuity_state_id": "cs_1",
            "upstream_shot_id": "s1",
            "upstream_take_id": "t1",
        }
        req = GenerationRequest(
            id="greq_old", shot_id="s1", shot_version=1,
            generation_plan_id="p1", generation_plan_version=1,
            prompt_artifact_id="pr1", prompt_artifact_version=1,
            workflow_definition_id="h3_flf_v1",
            workflow_definition_version="1.0.0",
            workflow_template_fingerprint=_FP,
            take_number=1, seed=42,
            continuity_snapshot=old_snap,
        )
        assert req.continuity_snapshot == old_snap
        assert "identity_context" not in req.continuity_snapshot


# ---------------------------------------------------------------------------
# R2V Backward Compatibility
# ---------------------------------------------------------------------------

class TestR2VBackwardCompat:
    def test_r2v_prompt_still_has_picture_tags(self):
        from film_director.generation.h3_types import H3ReferenceBinding
        shot = ShotSpecificationV1(
            id="shot_1", beat_id="b1", dramatic_purpose="test",
            subjects=[ShotSubject(character_id="char_1", name="John")],
            action="walks forward", camera=CameraIntent(shot_size="medium"),
            order_index=0, version=1, created_at="t", updated_at="t",
        )
        plan = GenerationPlan(
            id="plan_1", shot_id="shot_1", shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=5.0, version=1, created_at="t", updated_at="t",
        )
        binding = H3ReferenceBinding(
            subject_index=0, character_id="char_1",
            character_name="John", appearance="tall man",
            picture_index=1, local_path="/p", content_sha256=_FP,
        )
        builder = H3PromptBuilder()
        prompt = builder.build(shot, plan, [binding])
        assert "<Picture 1>" in prompt.rendered_prompt_text
        assert "<Subject" in prompt.rendered_prompt_text


# ---------------------------------------------------------------------------
# Workflow Fingerprints
# ---------------------------------------------------------------------------

class TestWorkflowFingerprints:
    def test_all_unchanged(self):
        import hashlib
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        for fname, expected in [
            ("workflows/h3/r2v_v1.json", "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4"),
            ("workflows/h3/r2v_v2.json", "b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9"),
            ("workflows/h3/flf_v1.json", "47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6"),
        ]:
            actual = hashlib.sha256(open(os.path.join(project_root, fname), "rb").read()).hexdigest()
            assert actual == expected, f"{fname}: {actual} != {expected}"
