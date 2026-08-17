"""Tests for M7.G.A — VisualAssetPack, AssetRole, AssetRoleBinding,
deterministic selection, fingerprinting, and backward compatibility.
"""
import hashlib
import json
import sqlite3

import pytest

from film_director.models.reference import (
    AssetRole,
    AssetRoleBinding,
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
    VisualAssetPack,
    compute_pack_fingerprint,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    ReferenceAssetRepository,
    VisualAssetPackRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


def _asset(**kw) -> ReferenceAsset:
    base = dict(
        id="ref-001", project_id="proj-001", character_id="char-001",
        shot_id=None, kind=ReferenceKind.CHARACTER_FACE,
        source=ReferenceSource.USER_UPLOAD, managed_path="storage/ref/001/original.png",
        content_sha256=_SHA, source_provenance="upload-001",
        source_fingerprint=_SHA_B, status=ReferenceStatus.CANDIDATE,
        source_state=ReferenceSourceState.CURRENT, pinned=False,
        width=512, height=768, created_at="2024-01-01T00:00:00", updated_at="2024-01-01T00:00:00",
    )
    base.update(kw)
    return ReferenceAsset(**base)


def _pack(**kw) -> VisualAssetPack:
    base = dict(
        id="vap_test000001",
        project_id="proj-001",
        name="Test Pack",
        description="",
        version=1,
        fingerprint=_SHA,
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )
    base.update(kw)
    return VisualAssetPack(**base)


def _binding(**kw) -> AssetRoleBinding:
    base = dict(
        id="arb_test000001",
        pack_id="vap_test000001",
        reference_asset_id="ref-001",
        role=AssetRole.CHARACTER_FACE_CLOSEUP,
        order_index=0,
        created_at="2024-01-01T00:00:00",
    )
    base.update(kw)
    return AssetRoleBinding(**base)


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


def _seed_asset(db, asset_id="ref-001", project_id="proj-001", **kw):
    """Insert a reference asset for FK satisfaction."""
    repo = ReferenceAssetRepository(db)
    repo.save(_asset(id=asset_id, project_id=project_id, **kw))


# ===========================================================================
# ASSET ROLE ENUM
# ===========================================================================

class TestAssetRole:
    def test_all_required_values_present(self):
        expected = {
            "CHARACTER_FACE_CLOSEUP", "CHARACTER_BODY_FRONT",
            "CHARACTER_BODY_SIDE", "CHARACTER_BODY_BACK",
            "CHARACTER_TURNAROUND", "CHARACTER_WARDROBE",
            "ENVIRONMENT_MASTER", "ENVIRONMENT_VIEW",
            "ENVIRONMENT_PANORAMA_360", "ENVIRONMENT_DEPTH",
            "ENVIRONMENT_LAYOUT",
            "PROP_REFERENCE", "PROP_TURNAROUND",
            "STYLE_REFERENCE", "CONTINUITY_FRAME",
            "MOTION_REFERENCE", "CONTROL_VIDEO", "AUDIO_REFERENCE",
        }
        actual = {r.name for r in AssetRole}
        assert actual == expected

    def test_roundtrip_string_values(self):
        for role in AssetRole:
            assert AssetRole(role.value) == role

    def test_no_provider_specific_values(self):
        """AssetRole must not contain H3/LTX/Wan/SCAIL/Kling provider names."""
        provider_fragments = [
            "h3", "ltx", "wan", "vace", "scail", "kling", "seedance",
            "comfyui", "picture_1", "picture_2", "ref_image",
            "minimax", "skyreels",
        ]
        for role in AssetRole:
            lower = role.value.lower()
            for frag in provider_fragments:
                assert frag not in lower, f"AssetRole {role.name} contains provider concept '{frag}'"

    def test_values_are_lowercase_strings(self):
        for role in AssetRole:
            assert role.value == role.value.lower()
            assert isinstance(role.value, str)


# ===========================================================================
# VISUAL ASSET PACK — model
# ===========================================================================

class TestVisualAssetPackModel:
    def test_valid_construction(self):
        p = _pack()
        assert p.id == "vap_test000001"
        assert p.project_id == "proj-001"
        assert p.version == 1

    def test_empty_id_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _pack(id="")

    def test_empty_project_id_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _pack(project_id="")

    def test_empty_name_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _pack(name="")

    def test_invalid_fingerprint_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _pack(fingerprint="bad")

    def test_no_provider_field(self):
        """Canonical VisualAssetPack has no provider/model/workflow fields."""
        fields = set(VisualAssetPack.model_fields.keys())
        provider_fields = {
            "engine_family", "workflow_profile", "provider", "workflow",
            "model", "node", "slot", "workflow_id", "model_id",
        }
        assert fields & provider_fields == set()


# ===========================================================================
# ASSET ROLE BINDING — model
# ===========================================================================

class TestAssetRoleBindingModel:
    def test_valid_construction(self):
        b = _binding()
        assert b.pack_id == "vap_test000001"
        assert b.role == AssetRole.CHARACTER_FACE_CLOSEUP

    def test_empty_pack_id_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _binding(pack_id="")

    def test_empty_asset_id_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _binding(reference_asset_id="")

    def test_negative_order_rejected(self):
        with pytest.raises((ValueError, Exception)):
            _binding(order_index=-1)

    def test_no_provider_field(self):
        """Canonical binding has no provider/model/workflow fields."""
        fields = set(AssetRoleBinding.model_fields.keys())
        provider_fields = {
            "engine_family", "workflow_profile", "provider", "workflow",
            "model", "node", "slot", "workflow_id", "model_id",
            "picture_slot",
        }
        assert fields & provider_fields == set()


# ===========================================================================
# DATABASE / MIGRATION
# ===========================================================================

class TestVisualAssetPackMigration:
    def test_fresh_db_has_pack_tables(self, tmp_path):
        db = Database(str(tmp_path / "fresh.db"))
        db.init_schema()
        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "visual_asset_packs" in tables
        assert "asset_role_bindings" in tables

    def test_existing_db_migration_safe(self, tmp_path):
        """Pre-M7.G.A data must survive migration."""
        db = Database(str(tmp_path / "existing.db"))
        db.init_schema()
        # Insert M1-style project data
        _seed_project(db, "p1")
        # Re-init should not destroy existing data
        db.init_schema()
        conn = sqlite3.connect(str(tmp_path / "existing.db"))
        row = conn.execute("SELECT title FROM production_projects WHERE id = 'p1'").fetchone()
        conn.close()
        assert row[0] == "Test"

    def test_historical_reference_assets_readable(self, tmp_path):
        """Historical ReferenceAssets must remain readable after migration."""
        db = Database(str(tmp_path / "hist.db"))
        db.init_schema()
        _seed_project(db)
        ref_repo = ReferenceAssetRepository(db)
        ref_repo.save(_asset(id="ref-old"))
        # Re-init
        db.init_schema()
        fetched = ref_repo.get("ref-old")
        assert fetched is not None
        assert fetched.id == "ref-old"

    def test_historical_generation_requests_readable(self, tmp_path):
        """Historical GenerationRequests must remain readable after migration."""
        from film_director.persistence.repositories import ReferenceGenerationRequestRepository
        from film_director.models.reference import ReferenceGenerationRequest
        db = Database(str(tmp_path / "hist_gr.db"))
        db.init_schema()
        _seed_project(db)
        req_repo = ReferenceGenerationRequestRepository(db)
        req = ReferenceGenerationRequest(
            id="rgreq-old", project_id="proj-001", character_id="char-001",
            requested_kind=ReferenceKind.CHARACTER_FACE,
            source_appearance_hash="c" * 64, prompt="Old prompt",
            workflow_definition_id="wf-v1", workflow_definition_version="1",
            workflow_template_fingerprint="d" * 64, seed=42,
            created_at="2024-01-01T00:00:00",
        )
        req_repo.create(req)
        db.init_schema()
        fetched = req_repo.get("rgreq-old")
        assert fetched is not None
        assert fetched.prompt == "Old prompt"

    def test_old_assets_not_required_to_have_pack_bindings(self, tmp_path):
        """Old ReferenceAssets with no pack bindings must remain valid."""
        db = Database(str(tmp_path / "nopack.db"))
        db.init_schema()
        _seed_project(db)
        ref_repo = ReferenceAssetRepository(db)
        ref_repo.save(_asset(id="ref-no-pack"))
        fetched = ref_repo.get("ref-no-pack")
        assert fetched is not None
        # No pack binding needed


# ===========================================================================
# PACK PERSISTENCE (Repository)
# ===========================================================================

class TestVisualAssetPackRepository:
    @pytest.fixture
    def env(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        _seed_project(db, "proj-002")
        pack_repo = VisualAssetPackRepository(db)
        ref_repo = ReferenceAssetRepository(db)
        return pack_repo, ref_repo, db

    def test_create_and_get(self, env):
        pack_repo, _, _ = env
        pack = _pack()
        pack_repo.save(pack)
        fetched = pack_repo.get(pack.id)
        assert fetched is not None
        assert fetched.id == pack.id
        assert fetched.name == "Test Pack"
        assert fetched.version == 1

    def test_stable_identity(self, env):
        """Pack ID is stable across save/get."""
        pack_repo, _, _ = env
        pack = _pack(id="vap_stable00001")
        pack_repo.save(pack)
        fetched = pack_repo.get("vap_stable00001")
        assert fetched.id == "vap_stable00001"

    def test_project_ownership(self, env):
        """Pack belongs to project."""
        pack_repo, _, _ = env
        pack = _pack(project_id="proj-001")
        pack_repo.save(pack)
        fetched = pack_repo.get(pack.id)
        assert fetched.project_id == "proj-001"

    def test_list_by_project(self, env):
        pack_repo, _, _ = env
        pack_repo.save(_pack(id="vap_p1_0000001", project_id="proj-001"))
        pack_repo.save(_pack(id="vap_p1_0000002", project_id="proj-001"))
        pack_repo.save(_pack(id="vap_p2_0000001", project_id="proj-002"))
        p1_packs = pack_repo.list_by_project("proj-001")
        assert len(p1_packs) == 2
        p2_packs = pack_repo.list_by_project("proj-002")
        assert len(p2_packs) == 1


# ===========================================================================
# BINDINGS
# ===========================================================================

class TestAssetRoleBindings:
    @pytest.fixture
    def env(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        _seed_project(db, "proj-002")
        pack_repo = VisualAssetPackRepository(db)
        ref_repo = ReferenceAssetRepository(db)
        # Create a pack
        pack_repo.save(_pack(id="vap_test000001"))
        # Create reference assets
        _seed_asset(db, "ref-001", "proj-001")
        _seed_asset(db, "ref-002", "proj-001", content_sha256=_SHA_B)
        _seed_asset(db, "ref-003", "proj-001", content_sha256=_SHA_C)
        return pack_repo, ref_repo, db

    def test_add_binding(self, env):
        pack_repo, _, _ = env
        binding = _binding()
        pack_repo.add_binding(binding)
        bindings = pack_repo.list_bindings("vap_test000001")
        assert len(bindings) == 1
        assert bindings[0].reference_asset_id == "ref-001"
        assert bindings[0].role == AssetRole.CHARACTER_FACE_CLOSEUP

    def test_binding_fk_integrity_pack(self, env):
        """Invalid pack FK must be rejected."""
        pack_repo, _, _ = env
        binding = _binding(pack_id="nonexistent")
        with pytest.raises((sqlite3.IntegrityError, Exception)):
            pack_repo.add_binding(binding)

    def test_binding_fk_integrity_asset(self, env):
        """Invalid ReferenceAsset FK must be rejected."""
        pack_repo, _, _ = env
        binding = _binding(reference_asset_id="nonexistent")
        with pytest.raises((sqlite3.IntegrityError, Exception)):
            pack_repo.add_binding(binding)

    def test_multiple_assets_same_role(self, env):
        """Multiple assets can have the same role in a pack."""
        pack_repo, _, _ = env
        pack_repo.add_binding(_binding(
            id="arb_001", reference_asset_id="ref-001",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        pack_repo.add_binding(_binding(
            id="arb_002", reference_asset_id="ref-002",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=1,
        ))
        bindings = pack_repo.list_bindings("vap_test000001")
        assert len(bindings) == 2

    def test_deterministic_ordering(self, env):
        """Bindings must be returned in deterministic order."""
        pack_repo, _, _ = env
        # Insert in reverse order
        pack_repo.add_binding(_binding(
            id="arb_002", reference_asset_id="ref-002",
            role=AssetRole.ENVIRONMENT_VIEW, order_index=1,
        ))
        pack_repo.add_binding(_binding(
            id="arb_001", reference_asset_id="ref-001",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        bindings = pack_repo.list_bindings("vap_test000001")
        assert bindings[0].role == AssetRole.CHARACTER_FACE_CLOSEUP
        assert bindings[1].role == AssetRole.ENVIRONMENT_VIEW

    def test_same_asset_reuse_different_roles(self, env):
        """Same ReferenceAsset can be reused in different roles."""
        pack_repo, _, _ = env
        pack_repo.add_binding(_binding(
            id="arb_001", reference_asset_id="ref-001",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        pack_repo.add_binding(_binding(
            id="arb_002", reference_asset_id="ref-001",
            role=AssetRole.STYLE_REFERENCE, order_index=0,
        ))
        bindings = pack_repo.list_bindings("vap_test000001")
        assert len(bindings) == 2

    def test_remove_binding_leaves_asset_intact(self, env):
        """Removing a binding must NOT delete the ReferenceAsset."""
        pack_repo, ref_repo, _ = env
        pack_repo.add_binding(_binding(id="arb_001"))
        pack_repo.remove_binding("arb_001")
        bindings = pack_repo.list_bindings("vap_test000001")
        assert len(bindings) == 0
        # ReferenceAsset must still exist
        asset = ref_repo.get("ref-001")
        assert asset is not None

    def test_update_binding_role_and_order(self, env):
        """Binding role and order can be updated."""
        pack_repo, _, _ = env
        pack_repo.add_binding(_binding(id="arb_001"))
        pack_repo.update_binding(
            "arb_001",
            role=AssetRole.STYLE_REFERENCE,
            order_index=5,
        )
        bindings = pack_repo.list_bindings("vap_test000001")
        assert len(bindings) == 1
        assert bindings[0].role == AssetRole.STYLE_REFERENCE
        assert bindings[0].order_index == 5


# ===========================================================================
# ELIGIBILITY — production-eligible selection
# ===========================================================================

class TestEligibility:
    @pytest.fixture
    def env(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        pack_repo = VisualAssetPackRepository(db)
        ref_repo = ReferenceAssetRepository(db)
        pack_repo.save(_pack(id="vap_test000001"))
        return pack_repo, ref_repo, db

    def _add_asset_and_bind(self, env, asset_id, status, source_state, role, pinned=False):
        pack_repo, ref_repo, _ = env
        ref_repo.save(_asset(
            id=asset_id, status=status, source_state=source_state, pinned=pinned,
            content_sha256=hashlib.sha256(asset_id.encode()).hexdigest(),
        ))
        pack_repo.add_binding(_binding(
            id=f"arb_{asset_id}", reference_asset_id=asset_id,
            role=role, order_index=0,
        ))

    def test_approved_current_included(self, env):
        self._add_asset_and_bind(
            env, "ref-ok", ReferenceStatus.APPROVED, ReferenceSourceState.CURRENT,
            AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 1
        assert eligible[0].id == "ref-ok"

    def test_candidate_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-cand", ReferenceStatus.CANDIDATE, ReferenceSourceState.CURRENT,
            AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0

    def test_rejected_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-rej", ReferenceStatus.REJECTED, ReferenceSourceState.CURRENT,
            AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0

    def test_archived_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-arch", ReferenceStatus.ARCHIVED, ReferenceSourceState.CURRENT,
            AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0

    def test_stale_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-stale", ReferenceStatus.APPROVED, ReferenceSourceState.STALE,
            AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0

    def test_pinned_candidate_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-pin-cand", ReferenceStatus.CANDIDATE, ReferenceSourceState.CURRENT,
            AssetRole.CHARACTER_FACE_CLOSEUP, pinned=True,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0

    def test_pinned_stale_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-pin-stale", ReferenceStatus.APPROVED, ReferenceSourceState.STALE,
            AssetRole.CHARACTER_FACE_CLOSEUP, pinned=True,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0

    def test_pinned_rejected_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-pin-rej", ReferenceStatus.REJECTED, ReferenceSourceState.CURRENT,
            AssetRole.CHARACTER_FACE_CLOSEUP, pinned=True,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0

    def test_pinned_archived_excluded(self, env):
        self._add_asset_and_bind(
            env, "ref-pin-arch", ReferenceStatus.ARCHIVED, ReferenceSourceState.CURRENT,
            AssetRole.CHARACTER_FACE_CLOSEUP, pinned=True,
        )
        pack_repo = env[0]
        eligible = pack_repo.resolve_eligible_assets(
            "vap_test000001", role=AssetRole.CHARACTER_FACE_CLOSEUP,
        )
        assert len(eligible) == 0


# ===========================================================================
# FILTERING
# ===========================================================================

class TestFiltering:
    @pytest.fixture
    def env(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        _seed_project(db, "proj-002")
        pack_repo = VisualAssetPackRepository(db)
        ref_repo = ReferenceAssetRepository(db)
        return pack_repo, ref_repo, db

    def test_role_filtering(self, env):
        pack_repo, ref_repo, _ = env
        pack_repo.save(_pack(id="vap_filt"))
        ref_repo.save(_asset(id="ref-face", status=ReferenceStatus.APPROVED,
                             content_sha256=hashlib.sha256(b"face").hexdigest()))
        ref_repo.save(_asset(id="ref-env", status=ReferenceStatus.APPROVED,
                             content_sha256=hashlib.sha256(b"env").hexdigest()))
        pack_repo.add_binding(_binding(
            id="arb_f", pack_id="vap_filt", reference_asset_id="ref-face",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        pack_repo.add_binding(_binding(
            id="arb_e", pack_id="vap_filt", reference_asset_id="ref-env",
            role=AssetRole.ENVIRONMENT_VIEW, order_index=0,
        ))
        faces = pack_repo.resolve_eligible_assets("vap_filt", role=AssetRole.CHARACTER_FACE_CLOSEUP)
        assert len(faces) == 1
        assert faces[0].id == "ref-face"

    def test_project_isolation(self, env):
        """Packs from project A must not appear in project B listing."""
        pack_repo, _, _ = env
        pack_repo.save(_pack(id="vap_p1", project_id="proj-001"))
        pack_repo.save(_pack(id="vap_p2", project_id="proj-002"))
        p1_packs = pack_repo.list_by_project("proj-001")
        p1_ids = {p.id for p in p1_packs}
        assert "vap_p2" not in p1_ids

    def test_character_ownership_filtering(self, env):
        """Eligible assets filtered by character_id."""
        pack_repo, ref_repo, _ = env
        pack_repo.save(_pack(id="vap_char"))
        ref_repo.save(_asset(
            id="ref-c1", character_id="char-001", status=ReferenceStatus.APPROVED,
            content_sha256=hashlib.sha256(b"c1").hexdigest(),
        ))
        ref_repo.save(_asset(
            id="ref-c2", character_id="char-002", status=ReferenceStatus.APPROVED,
            content_sha256=hashlib.sha256(b"c2").hexdigest(),
        ))
        pack_repo.add_binding(_binding(
            id="arb_c1", pack_id="vap_char", reference_asset_id="ref-c1",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        pack_repo.add_binding(_binding(
            id="arb_c2", pack_id="vap_char", reference_asset_id="ref-c2",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=1,
        ))
        eligible = pack_repo.resolve_eligible_assets(
            "vap_char", role=AssetRole.CHARACTER_FACE_CLOSEUP,
            character_id="char-001",
        )
        assert len(eligible) == 1
        assert eligible[0].id == "ref-c1"

    def test_shot_ownership_filtering(self, env):
        """Eligible assets filtered by shot_id."""
        pack_repo, ref_repo, _ = env
        pack_repo.save(_pack(id="vap_shot"))
        ref_repo.save(_asset(
            id="ref-s1", kind=ReferenceKind.STORYBOARD, character_id=None, shot_id="shot-1",
            status=ReferenceStatus.APPROVED,
            content_sha256=hashlib.sha256(b"s1").hexdigest(),
        ))
        ref_repo.save(_asset(
            id="ref-s2", kind=ReferenceKind.STORYBOARD, character_id=None, shot_id="shot-2",
            status=ReferenceStatus.APPROVED,
            content_sha256=hashlib.sha256(b"s2").hexdigest(),
        ))
        pack_repo.add_binding(_binding(
            id="arb_s1", pack_id="vap_shot", reference_asset_id="ref-s1",
            role=AssetRole.CONTINUITY_FRAME, order_index=0,
        ))
        pack_repo.add_binding(_binding(
            id="arb_s2", pack_id="vap_shot", reference_asset_id="ref-s2",
            role=AssetRole.CONTINUITY_FRAME, order_index=1,
        ))
        eligible = pack_repo.resolve_eligible_assets(
            "vap_shot", role=AssetRole.CONTINUITY_FRAME,
            shot_id="shot-1",
        )
        assert len(eligible) == 1
        assert eligible[0].id == "ref-s1"

    def test_no_cross_project_asset_leakage(self, env):
        """Assets from project B must not leak into project A packs."""
        pack_repo, ref_repo, _ = env
        # Pack in proj-001
        pack_repo.save(_pack(id="vap_p1", project_id="proj-001"))
        # Asset in proj-002
        ref_repo.save(_asset(
            id="ref-p2", project_id="proj-002", status=ReferenceStatus.APPROVED,
            content_sha256=hashlib.sha256(b"p2").hexdigest(),
        ))
        # Try to bind cross-project (the binding table allows it, but
        # resolve_eligible_assets filters by pack's project_id)
        pack_repo.add_binding(_binding(
            id="arb_cross", pack_id="vap_p1", reference_asset_id="ref-p2",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        eligible = pack_repo.resolve_eligible_assets(
            "vap_p1", role=AssetRole.CHARACTER_FACE_CLOSEUP,
            project_id="proj-001",
        )
        assert len(eligible) == 0


# ===========================================================================
# FINGERPRINT
# ===========================================================================

class TestFingerprint:
    def test_deterministic_independent_of_db_query_order(self):
        """Fingerprint must be the same regardless of input order."""
        bindings_a = [
            ("ref-001", "character_face_closeup", _SHA, 0),
            ("ref-002", "environment_view", _SHA_B, 0),
        ]
        bindings_b = [
            ("ref-002", "environment_view", _SHA_B, 0),
            ("ref-001", "character_face_closeup", _SHA, 0),
        ]
        fp_a = compute_pack_fingerprint("vap_001", bindings_a)
        fp_b = compute_pack_fingerprint("vap_001", bindings_b)
        assert fp_a == fp_b

    def test_deterministic_independent_of_insertion_order(self):
        """When role ordering is identical, fingerprint is insertion-order-independent."""
        bindings_a = [
            ("ref-001", "character_face_closeup", _SHA, 0),
            ("ref-002", "character_face_closeup", _SHA_B, 1),
        ]
        bindings_b = [
            ("ref-002", "character_face_closeup", _SHA_B, 1),
            ("ref-001", "character_face_closeup", _SHA, 0),
        ]
        assert compute_pack_fingerprint("vap_001", bindings_a) == \
               compute_pack_fingerprint("vap_001", bindings_b)

    def test_changes_when_binding_membership_changes(self):
        bindings_1 = [("ref-001", "character_face_closeup", _SHA, 0)]
        bindings_2 = [
            ("ref-001", "character_face_closeup", _SHA, 0),
            ("ref-002", "environment_view", _SHA_B, 0),
        ]
        assert compute_pack_fingerprint("vap_001", bindings_1) != \
               compute_pack_fingerprint("vap_001", bindings_2)

    def test_changes_when_role_changes(self):
        bindings_a = [("ref-001", "character_face_closeup", _SHA, 0)]
        bindings_b = [("ref-001", "style_reference", _SHA, 0)]
        assert compute_pack_fingerprint("vap_001", bindings_a) != \
               compute_pack_fingerprint("vap_001", bindings_b)

    def test_changes_when_order_changes(self):
        bindings_a = [
            ("ref-001", "character_face_closeup", _SHA, 0),
            ("ref-002", "character_face_closeup", _SHA_B, 1),
        ]
        bindings_b = [
            ("ref-001", "character_face_closeup", _SHA, 1),
            ("ref-002", "character_face_closeup", _SHA_B, 0),
        ]
        assert compute_pack_fingerprint("vap_001", bindings_a) != \
               compute_pack_fingerprint("vap_001", bindings_b)

    def test_changes_when_asset_content_changes(self):
        bindings_a = [("ref-001", "character_face_closeup", _SHA, 0)]
        bindings_b = [("ref-001", "character_face_closeup", _SHA_C, 0)]
        assert compute_pack_fingerprint("vap_001", bindings_a) != \
               compute_pack_fingerprint("vap_001", bindings_b)

    def test_no_timestamp_dependency(self):
        """Same bindings at different times → same fingerprint."""
        bindings = [("ref-001", "character_face_closeup", _SHA, 0)]
        fp1 = compute_pack_fingerprint("vap_001", bindings)
        fp2 = compute_pack_fingerprint("vap_001", bindings)
        assert fp1 == fp2

    def test_no_absolute_paths(self):
        """Fingerprint must not contain absolute paths."""
        bindings = [("ref-001", "character_face_closeup", _SHA, 0)]
        fp = compute_pack_fingerprint("vap_001", bindings)
        assert "\\" not in fp
        assert "/" not in fp
        assert ":" not in fp  # no C: drive letters

    def test_valid_sha256(self):
        bindings = [("ref-001", "character_face_closeup", _SHA, 0)]
        fp = compute_pack_fingerprint("vap_001", bindings)
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_version_increments_on_effective_changes(self, tmp_path):
        """Version should increment when membership changes."""
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        pack_repo = VisualAssetPackRepository(db)
        ref_repo = ReferenceAssetRepository(db)
        ref_repo.save(_asset(id="ref-001", status=ReferenceStatus.APPROVED))
        ref_repo.save(_asset(id="ref-002", status=ReferenceStatus.APPROVED,
                             content_sha256=_SHA_B))
        # Create pack
        pack_repo.save(_pack(id="vap_ver"))
        assert pack_repo.get("vap_ver").version == 1
        fp1 = pack_repo.get("vap_ver").fingerprint
        # Add binding and recompute
        pack_repo.add_binding(_binding(
            id="arb_v1", pack_id="vap_ver", reference_asset_id="ref-001",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        pack_repo.recompute_fingerprint("vap_ver")
        updated = pack_repo.get("vap_ver")
        assert updated.version == 2
        assert updated.fingerprint != fp1
        fp2 = updated.fingerprint
        # Add another binding
        pack_repo.add_binding(_binding(
            id="arb_v2", pack_id="vap_ver", reference_asset_id="ref-002",
            role=AssetRole.ENVIRONMENT_VIEW, order_index=0,
        ))
        pack_repo.recompute_fingerprint("vap_ver")
        updated2 = pack_repo.get("vap_ver")
        assert updated2.version == 3
        assert updated2.fingerprint != fp2

    def test_version_no_increment_when_fingerprint_unchanged(self, tmp_path):
        """Version must NOT increment when fingerprint is the same."""
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        pack_repo = VisualAssetPackRepository(db)
        ref_repo = ReferenceAssetRepository(db)
        ref_repo.save(_asset(id="ref-001", status=ReferenceStatus.APPROVED))
        pack_repo.save(_pack(id="vap_nochg"))
        pack_repo.add_binding(_binding(
            id="arb_nc1", pack_id="vap_nochg", reference_asset_id="ref-001",
            role=AssetRole.CHARACTER_FACE_CLOSEUP, order_index=0,
        ))
        pack_repo.recompute_fingerprint("vap_nochg")
        v_after_first = pack_repo.get("vap_nochg").version
        fp_after_first = pack_repo.get("vap_nochg").fingerprint
        # Recompute again without changes
        pack_repo.recompute_fingerprint("vap_nochg")
        v_after_second = pack_repo.get("vap_nochg").version
        fp_after_second = pack_repo.get("vap_nochg").fingerprint
        assert v_after_second == v_after_first
        assert fp_after_second == fp_after_first


# ===========================================================================
# BACKWARD COMPATIBILITY
# ===========================================================================

class TestBackwardCompatibility:
    def test_existing_unbound_assets_remain_usable(self, tmp_path):
        """Existing ReferenceAssets with no pack bindings must work."""
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-001")
        ref_repo = ReferenceAssetRepository(db)
        ref_repo.save(_asset(id="ref-unbound"))
        fetched = ref_repo.get("ref-unbound")
        assert fetched is not None
        assert fetched.kind == ReferenceKind.CHARACTER_FACE
        # All M5 operations still work
        ref_repo.update_status("ref-unbound", ReferenceStatus.APPROVED)
        ref_repo.update_source_state("ref-unbound", ReferenceSourceState.STALE)
        ref_repo.update_pinned("ref-unbound", True)
        fetched2 = ref_repo.get("ref-unbound")
        assert fetched2.status == ReferenceStatus.APPROVED
        assert fetched2.source_state == ReferenceSourceState.STALE
        assert fetched2.pinned is True

    def test_reference_kind_unchanged(self):
        """M5 ReferenceKind enum must be unchanged."""
        assert ReferenceKind.CHARACTER_FACE == "character_face"
        assert ReferenceKind.CHARACTER_BODY == "character_body"
        assert ReferenceKind.STORYBOARD == "storyboard"
        assert len(ReferenceKind) == 3

    def test_reference_source_unchanged(self):
        assert ReferenceSource.USER_UPLOAD == "user_upload"
        assert ReferenceSource.WIND_COMIC == "wind_comic"
        assert ReferenceSource.GENERATED == "generated"
        assert len(ReferenceSource) == 3

    def test_reference_status_unchanged(self):
        assert ReferenceStatus.CANDIDATE == "candidate"
        assert ReferenceStatus.APPROVED == "approved"
        assert ReferenceStatus.REJECTED == "rejected"
        assert ReferenceStatus.ARCHIVED == "archived"
        assert len(ReferenceStatus) == 4

    def test_reference_source_state_unchanged(self):
        assert ReferenceSourceState.CURRENT == "current"
        assert ReferenceSourceState.STALE == "stale"
        assert len(ReferenceSourceState) == 2


# ===========================================================================
# BOUNDARY — no provider concepts in canonical layer
# ===========================================================================

class TestBoundary:
    def test_visual_asset_pack_no_provider_field(self):
        fields = set(VisualAssetPack.model_fields.keys())
        provider_fields = {
            "engine_family", "workflow_profile", "provider", "workflow",
            "model", "node", "slot", "workflow_id", "model_id",
            "picture_slot", "ref_image", "unet",
        }
        assert fields & provider_fields == set()

    def test_binding_no_provider_field(self):
        fields = set(AssetRoleBinding.model_fields.keys())
        provider_fields = {
            "engine_family", "workflow_profile", "provider", "workflow",
            "model", "node", "slot", "workflow_id", "model_id",
            "picture_slot", "ref_image", "unet",
        }
        assert fields & provider_fields == set()

    def test_asset_role_no_provider_concepts(self):
        provider_fragments = [
            "h3", "ltx", "wan", "vace", "scail", "kling", "seedance",
            "comfyui", "picture_1", "picture_2", "ref_image",
            "minimax", "skyreels", "node_", "slot_",
        ]
        for role in AssetRole:
            lower = role.value.lower()
            for frag in provider_fragments:
                assert frag not in lower

    def test_no_h3_picture_slot_in_canonical_layer(self):
        """No H3 Picture slot concepts anywhere in canonical models."""
        for role in AssetRole:
            assert "picture" not in role.value.lower()
            assert "picture" not in role.name.lower()

    def test_no_workflow_model_node_ids_in_canonical_layer(self):
        for field_set in [
            set(VisualAssetPack.model_fields.keys()),
            set(AssetRoleBinding.model_fields.keys()),
        ]:
            for f in field_set:
                assert "comfyui" not in f.lower()
                assert "workflow" not in f.lower() or f == "workflow"  # just in case
                assert "node_id" not in f.lower()
