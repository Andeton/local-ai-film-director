"""Tests for M5.A — ReferenceAsset, ReferenceGenerationRequest,
ReferenceGenerationExecution models, enums, ownership invariants,
repositories, and migration safety.
"""
import json

import pytest

from film_director.models.reference import (
    ReferenceAsset,
    ReferenceGenerationExecution,
    ReferenceGenerationRequest,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ReferenceAssetRepository,
    ReferenceGenerationExecutionRepository,
    ReferenceGenerationRequestRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA = "a" * 64


def _asset(**kw) -> ReferenceAsset:
    base = dict(
        id="ref-001", project_id="proj-001", character_id="char-001",
        shot_id=None, kind=ReferenceKind.CHARACTER_FACE,
        source=ReferenceSource.USER_UPLOAD, managed_path="storage/ref/001/original.png",
        content_sha256=_SHA, source_provenance="upload-001",
        source_fingerprint="b" * 64, status=ReferenceStatus.CANDIDATE,
        source_state=ReferenceSourceState.CURRENT, pinned=False,
        width=512, height=768, created_at="2024-01-01T00:00:00", updated_at="2024-01-01T00:00:00",
    )
    base.update(kw)
    return ReferenceAsset(**base)


def _gen_request(**kw) -> ReferenceGenerationRequest:
    base = dict(
        id="rgreq-001", project_id="proj-001", character_id="char-001",
        requested_kind=ReferenceKind.CHARACTER_FACE,
        source_appearance_hash="c" * 64, prompt="A detective in a trench coat",
        negative_prompt="", workflow_definition_id="wf-zimg-v1",
        workflow_definition_version="1", workflow_template_fingerprint="d" * 64,
        parameters_snapshot=[{"name": "steps", "value": 20}], seed=42,
        created_at="2024-01-01T00:00:00",
    )
    base.update(kw)
    return ReferenceGenerationRequest(**base)


def _gen_exec(**kw) -> ReferenceGenerationExecution:
    base = dict(
        id="rgexe-001", request_id="rgreq-001", status="pending",
        comfyui_prompt_id=None, output_reference_asset_id=None,
        submitted_at=None, completed_at=None, error=None,
        created_at="2024-01-01T00:00:00", updated_at="2024-01-01T00:00:00",
    )
    base.update(kw)
    return ReferenceGenerationExecution(**base)


# ===========================================================================
# ENUMS
# ===========================================================================

class TestEnums:
    def test_reference_kind_values(self):
        assert ReferenceKind.CHARACTER_FACE == "character_face"
        assert ReferenceKind.CHARACTER_BODY == "character_body"
        assert ReferenceKind.STORYBOARD == "storyboard"

    def test_reference_source_values(self):
        assert ReferenceSource.USER_UPLOAD == "user_upload"
        assert ReferenceSource.WIND_COMIC == "wind_comic"
        assert ReferenceSource.GENERATED == "generated"

    def test_reference_status_values(self):
        assert ReferenceStatus.CANDIDATE == "candidate"
        assert ReferenceStatus.APPROVED == "approved"
        assert ReferenceStatus.REJECTED == "rejected"
        assert ReferenceStatus.ARCHIVED == "archived"

    def test_reference_source_state_values(self):
        assert ReferenceSourceState.CURRENT == "current"
        assert ReferenceSourceState.STALE == "stale"


# ===========================================================================
# REFERENCE ASSET — valid construction
# ===========================================================================

class TestReferenceAssetValid:
    def test_character_face(self):
        a = _asset(kind=ReferenceKind.CHARACTER_FACE, character_id="c1", shot_id=None)
        assert a.kind == ReferenceKind.CHARACTER_FACE
        assert a.character_id == "c1"
        assert a.shot_id is None

    def test_character_body(self):
        a = _asset(kind=ReferenceKind.CHARACTER_BODY, character_id="c1", shot_id=None)
        assert a.kind == ReferenceKind.CHARACTER_BODY

    def test_storyboard(self):
        a = _asset(kind=ReferenceKind.STORYBOARD, character_id=None, shot_id="shot-1")
        assert a.kind == ReferenceKind.STORYBOARD
        assert a.shot_id == "shot-1"
        assert a.character_id is None

    def test_pinned_candidate_structurally_allowed(self):
        a = _asset(pinned=True, status=ReferenceStatus.CANDIDATE)
        assert a.pinned is True
        assert a.status == ReferenceStatus.CANDIDATE

    def test_approved_stale_structurally_allowed(self):
        a = _asset(status=ReferenceStatus.APPROVED, source_state=ReferenceSourceState.STALE)
        assert a.status == ReferenceStatus.APPROVED
        assert a.source_state == ReferenceSourceState.STALE


# ===========================================================================
# REFERENCE ASSET — ownership invariants
# ===========================================================================

class TestReferenceAssetOwnership:
    def test_character_face_requires_character_id(self):
        with pytest.raises((ValueError, Exception)):
            _asset(kind=ReferenceKind.CHARACTER_FACE, character_id=None, shot_id=None)

    def test_character_body_requires_character_id(self):
        with pytest.raises((ValueError, Exception)):
            _asset(kind=ReferenceKind.CHARACTER_BODY, character_id=None, shot_id=None)

    def test_character_ref_with_shot_id_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(kind=ReferenceKind.CHARACTER_FACE, character_id="c1", shot_id="s1")

    def test_storyboard_requires_shot_id(self):
        with pytest.raises((ValueError, Exception)):
            _asset(kind=ReferenceKind.STORYBOARD, character_id=None, shot_id=None)

    def test_storyboard_with_character_id_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(kind=ReferenceKind.STORYBOARD, character_id="c1", shot_id="s1")

    def test_both_owners_none_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(kind=ReferenceKind.CHARACTER_FACE, character_id=None, shot_id=None)

    def test_both_owners_populated_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(kind=ReferenceKind.CHARACTER_FACE, character_id="c1", shot_id="s1")


# ===========================================================================
# REFERENCE ASSET — field validation
# ===========================================================================

class TestReferenceAssetValidation:
    def test_bad_sha_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(content_sha256="bad")

    def test_empty_managed_path_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(managed_path="")

    def test_empty_provenance_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(source_provenance="")

    def test_zero_width_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(width=0)

    def test_negative_height_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(height=-1)

    def test_empty_project_id_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _asset(project_id="")


# ===========================================================================
# REFERENCE GENERATION REQUEST — valid + immutability
# ===========================================================================

class TestReferenceGenerationRequest:
    def test_valid_face_request(self):
        r = _gen_request(requested_kind=ReferenceKind.CHARACTER_FACE)
        assert r.requested_kind == ReferenceKind.CHARACTER_FACE

    def test_valid_body_request(self):
        r = _gen_request(requested_kind=ReferenceKind.CHARACTER_BODY)
        assert r.requested_kind == ReferenceKind.CHARACTER_BODY

    def test_storyboard_request_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _gen_request(requested_kind=ReferenceKind.STORYBOARD)

    def test_missing_appearance_hash_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _gen_request(source_appearance_hash="")

    def test_empty_prompt_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _gen_request(prompt="")

    def test_workflow_identity_required(self):
        with pytest.raises((ValueError, Exception)):
            _gen_request(workflow_definition_id="")

    def test_parameters_snapshot_roundtrip(self):
        params = [{"name": "steps", "value": 30}, {"name": "cfg", "value": 7.5}]
        r = _gen_request(parameters_snapshot=params)
        assert r.parameters_snapshot == params

    def test_seed_preserved(self):
        r = _gen_request(seed=999)
        assert r.seed == 999


# ===========================================================================
# REFERENCE GENERATION EXECUTION — states
# ===========================================================================

class TestReferenceGenerationExecution:
    def test_valid_pending(self):
        e = _gen_exec(status="pending")
        assert e.status == "pending"

    def test_valid_running(self):
        e = _gen_exec(status="running", comfyui_prompt_id="prompt-1")
        assert e.status == "running"

    def test_succeeded_requires_output_and_completed(self):
        with pytest.raises((ValueError, Exception)):
            _gen_exec(status="succeeded", output_reference_asset_id=None, completed_at="t1")

    def test_succeeded_rejects_error(self):
        with pytest.raises((ValueError, Exception)):
            _gen_exec(status="succeeded", output_reference_asset_id="ref-1",
                      completed_at="t1", error="oops")

    def test_failed_requires_error_and_completed(self):
        with pytest.raises((ValueError, Exception)):
            _gen_exec(status="failed", error=None, completed_at="t1")

    def test_failed_rejects_output(self):
        with pytest.raises((ValueError, Exception)):
            _gen_exec(status="failed", error="fail", completed_at="t1",
                      output_reference_asset_id="ref-1")

    def test_valid_succeeded(self):
        e = _gen_exec(status="succeeded", output_reference_asset_id="ref-1",
                      completed_at="t1", error=None)
        assert e.status == "succeeded"

    def test_valid_failed(self):
        e = _gen_exec(status="failed", error="timeout", completed_at="t1",
                      output_reference_asset_id=None)
        assert e.status == "failed"


# ===========================================================================
# DATABASE / MIGRATION
# ===========================================================================

class TestDatabaseMigration:
    def test_fresh_db_has_reference_tables(self, tmp_path):
        db = Database(str(tmp_path / "fresh.db"))
        db.init_schema()
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "reference_assets" in tables
        assert "reference_generation_requests" in tables
        assert "reference_generation_executions" in tables

    def test_existing_db_migration_safe(self, tmp_path):
        """M0-M4 data must survive M5 migration."""
        db = Database(str(tmp_path / "existing.db"))
        db.init_schema()
        # Insert M1-style data
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "existing.db"))
        conn.execute(
            "INSERT INTO production_projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p1", "wc1", "Test", "active", "16:9", "{}",
             "t1", "t1", "wind_comic", "wc1", "wc1", 1, "t1", "a" * 64),
        )
        conn.commit()
        conn.close()
        # Re-init should not destroy existing data
        db.init_schema()
        import sqlite3 as s
        conn2 = s.connect(str(tmp_path / "existing.db"))
        row = conn2.execute("SELECT title FROM production_projects WHERE id = 'p1'").fetchone()
        conn2.close()
        assert row[0] == "Test"

    def test_idempotent_init(self, tmp_path):
        db = Database(str(tmp_path / "idem.db"))
        db.init_schema()
        db.init_schema()  # second call should not fail
        db.init_schema()  # third call


