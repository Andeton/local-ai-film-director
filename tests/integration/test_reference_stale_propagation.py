"""Integration tests for M5.F — reference staleness on character appearance changes.

Tests atomicity of import + stale propagation, source policies, historical
immutability, and ReferenceSelector exclusion of stale refs.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from film_director.models.canonical import (
    CameraIntent,
    CharacterReference,
    GenerationPlan,
    ProductionProject,
    ReferenceRequirements,
    Scene,
    Sequence,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
    compute_appearance_fingerprint,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    CharacterRepository,
    ProjectRepository,
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.services.import_service import ImportService
from film_director.services.reference_lifecycle import ReferenceSelector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prov(char_appearance: str = "dark hair") -> Provenance:
    """Build provenance with source_hash derived from appearance for test."""
    from film_director.models.provenance import compute_source_hash
    return Provenance(
        source_system="wind_comic",
        source_project_id="wc-p1",
        source_asset_id="wc-c1",
        source_asset_version=1,
        imported_at="2026-01-01T00:00:00",
        source_hash=compute_source_hash({"name": "Alice", "appearance": char_appearance}),
    )


def _make_ref(
    asset_id: str,
    project_id: str = "proj-1",
    character_id: str = "char-1",
    source: ReferenceSource = ReferenceSource.GENERATED,
    source_fingerprint: str | None = None,
    status: ReferenceStatus = ReferenceStatus.APPROVED,
    pinned: bool = False,
) -> ReferenceAsset:
    return ReferenceAsset(
        id=asset_id,
        project_id=project_id,
        character_id=character_id,
        kind=ReferenceKind.CHARACTER_BODY,
        source=source,
        managed_path=f"/storage/refs/{asset_id}.png",
        content_sha256="a" * 64,
        source_provenance="gen-req-1",
        source_fingerprint=source_fingerprint,
        status=status,
        source_state=ReferenceSourceState.CURRENT,
        pinned=pinned,
        width=1024,
        height=1024,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


@pytest.fixture
def env(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    db = Database(db_path)
    db.init_schema()

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    char_repo = CharacterRepository(db)
    ref_repo = ReferenceAssetRepository(db)

    with db.connection() as conn:
        project_repo.save_project(ProductionProject(
            id="proj-1", wc_project_id="wc-p1", title="Test",
            status="active", created_at="2026-01-01", updated_at="2026-01-01",
            provenance=_prov(),
        ), conn=conn)
        seq_repo.save_sequence(Sequence(
            id="seq-1", project_id="proj-1", name="Main", order_index=0,
        ), conn=conn)
        scene_repo.save_scene(Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="Scene1", location="office", description="office",
            order_index=0, provenance=_prov(),
        ), conn=conn)
        char_repo.save_character(CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective",
            appearance="dark hair, trench coat",
            provenance=_prov("dark hair, trench coat"),
        ), conn=conn)

    return {
        "db": db,
        "project_repo": project_repo,
        "seq_repo": seq_repo,
        "scene_repo": scene_repo,
        "char_repo": char_repo,
        "ref_repo": ref_repo,
    }


def _build_mock_adapter(appearance: str = "dark hair, trench coat", name: str = "Alice"):
    """Build a WC adapter mock returning a character with the given appearance."""
    from film_director.adapters.wind_comic import WCProjectBundle
    from film_director.models.wind_comic_dto import (
        WCCharacter, WCProject, WCScene,
    )
    mock = MagicMock()
    mock.read_project_bundle.return_value = WCProjectBundle(
        project=WCProject(
            id="wc-p1", title="Test", status="active", aspect="16:9",
            style_id=None, script_data=None, locked_characters=[],
        ),
        scenes=[WCScene(
            asset_id="wc-s1", project_id="wc-p1", name="Scene1", version=1,
            data={"description": "office", "location": "office"},
            media_urls=[], persistent_url=None,
        )],
        characters=[WCCharacter(
            asset_id="wc-c1", project_id="wc-p1", name=name, version=2,
            data={"description": "Detective", "appearance": appearance},
            media_urls=[], persistent_url=None,
        )],
    )
    return mock


# ---------------------------------------------------------------------------
# Appearance change stales generated refs atomically
# ---------------------------------------------------------------------------

class TestAppearanceChangeStalesGeneratedRefs:
    def test_wc_appearance_change_stales_old_generated_ref(self, env):
        """Reimport with changed appearance → GENERATED ref becomes STALE."""
        old_fp = compute_appearance_fingerprint("dark hair, trench coat")
        env["ref_repo"].save(_make_ref("ref-gen", source_fingerprint=old_fp))

        adapter = _build_mock_adapter(appearance="blond hair, leather jacket")
        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=env["ref_repo"],
        )
        import_svc.import_project("wc-p1")

        asset = env["ref_repo"].get("ref-gen")
        assert asset.source_state == ReferenceSourceState.STALE

    def test_non_appearance_change_does_not_stale(self, env):
        """Character name/description change without appearance change → refs stay CURRENT."""
        old_fp = compute_appearance_fingerprint("dark hair, trench coat")
        env["ref_repo"].save(_make_ref("ref-gen", source_fingerprint=old_fp))

        # Same appearance, different name
        adapter = _build_mock_adapter(
            appearance="dark hair, trench coat", name="Alice Renamed",
        )
        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=env["ref_repo"],
        )
        import_svc.import_project("wc-p1")

        asset = env["ref_repo"].get("ref-gen")
        assert asset.source_state == ReferenceSourceState.CURRENT

    def test_user_upload_remains_selectable_after_appearance_change(self, env):
        """USER_UPLOAD ref stays CURRENT when appearance text changes."""
        env["ref_repo"].save(_make_ref(
            "ref-upload", source=ReferenceSource.USER_UPLOAD,
            source_fingerprint=None,
        ))

        adapter = _build_mock_adapter(appearance="completely new look")
        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=env["ref_repo"],
        )
        import_svc.import_project("wc-p1")

        asset = env["ref_repo"].get("ref-upload")
        assert asset.source_state == ReferenceSourceState.CURRENT

    def test_approved_pinned_generated_becomes_stale_and_ineligible(self, env):
        """APPROVED+PINNED generated ref becomes STALE, ReferenceSelector rejects it."""
        old_fp = compute_appearance_fingerprint("dark hair, trench coat")
        env["ref_repo"].save(_make_ref(
            "ref-pinned", source_fingerprint=old_fp,
            status=ReferenceStatus.APPROVED, pinned=True,
        ))

        adapter = _build_mock_adapter(appearance="new appearance")
        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=env["ref_repo"],
        )
        import_svc.import_project("wc-p1")

        asset = env["ref_repo"].get("ref-pinned")
        assert asset.source_state == ReferenceSourceState.STALE
        assert asset.status == ReferenceStatus.APPROVED  # preserved
        assert asset.pinned is True  # preserved

        # ReferenceSelector must reject stale ref
        from film_director.errors import ReferenceResolutionError
        shot = ShotSpecificationV1(
            id="shot-1", beat_id="beat-1", dramatic_purpose="test",
            subjects=[ShotSubject(character_id="char-1", name="Alice", ref_images=[])],
            action="test", camera=CameraIntent(shot_size="medium"),
            duration_sec=5.0, order_index=0, version=1,
            created_at="t", updated_at="t",
        )
        characters = env["char_repo"].get_characters_by_project("proj-1")
        all_assets = env["ref_repo"].list_by_project("proj-1")
        selector = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError, match="char-1"):
            selector.select(
                shot=shot, project_id="proj-1",
                kind=ReferenceKind.CHARACTER_BODY,
                characters=characters, assets=all_assets,
            )

    def test_revert_appearance_does_not_reactivate(self, env):
        """Reverting to original appearance does NOT make stale ref CURRENT again."""
        old_fp = compute_appearance_fingerprint("dark hair, trench coat")
        env["ref_repo"].save(_make_ref("ref-gen", source_fingerprint=old_fp))

        # Change appearance → stale
        adapter = _build_mock_adapter(appearance="new look")
        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=env["ref_repo"],
        )
        import_svc.import_project("wc-p1")
        assert env["ref_repo"].get("ref-gen").source_state == ReferenceSourceState.STALE

        # Revert appearance → still stale
        adapter2 = _build_mock_adapter(appearance="dark hair, trench coat")
        import_svc2 = ImportService(
            adapter=adapter2,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=env["ref_repo"],
        )
        import_svc2.import_project("wc-p1")
        assert env["ref_repo"].get("ref-gen").source_state == ReferenceSourceState.STALE

    def test_new_generated_ref_with_new_fingerprint_stays_current(self, env):
        """A ref generated AFTER appearance change with new fingerprint stays CURRENT."""
        old_fp = compute_appearance_fingerprint("dark hair, trench coat")
        new_fp = compute_appearance_fingerprint("new look")
        # Old ref with old fingerprint
        env["ref_repo"].save(_make_ref("ref-old", source_fingerprint=old_fp))
        # New ref with new fingerprint (generated after appearance change)
        env["ref_repo"].save(_make_ref("ref-new", source_fingerprint=new_fp))

        adapter = _build_mock_adapter(appearance="new look")
        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=env["ref_repo"],
        )
        import_svc.import_project("wc-p1")

        assert env["ref_repo"].get("ref-old").source_state == ReferenceSourceState.STALE
        assert env["ref_repo"].get("ref-new").source_state == ReferenceSourceState.CURRENT

    def test_stale_propagation_failure_rolls_back_character(self, env):
        """If stale propagation fails, character appearance update rolls back."""
        old_fp = compute_appearance_fingerprint("dark hair, trench coat")
        env["ref_repo"].save(_make_ref("ref-gen", source_fingerprint=old_fp))

        # Use a real adapter mock but sabotage the ref repo
        adapter = _build_mock_adapter(appearance="sabotaged new look")
        broken_ref_repo = MagicMock(spec=ReferenceAssetRepository)
        broken_ref_repo.mark_generated_stale_for_character.side_effect = \
            Exception("DB corruption")

        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=broken_ref_repo,
        )

        from film_director.errors import NormalizationError
        with pytest.raises(NormalizationError):
            import_svc.import_project("wc-p1")

        # Character appearance should NOT have changed (rolled back)
        char = env["char_repo"].get_character("char-1")
        assert char.appearance == "dark hair, trench coat"

        # Ref should still be CURRENT (not staled)
        asset = env["ref_repo"].get("ref-gen")
        assert asset.source_state == ReferenceSourceState.CURRENT

    def test_no_ref_repo_skips_stale_propagation(self, env):
        """ImportService without ref_asset_repo still works (backward compat)."""
        adapter = _build_mock_adapter(appearance="new appearance")
        import_svc = ImportService(
            adapter=adapter,
            project_repo=env["project_repo"],
            sequence_repo=env["seq_repo"],
            scene_repo=env["scene_repo"],
            character_repo=env["char_repo"],
            db=env["db"],
            ref_asset_repo=None,  # no ref repo
        )
        # Should not raise
        result = import_svc.import_project("wc-p1")
        assert result.characters_imported == 1
