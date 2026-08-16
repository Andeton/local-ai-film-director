"""Tests for M5.D — reference lifecycle + deterministic selection.

Unit tests for pure selector logic + integration tests for lifecycle persistence.
"""
from __future__ import annotations

import copy

import pytest

from film_director.errors import ReferenceLifecycleError, ReferenceResolutionError
from film_director.models.canonical import (
    CameraIntent,
    CharacterReference,
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
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import ReferenceAssetRepository
from film_director.services.reference_lifecycle import (
    ReferenceLifecycleService,
    ReferenceSelector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA = "a" * 64


def _prov():
    return Provenance(
        source_system="wind_comic", source_project_id="proj-1",
        source_asset_id="asset-1", source_asset_version=1,
        imported_at="t", source_hash="a" * 64,
    )


def _char(char_id="char-1", project_id="proj-1", name="Hero") -> CharacterReference:
    return CharacterReference(
        id=char_id, project_id=project_id, wc_character_id=f"wc-{char_id}",
        name=name, description="", appearance="", provenance=_prov(),
    )


def _asset(id="ref-1", project_id="proj-1", character_id="char-1",
           kind=ReferenceKind.CHARACTER_BODY, status=ReferenceStatus.APPROVED,
           source_state=ReferenceSourceState.CURRENT, pinned=False,
           created_at="2024-01-01T00:00:00", **kw) -> ReferenceAsset:
    base = dict(
        id=id, project_id=project_id, character_id=character_id, shot_id=None,
        kind=kind, source=ReferenceSource.GENERATED,
        managed_path=f"references/{project_id}/{id}/original.png",
        content_sha256=_SHA, source_provenance="gen-001",
        source_fingerprint="b" * 64, status=status,
        source_state=source_state, pinned=pinned,
        width=1024, height=1024, created_at=created_at, updated_at=created_at,
    )
    base.update(kw)
    return ReferenceAsset(**base)


def _shot(subjects=None) -> ShotSpecificationV1:
    if subjects is None:
        subjects = [ShotSubject(character_id="char-1", name="Hero", ref_images=[])]
    return ShotSpecificationV1(
        id="shot-1", beat_id="beat-1", dramatic_purpose="test",
        subjects=subjects, action="test", environment={},
        camera=CameraIntent(shot_size="wide"), lighting={},
        audio_intent={}, duration_sec=5.0, continuity_inputs={},
        order_index=0, status="draft", source="generated", version=1,
        created_at="t", updated_at="t",
    )


def _seed_project(db, project_id="proj-1"):
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


# Default characters for selector tests
_CHARS = [_char("char-1", "proj-1", "Hero")]


# ===========================================================================
# LIFECYCLE
# ===========================================================================

class TestLifecycle:
    @pytest.fixture
    def env(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.init_schema()
        _seed_project(db, "proj-1")
        repo = ReferenceAssetRepository(db)
        svc = ReferenceLifecycleService(repo)
        return dict(repo=repo, svc=svc, db=db)

    def test_approve_candidate(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.CANDIDATE))
        env["svc"].approve("ref-1")
        assert env["repo"].get("ref-1").status == ReferenceStatus.APPROVED

    def test_reject_reference(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.APPROVED))
        env["svc"].reject("ref-1")
        assert env["repo"].get("ref-1").status == ReferenceStatus.REJECTED

    def test_archive_reference(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.APPROVED))
        env["svc"].archive("ref-1")
        assert env["repo"].get("ref-1").status == ReferenceStatus.ARCHIVED

    def test_pin_approved_current(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.APPROVED,
                                source_state=ReferenceSourceState.CURRENT))
        env["svc"].pin("ref-1")
        assert env["repo"].get("ref-1").pinned is True

    def test_pin_candidate_rejected(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.CANDIDATE))
        with pytest.raises(ReferenceLifecycleError):
            env["svc"].pin("ref-1")

    def test_pin_stale_rejected(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.APPROVED,
                                source_state=ReferenceSourceState.STALE))
        with pytest.raises(ReferenceLifecycleError):
            env["svc"].pin("ref-1")

    def test_pin_rejected_rejected(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.REJECTED))
        with pytest.raises(ReferenceLifecycleError):
            env["svc"].pin("ref-1")

    def test_pin_archived_rejected(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.ARCHIVED))
        with pytest.raises(ReferenceLifecycleError):
            env["svc"].pin("ref-1")

    def test_unpin(self, env):
        env["repo"].save(_asset(pinned=True))
        env["svc"].unpin("ref-1")
        assert env["repo"].get("ref-1").pinned is False

    def test_approve_stale_does_not_make_current(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.CANDIDATE,
                                source_state=ReferenceSourceState.STALE))
        env["svc"].approve("ref-1")
        a = env["repo"].get("ref-1")
        assert a.status == ReferenceStatus.APPROVED
        assert a.source_state == ReferenceSourceState.STALE

    def test_lifecycle_preserves_identity(self, env):
        env["repo"].save(_asset(status=ReferenceStatus.CANDIDATE,
                                content_sha256="c" * 64))
        env["svc"].approve("ref-1")
        a = env["repo"].get("ref-1")
        assert a.content_sha256 == "c" * 64
        assert a.kind == ReferenceKind.CHARACTER_BODY
        assert a.managed_path == "references/proj-1/ref-1/original.png"

    def test_missing_reference(self, env):
        from film_director.errors import ReferenceNotFoundError
        with pytest.raises(ReferenceNotFoundError):
            env["svc"].approve("nonexistent")