# ===========================================================================
# REPOSITORIES
# ===========================================================================

def _seed_project(db, project_id="proj-001"):
    """Insert a minimal production project for FK satisfaction."""
    with db.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO production_projects "
            "(id, wc_project_id, title, status, aspect, director_context, "
            "created_at, updated_at, prov_source_system, prov_source_project_id, "
            "prov_source_asset_id, prov_source_asset_version, prov_imported_at, prov_source_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, f"wc-{project_id}", "Test", "active", "16:9", "{}",
             "t", "t", "wind_comic", project_id, project_id, 1, "t", "a" * 64),
        )


class TestReferenceAssetRepository:
    @pytest.fixture
    def repo(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        _seed_project(db, "p1")
        _seed_project(db, "p2")
        return ReferenceAssetRepository(db), db

    def test_create_and_get(self, repo):
        r, db = repo
        asset = _asset()
        r.save(asset)
        fetched = r.get(asset.id)
        assert fetched is not None
        assert fetched.id == asset.id
        assert fetched.kind == ReferenceKind.CHARACTER_FACE
        assert fetched.content_sha256 == _SHA
        assert fetched.pinned is False

    def test_list_by_project(self, repo):
        r, _ = repo
        r.save(_asset(id="r1", project_id="p1"))
        r.save(_asset(id="r2", project_id="p1"))
        r.save(_asset(id="r3", project_id="p2"))
        assert len(r.list_by_project("p1")) == 2

    def test_list_by_character(self, repo):
        r, _ = repo
        r.save(_asset(id="r1", character_id="c1"))
        r.save(_asset(id="r2", character_id="c1"))
        r.save(_asset(id="r3", character_id="c2"))
        assert len(r.list_by_character("c1")) == 2

    def test_list_by_shot(self, repo):
        r, _ = repo
        r.save(_asset(id="r1", kind=ReferenceKind.STORYBOARD, character_id=None, shot_id="s1"))
        r.save(_asset(id="r2", kind=ReferenceKind.STORYBOARD, character_id=None, shot_id="s1"))
        assert len(r.list_by_shot("s1")) == 2

    def test_multiple_refs_per_character(self, repo):
        r, _ = repo
        r.save(_asset(id="r1", character_id="c1"))
        r.save(_asset(id="r2", character_id="c1", kind=ReferenceKind.CHARACTER_BODY))
        refs = r.list_by_character("c1")
        assert len(refs) == 2

    def test_update_status(self, repo):
        r, _ = repo
        r.save(_asset(id="r1"))
        r.update_status("r1", ReferenceStatus.APPROVED)
        fetched = r.get("r1")
        assert fetched.status == ReferenceStatus.APPROVED

    def test_update_source_state(self, repo):
        r, _ = repo
        r.save(_asset(id="r1"))
        r.update_source_state("r1", ReferenceSourceState.STALE)
        fetched = r.get("r1")
        assert fetched.source_state == ReferenceSourceState.STALE

    def test_update_pinned(self, repo):
        r, _ = repo
        r.save(_asset(id="r1"))
        r.update_pinned("r1", True)
        fetched = r.get("r1")
        assert fetched.pinned is True

    def test_no_delete_api(self, repo):
        r, _ = repo
        assert not hasattr(r, "delete")


class TestReferenceGenRequestRepository:
    @pytest.fixture
    def repo(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        return ReferenceGenerationRequestRepository(db), db

    def test_create_and_get(self, repo):
        r, _ = repo
        req = _gen_request()
        r.create(req)
        fetched = r.get(req.id)
        assert fetched is not None
        assert fetched.prompt == req.prompt
        assert fetched.seed == req.seed
        assert fetched.parameters_snapshot == req.parameters_snapshot

    def test_no_update_api(self, repo):
        r, _ = repo
        assert not hasattr(r, "update")
        assert not hasattr(r, "save")  # only create

    def test_immutability_across_operations(self, repo):
        """Request payload must not change after creation."""
        r, _ = repo
        req = _gen_request(prompt="Original prompt", seed=123)
        r.create(req)
        fetched = r.get(req.id)
        assert fetched.prompt == "Original prompt"
        assert fetched.seed == 123


class TestReferenceGenExecRepository:
    @pytest.fixture
    def repo(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        # Seed gen requests for FK satisfaction
        req_repo = ReferenceGenerationRequestRepository(db)
        req_repo.create(_gen_request(id="rgreq-001"))
        req_repo.create(_gen_request(id="rq1"))
        req_repo.create(_gen_request(id="rq2"))
        return ReferenceGenerationExecutionRepository(db), db

    def test_create_and_get(self, repo):
        r, _ = repo
        exe = _gen_exec()
        r.create(exe)
        fetched = r.get(exe.id)
        assert fetched is not None
        assert fetched.status == "pending"

    def test_update_lifecycle(self, repo):
        r, _ = repo
        r.create(_gen_exec(id="e1"))
        r.update_status("e1", status="running", comfyui_prompt_id="cp-1",
                         submitted_at="t1")
        fetched = r.get("e1")
        assert fetched.status == "running"
        assert fetched.comfyui_prompt_id == "cp-1"

    def test_get_by_request(self, repo):
        r, _ = repo
        r.create(_gen_exec(id="e1", request_id="rq1"))
        r.create(_gen_exec(id="e2", request_id="rq1"))
        r.create(_gen_exec(id="e3", request_id="rq2"))
        assert len(r.list_by_request("rq1")) == 2

    def test_execution_update_does_not_mutate_request(self, repo):
        """Updating execution must not affect stored request."""
        r, db = repo
        req_repo = ReferenceGenerationRequestRepository(db)
        req = _gen_request(id="rq-test", prompt="Keep this")
        req_repo.create(req)
        r.create(_gen_exec(id="e1", request_id="rq-test"))
        r.update_status("e1", status="running", submitted_at="t1")
        fetched_req = req_repo.get("rq-test")
        assert fetched_req.prompt == "Keep this"

    def _seed_request(self, repo, request_id):
        """Insert a gen request for FK satisfaction."""
        _, db = repo
        req_repo = ReferenceGenerationRequestRepository(db)
        req_repo.create(_gen_request(id=request_id))
