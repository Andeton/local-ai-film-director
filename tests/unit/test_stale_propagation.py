"""Tests for StalePropagator (Task 6 / M2.F).

TDD: written before implementation. Tests cover:
1. Scene cascade (4): beats+shots+plans outdated, sibling unaffected, idempotent, rows preserved
2. Character cascade (4): matching char_id, same-name different ID, unrelated, beat NOT outdated
3. Beat cascade (3): shots+plans outdated, beat unchanged, sibling unaffected
4. Shot cascade (3): plan outdated, shot unchanged, sibling unaffected
5. Project cascade (3): all outdated, second project unaffected, idempotent
6. Transaction (3): shared conn, rollback on failure, absent conn
7. Invariants (3): versions unchanged, no deletion, already-outdated not counted
8. Defensive (1): current shot under already-outdated beat still marked
"""
import sqlite3
from unittest.mock import patch

import pytest

from film_director.models.canonical import (
    Beat,
    CameraIntent,
    GenerationPlan,
    ProductionProject,
    ReferenceRequirements,
    Scene,
    Sequence,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    GenerationPlanRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.enrichment.stale_propagator import StalePropagator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _project(id="p1", wc="wc1", title="Film", **kw) -> ProductionProject:
    return ProductionProject(
        id=id, wc_project_id=wc, title=title,
        created_at="t", updated_at="t",
        provenance=_prov(source_asset_id=wc),
        **kw,
    )


def _sequence(id="sq1", project_id="p1", name="Seq", order_index=0) -> Sequence:
    return Sequence(id=id, project_id=project_id, name=name, order_index=order_index)


def _scene(id="s1", sequence_id="sq1", wc_scene_id="ws1", name="Scene", **kw) -> Scene:
    return Scene(
        id=id, sequence_id=sequence_id, wc_scene_id=wc_scene_id,
        name=name, location="L", description="D", order_index=0,
        provenance=_prov(),
        **kw,
    )


def _beat(id="b1", scene_id="s1", **kw) -> Beat:
    defaults = dict(
        dramatic_action="Hero enters",
        character_intention="Seek truth",
        change="Discovers clue",
        characters=["char-1"],
        order_index=0,
        status="draft",
        source="llm",
        version=1,
        created_at="t",
        updated_at="t",
    )
    defaults.update(kw)
    return Beat(id=id, scene_id=scene_id, **defaults)


def _shot(id="sh1", beat_id="b1", **kw) -> ShotSpecificationV1:
    defaults = dict(
        dramatic_purpose="Reveal hero",
        subjects=[
            ShotSubject(character_id="c1", name="Hero", ref_images=["ref1.png"]),
        ],
        action="Hero walks in",
        environment={"setting": "forest"},
        camera=CameraIntent(shot_size="medium", angle="eye_level", movement="dolly_in"),
        lighting={"key": "low"},
        audio_intent={"music": "tense"},
        duration_sec=4.5,
        continuity_inputs={},
        order_index=0,
        status="draft",
        source="generated",
        version=1,
        created_at="t",
        updated_at="t",
    )
    defaults.update(kw)
    return ShotSpecificationV1(id=id, beat_id=beat_id, **defaults)


def _plan(id="gp1", shot_id="sh1", **kw) -> GenerationPlan:
    defaults = dict(
        shot_version=1,
        strategy="TEXT_TO_VIDEO",
        reference_requirements=ReferenceRequirements(),
        duration_sec=4.5,
        resolution_intent={"width": 1280, "height": 720},
        seed_policy="random",
        seed=None,
        continuity_mode="none",
        selection_reason="No storyboard",
        status="draft",
        version=1,
        created_at="t",
        updated_at="t",
    )
    defaults.update(kw)
    return GenerationPlan(id=id, shot_id=shot_id, **defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def repos(db):
    """All repos."""
    return {
        "db": db,
        "proj_repo": ProjectRepository(db),
        "seq_repo": SequenceRepository(db),
        "scene_repo": SceneRepository(db),
        "beat_repo": BeatRepository(db),
        "shot_repo": ShotRepository(db),
        "plan_repo": GenerationPlanRepository(db),
    }


@pytest.fixture
def full_hierarchy(repos):
    """Create: project -> sequence -> scene -> beat -> shot -> plan.

    Also creates a sibling scene (s2) with its own beat/shot/plan chain,
    to verify isolation.
    """
    pr, sr, scr = repos["proj_repo"], repos["seq_repo"], repos["scene_repo"]
    br, shr, plr = repos["beat_repo"], repos["shot_repo"], repos["plan_repo"]

    pr.save_project(_project())
    sr.save_sequence(_sequence())

    # Main scene chain
    scr.save_scene(_scene(id="s1", wc_scene_id="ws1"))
    br.save_beat(_beat(id="b1", scene_id="s1"))
    br.save_beat(_beat(id="b2", scene_id="s1", order_index=1))
    shr.save_shot(_shot(id="sh1", beat_id="b1"))
    shr.save_shot(_shot(id="sh2", beat_id="b2",
                        subjects=[ShotSubject(character_id="c2", name="Villain")]))
    plr.save_plan(_plan(id="gp1", shot_id="sh1"))
    plr.save_plan(_plan(id="gp2", shot_id="sh2"))

    # Sibling scene chain (should be unaffected by scene-level cascade on s1)
    scr.save_scene(_scene(id="s2", wc_scene_id="ws2"))
    br.save_beat(_beat(id="b3", scene_id="s2"))
    shr.save_shot(_shot(id="sh3", beat_id="b3",
                        subjects=[ShotSubject(character_id="c1", name="Hero")]))
    plr.save_plan(_plan(id="gp3", shot_id="sh3"))

    return repos


@pytest.fixture
def propagator(full_hierarchy):
    db = full_hierarchy["db"]
    return StalePropagator(
        db=db,
        beat_repo=full_hierarchy["beat_repo"],
        shot_repo=full_hierarchy["shot_repo"],
        plan_repo=full_hierarchy["plan_repo"],
        sequence_repo=full_hierarchy["seq_repo"],
        scene_repo=full_hierarchy["scene_repo"],
    )


# ---------------------------------------------------------------------------
# Scene cascade (4)
# ---------------------------------------------------------------------------

class TestSceneCascade:
    def test_scene_stale_marks_beats_shots_plans(self, propagator, full_hierarchy):
        """Scene s1: b1->sh1->gp1, b2->sh2->gp2 all become outdated."""
        count = propagator.propagate_scene_stale("s1")
        br = full_hierarchy["beat_repo"]
        shr = full_hierarchy["shot_repo"]
        plr = full_hierarchy["plan_repo"]
        assert br.get_beat("b1").status == "outdated"
        assert br.get_beat("b2").status == "outdated"
        assert shr.get_shot("sh1").status == "outdated"
        assert shr.get_shot("sh2").status == "outdated"
        assert plr.get_plans_by_shot("sh1")[0].status == "outdated"
        assert plr.get_plans_by_shot("sh2")[0].status == "outdated"
        # 2 beats + 2 shots + 2 plans = 6
        assert count == 6

    def test_sibling_scene_unaffected(self, propagator, full_hierarchy):
        """Scene s2 hierarchy should remain draft after s1 cascade."""
        propagator.propagate_scene_stale("s1")
        br = full_hierarchy["beat_repo"]
        shr = full_hierarchy["shot_repo"]
        plr = full_hierarchy["plan_repo"]
        assert br.get_beat("b3").status == "draft"
        assert shr.get_shot("sh3").status == "draft"
        assert plr.get_plans_by_shot("sh3")[0].status == "draft"

    def test_scene_stale_idempotent(self, propagator):
        """Second call returns 0 since everything is already outdated."""
        propagator.propagate_scene_stale("s1")
        count = propagator.propagate_scene_stale("s1")
        assert count == 0

    def test_rows_preserved_after_cascade(self, propagator, full_hierarchy):
        """Rows are still loadable by ID after cascade (no deletion)."""
        propagator.propagate_scene_stale("s1")
        assert full_hierarchy["beat_repo"].get_beat("b1") is not None
        assert full_hierarchy["shot_repo"].get_shot("sh1") is not None
        assert full_hierarchy["plan_repo"].get_plans_by_shot("sh1") != []


# ---------------------------------------------------------------------------
# Character cascade (4)
# ---------------------------------------------------------------------------

class TestCharacterCascade:
    def test_matching_character_id_marks_shot_and_plan(self, propagator, full_hierarchy):
        """Character c1 appears in sh1 (scene s1) and sh3 (scene s2)."""
        count = propagator.propagate_character_stale("c1", "p1")
        shr = full_hierarchy["shot_repo"]
        plr = full_hierarchy["plan_repo"]
        assert shr.get_shot("sh1").status == "outdated"
        assert shr.get_shot("sh3").status == "outdated"
        assert plr.get_plans_by_shot("sh1")[0].status == "outdated"
        assert plr.get_plans_by_shot("sh3")[0].status == "outdated"
        # 2 shots + 2 plans = 4
        assert count == 4

    def test_same_name_different_character_id_unaffected(self, propagator, full_hierarchy):
        """A character with the same name 'Hero' but different ID should not affect sh1."""
        count = propagator.propagate_character_stale("c999", "p1")
        assert count == 0
        assert full_hierarchy["shot_repo"].get_shot("sh1").status == "draft"

    def test_unrelated_character_unaffected(self, propagator, full_hierarchy):
        """Character not in any shot subjects."""
        count = propagator.propagate_character_stale("nonexistent", "p1")
        assert count == 0

    def test_beat_not_marked_for_character_change(self, propagator, full_hierarchy):
        """Character cascade should NOT mark beats outdated."""
        propagator.propagate_character_stale("c1", "p1")
        assert full_hierarchy["beat_repo"].get_beat("b1").status == "draft"
        assert full_hierarchy["beat_repo"].get_beat("b3").status == "draft"


# ---------------------------------------------------------------------------
# Beat cascade (3)
# ---------------------------------------------------------------------------

class TestBeatCascade:
    def test_beat_shots_and_plans_outdated(self, propagator, full_hierarchy):
        """Beat b1 -> sh1 -> gp1 should be outdated."""
        count = propagator.propagate_beat_stale("b1")
        assert full_hierarchy["shot_repo"].get_shot("sh1").status == "outdated"
        assert full_hierarchy["plan_repo"].get_plans_by_shot("sh1")[0].status == "outdated"
        # 1 shot + 1 plan = 2
        assert count == 2

    def test_beat_itself_unchanged(self, propagator, full_hierarchy):
        """propagate_beat_stale does NOT mark the beat itself."""
        propagator.propagate_beat_stale("b1")
        assert full_hierarchy["beat_repo"].get_beat("b1").status == "draft"

    def test_sibling_beat_unaffected(self, propagator, full_hierarchy):
        """b2's hierarchy should be untouched when b1 cascades."""
        propagator.propagate_beat_stale("b1")
        assert full_hierarchy["shot_repo"].get_shot("sh2").status == "draft"
        assert full_hierarchy["plan_repo"].get_plans_by_shot("sh2")[0].status == "draft"


# ---------------------------------------------------------------------------
# Shot cascade (3)
# ---------------------------------------------------------------------------

class TestShotCascade:
    def test_shot_plan_outdated(self, propagator, full_hierarchy):
        """Shot sh1 -> gp1 should be outdated."""
        count = propagator.propagate_shot_stale("sh1")
        assert full_hierarchy["plan_repo"].get_plans_by_shot("sh1")[0].status == "outdated"
        assert count == 1

    def test_shot_itself_unchanged(self, propagator, full_hierarchy):
        """propagate_shot_stale does NOT mark the shot itself."""
        propagator.propagate_shot_stale("sh1")
        assert full_hierarchy["shot_repo"].get_shot("sh1").status == "draft"

    def test_sibling_shot_plan_unaffected(self, propagator, full_hierarchy):
        """sh2's plan should be untouched when sh1 cascades."""
        propagator.propagate_shot_stale("sh1")
        assert full_hierarchy["plan_repo"].get_plans_by_shot("sh2")[0].status == "draft"


# ---------------------------------------------------------------------------
# Project cascade (3)
# ---------------------------------------------------------------------------

class TestProjectCascade:
    def test_all_beats_shots_plans_outdated(self, propagator, full_hierarchy):
        """All M2 artifacts across the project become outdated."""
        count = propagator.propagate_project_stale("p1")
        br = full_hierarchy["beat_repo"]
        shr = full_hierarchy["shot_repo"]
        plr = full_hierarchy["plan_repo"]
        for bid in ("b1", "b2", "b3"):
            assert br.get_beat(bid).status == "outdated"
        for sid in ("sh1", "sh2", "sh3"):
            assert shr.get_shot(sid).status == "outdated"
        for gid in ("gp1", "gp2", "gp3"):
            plans = plr.get_plans_by_shot(
                {"gp1": "sh1", "gp2": "sh2", "gp3": "sh3"}[gid]
            )
            assert any(p.id == gid and p.status == "outdated" for p in plans)
        # 3 beats + 3 shots + 3 plans = 9
        assert count == 9

    def test_second_project_unaffected(self, full_hierarchy, propagator):
        """Create a second project; it should be untouched."""
        pr = full_hierarchy["proj_repo"]
        sr = full_hierarchy["seq_repo"]
        scr = full_hierarchy["scene_repo"]
        br = full_hierarchy["beat_repo"]
        shr = full_hierarchy["shot_repo"]
        plr = full_hierarchy["plan_repo"]

        pr.save_project(_project(id="p2", wc="wc2", title="Film2"))
        sr.save_sequence(_sequence(id="sq2", project_id="p2"))
        scr.save_scene(_scene(id="s3", sequence_id="sq2", wc_scene_id="ws3"))
        br.save_beat(_beat(id="b4", scene_id="s3"))
        shr.save_shot(_shot(id="sh4", beat_id="b4"))
        plr.save_plan(_plan(id="gp4", shot_id="sh4"))

        propagator.propagate_project_stale("p1")

        assert br.get_beat("b4").status == "draft"
        assert shr.get_shot("sh4").status == "draft"
        assert plr.get_plans_by_shot("sh4")[0].status == "draft"

    def test_project_stale_idempotent(self, propagator):
        """Second call returns 0."""
        propagator.propagate_project_stale("p1")
        count = propagator.propagate_project_stale("p1")
        assert count == 0


# ---------------------------------------------------------------------------
# Transaction (3)
# ---------------------------------------------------------------------------

class TestTransaction:
    def test_shared_conn_no_commit(self, full_hierarchy, propagator):
        """When a conn is passed, propagator uses it without committing."""
        db = full_hierarchy["db"]
        with db.connection() as conn:
            propagator.propagate_scene_stale("s1", conn=conn)
            # Not yet committed by propagator; we can still read within conn
            row = conn.execute("SELECT status FROM beats WHERE id = 'b1'").fetchone()
            assert row["status"] == "outdated"
            # Rollback to prove propagator did not commit
            conn.rollback()
        # After rollback, beat should be back to draft
        assert full_hierarchy["beat_repo"].get_beat("b1").status == "draft"

    def test_rollback_on_failure(self, full_hierarchy):
        """If an error occurs mid-cascade, all changes roll back."""
        db = full_hierarchy["db"]
        br = full_hierarchy["beat_repo"]
        shr = full_hierarchy["shot_repo"]
        plr = full_hierarchy["plan_repo"]

        # Create propagator with a shot_repo that fails on mark_shots_outdated_by_beat
        prop = StalePropagator(
            db=db,
            beat_repo=br,
            shot_repo=shr,
            plan_repo=plr,
            sequence_repo=full_hierarchy["seq_repo"],
            scene_repo=full_hierarchy["scene_repo"],
        )

        with patch.object(shr, "mark_shots_outdated_by_beat", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError, match="injected"):
                prop.propagate_scene_stale("s1")

        # Everything should be unchanged due to rollback
        assert br.get_beat("b1").status == "draft"
        assert shr.get_shot("sh1").status == "draft"
        assert plr.get_plans_by_shot("sh1")[0].status == "draft"

    def test_absent_conn_opens_own_transaction(self, propagator, full_hierarchy):
        """When no conn is passed, propagator opens its own and commits."""
        propagator.propagate_beat_stale("b1")
        # Changes should persist (committed)
        assert full_hierarchy["shot_repo"].get_shot("sh1").status == "outdated"
        assert full_hierarchy["plan_repo"].get_plans_by_shot("sh1")[0].status == "outdated"


# ---------------------------------------------------------------------------
# Invariants (3)
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_versions_unchanged(self, propagator, full_hierarchy):
        """Version fields should not change after cascade."""
        propagator.propagate_scene_stale("s1")
        assert full_hierarchy["beat_repo"].get_beat("b1").version == 1
        assert full_hierarchy["shot_repo"].get_shot("sh1").version == 1
        assert full_hierarchy["plan_repo"].get_plans_by_shot("sh1")[0].version == 1

    def test_no_physical_deletion(self, propagator, full_hierarchy):
        """All rows remain loadable after cascade."""
        propagator.propagate_project_stale("p1")
        db = full_hierarchy["db"]
        with db.connection() as conn:
            beats = conn.execute("SELECT COUNT(*) as c FROM beats").fetchone()["c"]
            shots = conn.execute("SELECT COUNT(*) as c FROM shots").fetchone()["c"]
            plans = conn.execute("SELECT COUNT(*) as c FROM generation_plans").fetchone()["c"]
        assert beats == 3
        assert shots == 3
        assert plans == 3

    def test_already_outdated_not_counted(self, propagator, full_hierarchy):
        """Pre-outdated rows should not contribute to the count."""
        # Mark b1 outdated manually
        full_hierarchy["beat_repo"].mark_outdated("b1")
        full_hierarchy["shot_repo"].mark_outdated("sh1")
        full_hierarchy["plan_repo"].mark_outdated("gp1")
        # Now cascade scene s1 — b1/sh1/gp1 already outdated
        count = propagator.propagate_scene_stale("s1")
        # Only b2, sh2, gp2 should be newly outdated = 3
        assert count == 3


# ---------------------------------------------------------------------------
# Defensive (1)
# ---------------------------------------------------------------------------

class TestDefensive:
    def test_current_shot_under_outdated_beat_still_marked(self, full_hierarchy):
        """A shot that is still 'draft' under an already-outdated beat
        should still get marked outdated by scene/project cascade."""
        br = full_hierarchy["beat_repo"]
        shr = full_hierarchy["shot_repo"]
        plr = full_hierarchy["plan_repo"]
        db = full_hierarchy["db"]

        # Mark beat b1 as outdated, but leave shot sh1 as draft
        br.mark_outdated("b1")

        prop = StalePropagator(
            db=db,
            beat_repo=br,
            shot_repo=shr,
            plan_repo=plr,
            sequence_repo=full_hierarchy["seq_repo"],
            scene_repo=full_hierarchy["scene_repo"],
        )

        count = prop.propagate_scene_stale("s1")
        # b1 already outdated (not counted), but sh1+gp1 under it should be marked
        # b2 + sh2 + gp2 still current = 3
        # sh1 + gp1 still current = 2
        # Total newly outdated = 5 (b2, sh2, gp2, sh1, gp1)
        assert shr.get_shot("sh1").status == "outdated"
        assert plr.get_plans_by_shot("sh1")[0].status == "outdated"
        assert count == 5