# ===========================================================================
# SELECTOR — priority and exclusion
# ===========================================================================

class TestSelector:
    def test_pinned_beats_unpinned(self):
        assets = [
            _asset(id="r1", pinned=False, created_at="2024-01-02T00:00:00"),
            _asset(id="r2", pinned=True, created_at="2024-01-01T00:00:00"),
        ]
        sel = ReferenceSelector()
        result = sel.select(shot=_shot(), project_id="proj-1",
                            kind=ReferenceKind.CHARACTER_BODY,
                            characters=_CHARS, assets=assets)
        assert result[0].id == "r2"

    def test_newest_wins_same_pin(self):
        assets = [
            _asset(id="r1", created_at="2024-01-01T00:00:00"),
            _asset(id="r2", created_at="2024-01-02T00:00:00"),
        ]
        sel = ReferenceSelector()
        result = sel.select(shot=_shot(), project_id="proj-1",
                            kind=ReferenceKind.CHARACTER_BODY,
                            characters=_CHARS, assets=assets)
        assert result[0].id == "r2"

    def test_id_asc_breaks_timestamp_tie(self):
        assets = [
            _asset(id="r-b", created_at="2024-01-01T00:00:00"),
            _asset(id="r-a", created_at="2024-01-01T00:00:00"),
        ]
        sel = ReferenceSelector()
        result = sel.select(shot=_shot(), project_id="proj-1",
                            kind=ReferenceKind.CHARACTER_BODY,
                            characters=_CHARS, assets=assets)
        assert result[0].id == "r-a"

    def test_candidate_excluded(self):
        assets = [_asset(id="r1", status=ReferenceStatus.CANDIDATE)]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_stale_excluded(self):
        assets = [_asset(id="r1", source_state=ReferenceSourceState.STALE)]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_rejected_excluded(self):
        assets = [_asset(id="r1", status=ReferenceStatus.REJECTED)]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_archived_excluded(self):
        assets = [_asset(id="r1", status=ReferenceStatus.ARCHIVED)]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_pinned_stale_excluded(self):
        assets = [_asset(id="r1", pinned=True, source_state=ReferenceSourceState.STALE)]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_wrong_project_excluded(self):
        assets = [_asset(id="r1", project_id="other-project")]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_wrong_character_excluded(self):
        assets = [_asset(id="r1", character_id="wrong-char")]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_wrong_kind_excluded(self):
        assets = [_asset(id="r1", kind=ReferenceKind.CHARACTER_FACE)]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=_CHARS, assets=assets)

    def test_multi_character_subject_order(self):
        chars = [_char("char-A", name="A"), _char("char-B", name="B")]
        subjects = [
            ShotSubject(character_id="char-A", name="A", ref_images=[]),
            ShotSubject(character_id="char-B", name="B", ref_images=[]),
        ]
        assets = [
            _asset(id="rB", character_id="char-B"),
            _asset(id="rA", character_id="char-A"),
        ]
        sel = ReferenceSelector()
        result = sel.select(shot=_shot(subjects=subjects), project_id="proj-1",
                            kind=ReferenceKind.CHARACTER_BODY,
                            characters=chars, assets=assets)
        assert result[0].id == "rA"
        assert result[1].id == "rB"

    def test_missing_ref_raises_no_partial(self):
        chars = [_char("char-A", name="A"), _char("char-B", name="B")]
        subjects = [
            ShotSubject(character_id="char-A", name="A", ref_images=[]),
            ShotSubject(character_id="char-B", name="B", ref_images=[]),
        ]
        assets = [_asset(id="rA", character_id="char-A")]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError, match="char-B"):
            sel.select(shot=_shot(subjects=subjects), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=chars, assets=assets)

    def test_deterministic_regardless_of_input_order(self):
        assets1 = [
            _asset(id="r1", created_at="2024-01-01T00:00:00"),
            _asset(id="r2", created_at="2024-01-02T00:00:00"),
        ]
        assets2 = [
            _asset(id="r2", created_at="2024-01-02T00:00:00"),
            _asset(id="r1", created_at="2024-01-01T00:00:00"),
        ]
        sel = ReferenceSelector()
        r1 = sel.select(shot=_shot(), project_id="proj-1",
                        kind=ReferenceKind.CHARACTER_BODY,
                        characters=_CHARS, assets=assets1)
        r2 = sel.select(shot=_shot(), project_id="proj-1",
                        kind=ReferenceKind.CHARACTER_BODY,
                        characters=_CHARS, assets=assets2)
        assert r1[0].id == r2[0].id

    def test_selector_does_not_mutate_inputs(self):
        assets = [_asset(id="r1")]
        shot = _shot()
        chars = list(_CHARS)
        assets_copy = copy.deepcopy(assets)
        shot_copy = copy.deepcopy(shot)
        sel = ReferenceSelector()
        sel.select(shot=shot, project_id="proj-1",
                   kind=ReferenceKind.CHARACTER_BODY,
                   characters=chars, assets=assets)
        assert assets[0].id == assets_copy[0].id
        assert shot.subjects[0].character_id == shot_copy.subjects[0].character_id


# ===========================================================================
# SELECTOR — canonical character validation
# ===========================================================================

class TestSelectorCharacterValidation:
    def test_exact_canonical_id_resolves(self):
        chars = [_char("char-1")]
        assets = [_asset(id="r1", character_id="char-1")]
        sel = ReferenceSelector()
        result = sel.select(shot=_shot(), project_id="proj-1",
                            kind=ReferenceKind.CHARACTER_BODY,
                            characters=chars, assets=assets)
        assert result[0].id == "r1"

    def test_subject_absent_from_characters_raises(self):
        """Subject has char-1 but characters list is empty → error."""
        chars = []  # no characters
        assets = [_asset(id="r1", character_id="char-1")]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError, match="char-1"):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=chars, assets=assets)

    def test_character_from_another_project_raises(self):
        """Character belongs to proj-2, not proj-1 → error."""
        chars = [_char("char-1", project_id="proj-2")]
        assets = [_asset(id="r1", character_id="char-1")]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError, match="char-1"):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=chars, assets=assets)

    def test_matching_name_different_id_does_not_resolve(self):
        """Character has same name but different ID → error."""
        chars = [_char("char-OTHER", project_id="proj-1", name="Hero")]
        assets = [_asset(id="r1", character_id="char-1")]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError, match="char-1"):
            sel.select(shot=_shot(), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=chars, assets=assets)

    def test_character_order_does_not_affect_selection(self):
        chars = [_char("char-B", name="B"), _char("char-A", name="A")]
        subjects = [
            ShotSubject(character_id="char-A", name="A", ref_images=[]),
            ShotSubject(character_id="char-B", name="B", ref_images=[]),
        ]
        assets = [
            _asset(id="rA", character_id="char-A"),
            _asset(id="rB", character_id="char-B"),
        ]
        sel = ReferenceSelector()
        result = sel.select(shot=_shot(subjects=subjects), project_id="proj-1",
                            kind=ReferenceKind.CHARACTER_BODY,
                            characters=chars, assets=assets)
        assert result[0].id == "rA"
        assert result[1].id == "rB"

    def test_later_subject_unresolved_no_partial(self):
        """First subject resolves, second doesn't → no partial result."""
        chars = [_char("char-A", name="A")]  # char-B missing
        subjects = [
            ShotSubject(character_id="char-A", name="A", ref_images=[]),
            ShotSubject(character_id="char-B", name="B", ref_images=[]),
        ]
        assets = [
            _asset(id="rA", character_id="char-A"),
            _asset(id="rB", character_id="char-B"),
        ]
        sel = ReferenceSelector()
        with pytest.raises(ReferenceResolutionError, match="char-B"):
            sel.select(shot=_shot(subjects=subjects), project_id="proj-1",
                       kind=ReferenceKind.CHARACTER_BODY,
                       characters=chars, assets=assets)
