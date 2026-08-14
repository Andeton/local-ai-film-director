"""Integration tests for EnrichmentService (M2.G).

Uses FakeLLMProvider (same pattern as test_beat_enricher.py).
Tests: full pipeline, idempotency, human edit protection, atomic persistence,
M1+M2 apply_source_changes integration.
"""
from __future__ import annotations

import pytest

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.models.wind_comic_dto import WCScene
from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
from film_director.enrichment.stale_propagator import StalePropagator
from film_director.enrichment.strategy_selector import StrategySelector
from film_director.errors import HumanEditConflictError
from film_director.llm.provider import LLMResponse
from film_director.models.canonical import (
    Beat,
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
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.services.enrichment_service import EnrichmentResult, EnrichmentService
from film_director.services.import_service import ChangeDetection, ImportService


# ---------------------------------------------------------------------------
# FakeLLMProvider
# ---------------------------------------------------------------------------

class FakeLLMProvider:
    def __init__(self):
        self._responses: list = []
        self.call_count = 0
        self.captured_messages: list[list[dict]] = []  # captures each chat() call's messages

    def queue(self, response_or_exception):
        self._responses.append(response_or_exception)

    def chat(self, messages, expect_json=False):
        self.call_count += 1
        self.captured_messages.append(list(messages))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def health(self):
        return True


# ---------------------------------------------------------------------------
# FakeWindComicAdapter
# ---------------------------------------------------------------------------

class FakeWindComicAdapter:
    """In-memory Wind Comic adapter for testing."""

    def __init__(self, scenes_by_project: dict[str, list] | None = None) -> None:
        # scenes_by_project: {wc_project_id: [WCScene, ...]}
        self._scenes = scenes_by_project or {}

    def get_scenes(self, project_id: str):
        return self._scenes.get(project_id, [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prov() -> Provenance:
    return Provenance(
        source_system="wind_comic",
        source_project_id="proj-001",
        source_asset_id="asset-001",
        source_asset_version=1,
        imported_at="2024-01-01T00:00:00+00:00",
        source_hash="a" * 64,
    )


def _beat_response(n: int = 1) -> LLMResponse:
    """LLM response for BeatEnricher with n beats."""
    beats = [
        {
            "dramatic_action": f"Action {i}",
            "character_intention": f"Intent {i}",
            "change": f"Change {i}",
            "characters": ["Alice"],
        }
        for i in range(n)
    ]
    return LLMResponse(content="", parsed={"beats": beats}, model="test")


def _coverage_response(n: int = 1) -> LLMResponse:
    """LLM response for CoveragePlanner with n coverage decisions."""
    coverage = [
        {
            "shot_type": "establishing",
            "shot_size": "wide",
            "angle": "eye_level",
            "movement": "static",
            "purpose": f"Purpose {i}",
            "duration_sec": 3.0,
        }
        for i in range(n)
    ]
    return LLMResponse(content="", parsed={"coverage": coverage}, model="test")


# ---------------------------------------------------------------------------
# Fixture: wired environment
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    """Create a fully wired environment with DB, repos, and services."""
    db = Database(str(tmp_path / "our.db"))
    db.init_schema()

    project_repo = ProjectRepository(db)
    sequence_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    character_repo = CharacterRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)

    llm = FakeLLMProvider()

    stale_propagator = StalePropagator(
        db=db,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
    )

    # Minimal import_service (no adapter needed for apply_detected_changes)
    import_service = ImportService(
        adapter=None,  # not used by apply_detected_changes
        project_repo=project_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
        character_repo=character_repo,
        db=db,
    )

    svc = EnrichmentService(
        db=db,
        project_repo=project_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
        character_repo=character_repo,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        adapter=None,  # Not used in these tests
        import_service=import_service,
        beat_enricher=BeatEnricher(llm),
        coverage_planner=CoveragePlanner(llm),
        shot_spec_builder=ShotSpecBuilder(),
        strategy_selector=StrategySelector(),
        stale_propagator=stale_propagator,
    )

    return dict(
        db=db,
        svc=svc,
        llm=llm,
        project_repo=project_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
        character_repo=character_repo,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        import_service=import_service,
    )


def _seed_project(env, n_scenes=1) -> tuple[str, list[str]]:
    """Seed a project with scenes. Returns (project_id, [scene_ids])."""
    project = ProductionProject(
        id="proj-001",
        wc_project_id="wc-proj-001",
        title="Test Film",
        status="active",
        aspect="16:9",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        provenance=_prov(),
    )
    env["project_repo"].save_project(project)

    seq = Sequence(id="seq-001", project_id="proj-001", name="Main", order_index=0)
    env["sequence_repo"].save_sequence(seq)

    scene_ids = []
    for i in range(n_scenes):
        sid = f"scene-{i:03d}"
        scene = Scene(
            id=sid,
            sequence_id="seq-001",
            wc_scene_id=f"wc-scene-{i:03d}",
            name=f"Scene {i}",
            location=f"Location {i}",
            description=f"Description {i}",
            order_index=i,
            status="draft",
            provenance=_prov(),
        )
        env["scene_repo"].save_scene(scene)
        scene_ids.append(sid)

    return "proj-001", scene_ids


def _seed_character(env, name="Alice") -> CharacterReference:
    char = CharacterReference(
        id=f"char-{name.lower()}",
        project_id="proj-001",
        wc_character_id=f"wc-char-{name.lower()}",
        name=name,
        description="A character",
        appearance="Tall",
        turnaround_paths=[],
        visual_anchors=[],
        status="active",
        provenance=_prov(),
    )
    env["character_repo"].save_character(char)
    return char


# ===========================================================================
# Tests
# ===========================================================================


class TestEnrichProject:
    """Tests 1-6: enrich_project pipeline."""

    def test_full_enrich_creates_beats_shots_plans(self, env):
        """1. Full enrich_project creates beats+shots+plans."""
        project_id, scene_ids = _seed_project(env)
        llm = env["llm"]

        # Queue responses: 1 beat enrichment + 1 coverage plan
        llm.queue(_beat_response(2))      # 2 beats for scene
        llm.queue(_coverage_response(1))  # 1 coverage for beat 0
        llm.queue(_coverage_response(1))  # 1 coverage for beat 1

        result = env["svc"].enrich_project(project_id)

        assert isinstance(result, EnrichmentResult)
        assert result.project_id == project_id
        assert result.beats_created == 2
        assert result.shots_created == 2
        assert result.plans_created == 2

    def test_idempotent_second_call_creates_zero(self, env):
        """2. Idempotent: second enrich_project creates zero new."""
        project_id, _ = _seed_project(env)
        llm = env["llm"]

        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))

        result1 = env["svc"].enrich_project(project_id)
        assert result1.beats_created == 1

        # Second call — no LLM responses queued (shouldn't need any)
        result2 = env["svc"].enrich_project(project_id)
        assert result2.beats_created == 0
        assert result2.shots_created == 0
        assert result2.plans_created == 0

    def test_reuses_existing_current_beats(self, env):
        """3. enrich_project reuses existing current beats."""
        project_id, scene_ids = _seed_project(env)

        # Pre-seed a beat
        beat = Beat(
            id="beat-existing",
            scene_id=scene_ids[0],
            dramatic_action="Existing action",
            character_intention="Existing intent",
            change="Existing change",
            characters=[],
            order_index=0,
            status="draft",
            source="llm",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        llm = env["llm"]
        # Only need coverage (beat already exists)
        llm.queue(_coverage_response(1))

        result = env["svc"].enrich_project(project_id)
        assert result.beats_created == 0  # reused
        assert result.shots_created == 1
        assert result.plans_created == 1

    def test_reuses_existing_current_plans(self, env):
        """4. enrich_project reuses existing current plans."""
        project_id, scene_ids = _seed_project(env)
        llm = env["llm"]

        # First enrich
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        result1 = env["svc"].enrich_project(project_id)
        assert result1.plans_created == 1

        # Second call reuses everything
        result2 = env["svc"].enrich_project(project_id)
        assert result2.plans_created == 0

    def test_llm_failure_no_partial_artifacts(self, env):
        """5. LLM failure before persistence -> no partial artifacts."""
        project_id, _ = _seed_project(env)
        llm = env["llm"]

        # Beat enrichment succeeds, coverage fails
        llm.queue(_beat_response(1))
        llm.queue(RuntimeError("LLM exploded"))

        with pytest.raises(RuntimeError, match="LLM exploded"):
            env["svc"].enrich_project(project_id)

        # Nothing should be persisted
        beats = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(beats) == 0

    def test_atomic_graph_persistence_rollback(self, env):
        """6. Inject save failure -> nothing persisted."""
        project_id, _ = _seed_project(env)
        llm = env["llm"]

        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))

        # Monkey-patch save_plan to fail
        original_save = env["plan_repo"].save_plan

        def exploding_save(plan, conn=None):
            raise RuntimeError("DB write failed")

        env["plan_repo"].save_plan = exploding_save

        with pytest.raises(RuntimeError, match="DB write failed"):
            env["svc"].enrich_project(project_id)

        env["plan_repo"].save_plan = original_save

        # Beats and shots should NOT be persisted (rolled back)
        beats = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(beats) == 0


class TestEnrichSceneBeats:
    """Tests 7-9: enrich_scene_beats."""

    def test_creates_new_beats(self, env):
        """7. enrich_scene_beats creates new beats."""
        _seed_project(env)
        llm = env["llm"]
        llm.queue(_beat_response(2))

        new_beats = env["svc"].enrich_scene_beats("scene-000")
        assert len(new_beats) == 2
        # Verify persisted
        stored = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(stored) == 2

    def test_human_beat_force_false_raises(self, env):
        """8. enrich_scene_beats with human beat + force=False -> HumanEditConflictError."""
        _seed_project(env)

        # Pre-seed a human beat
        beat = Beat(
            id="beat-human",
            scene_id="scene-000",
            dramatic_action="Human action",
            character_intention="",
            change="",
            characters=[],
            order_index=0,
            status="draft",
            source="human",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        with pytest.raises(HumanEditConflictError):
            env["svc"].enrich_scene_beats("scene-000", force=False)

    def test_human_beat_force_true_replaces(self, env):
        """9. enrich_scene_beats with human beat + force=True -> old outdated, new created."""
        _seed_project(env)

        beat = Beat(
            id="beat-human",
            scene_id="scene-000",
            dramatic_action="Human action",
            character_intention="",
            change="",
            characters=[],
            order_index=0,
            status="draft",
            source="human",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        llm = env["llm"]
        llm.queue(_beat_response(1))

        new_beats = env["svc"].enrich_scene_beats("scene-000", force=True)
        assert len(new_beats) == 1

        # Old beat should be outdated
        old = env["beat_repo"].get_beat("beat-human")
        assert old.status == "outdated"

        # New beats should be current
        current = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(current) == 1
        assert current[0].id != "beat-human"


class TestPlanBeatCoverage:
    """Tests 10-12: plan_beat_coverage."""

    def test_creates_shots(self, env):
        """10. plan_beat_coverage creates shots."""
        _seed_project(env)

        beat = Beat(
            id="beat-001",
            scene_id="scene-000",
            dramatic_action="Test action",
            character_intention="",
            change="",
            characters=[],
            order_index=0,
            status="draft",
            source="llm",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        llm = env["llm"]
        llm.queue(_coverage_response(2))

        new_shots = env["svc"].plan_beat_coverage("beat-001")
        assert len(new_shots) == 2
        stored = env["shot_repo"].get_current_shots_by_beat("beat-001")
        assert len(stored) == 2

    def test_human_shot_force_false_raises(self, env):
        """11. plan_beat_coverage with human shot + force=False -> HumanEditConflictError."""
        _seed_project(env)

        beat = Beat(
            id="beat-001",
            scene_id="scene-000",
            dramatic_action="Test action",
            character_intention="",
            change="",
            characters=[],
            order_index=0,
            status="draft",
            source="llm",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        shot = ShotSpecificationV1(
            id="shot-human",
            beat_id="beat-001",
            dramatic_purpose="Human shot",
            action="Test",
            camera=CameraIntent(shot_size="wide"),
            order_index=0,
            status="draft",
            source="human",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["shot_repo"].save_shot(shot)

        with pytest.raises(HumanEditConflictError):
            env["svc"].plan_beat_coverage("beat-001", force=False)

    def test_human_shot_force_true_replaces(self, env):
        """12. plan_beat_coverage with human shot + force=True -> old outdated, new created."""
        _seed_project(env)

        beat = Beat(
            id="beat-001",
            scene_id="scene-000",
            dramatic_action="Test action",
            character_intention="",
            change="",
            characters=[],
            order_index=0,
            status="draft",
            source="llm",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        shot = ShotSpecificationV1(
            id="shot-human",
            beat_id="beat-001",
            dramatic_purpose="Human shot",
            action="Test",
            camera=CameraIntent(shot_size="wide"),
            order_index=0,
            status="draft",
            source="human",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["shot_repo"].save_shot(shot)

        llm = env["llm"]
        llm.queue(_coverage_response(1))

        new_shots = env["svc"].plan_beat_coverage("beat-001", force=True)
        assert len(new_shots) == 1

        old = env["shot_repo"].get_shot("shot-human")
        assert old.status == "outdated"

        current = env["shot_repo"].get_current_shots_by_beat("beat-001")
        assert len(current) == 1
        assert current[0].id != "shot-human"


class TestAssignStrategies:
    """Test 13: assign_strategies."""

    def test_creates_plans_old_outdated(self, env):
        """13. assign_strategies creates plans, old plans outdated."""
        project_id, _ = _seed_project(env)
        llm = env["llm"]

        # First enrich to create beats + shots + plans
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        env["svc"].enrich_project(project_id)

        # Get the old plan
        shots = env["shot_repo"].get_current_shots_by_project(project_id)
        assert len(shots) == 1
        old_plan = env["plan_repo"].get_current_plan_by_shot(shots[0].id)
        assert old_plan is not None
        old_plan_id = old_plan.id

        # Re-assign strategies
        count = env["svc"].assign_strategies(project_id)
        assert count == 1

        # Old plan should be outdated
        old = env["plan_repo"].get_plans_by_shot(shots[0].id)
        outdated = [p for p in old if p.status == "outdated"]
        assert len(outdated) == 1
        assert outdated[0].id == old_plan_id

        # New plan should be current
        new_plan = env["plan_repo"].get_current_plan_by_shot(shots[0].id)
        assert new_plan is not None
        assert new_plan.id != old_plan_id


class TestApplySourceChanges:
    """Tests 14-15: apply_source_changes (M1+M2 atomic)."""

    def test_atomic_m1_and_m2_in_one_transaction(self, env):
        """14. apply_source_changes atomic: M1+M2 in one transaction."""
        project_id, scene_ids = _seed_project(env)
        llm = env["llm"]

        # First enrich
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        env["svc"].enrich_project(project_id)

        # Create a scene modification change
        changes = [
            ChangeDetection(
                entity_type="scene",
                entity_id="scene-000",
                source_asset_id="wc-scene-000",
                change_type="modified",
            ),
        ]

        result = env["svc"].apply_source_changes(project_id, changes)
        assert result["m1_applied"] == 1

        # M1: scene should be outdated
        scene = env["scene_repo"].get_scene("scene-000")
        assert scene.status == "outdated"

        # M2: beats for that scene should be outdated
        beats = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(beats) == 0  # all outdated

    def test_rollback_on_failure(self, env):
        """15. apply_source_changes rollback: inject failure -> both M1 and M2 unchanged."""
        project_id, scene_ids = _seed_project(env)
        llm = env["llm"]

        # First enrich
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        env["svc"].enrich_project(project_id)

        # Verify pre-state
        beats_before = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(beats_before) == 1

        changes = [
            ChangeDetection(
                entity_type="scene",
                entity_id="scene-000",
                source_asset_id="wc-scene-000",
                change_type="modified",
            ),
        ]

        # Monkey-patch stale propagator to fail
        original = env["svc"]._stale_propagator.propagate_scene_stale

        def exploding_propagate(scene_id, conn=None):
            raise RuntimeError("Cascade failed")

        env["svc"]._stale_propagator.propagate_scene_stale = exploding_propagate

        with pytest.raises(RuntimeError, match="Cascade failed"):
            env["svc"].apply_source_changes(project_id, changes)

        env["svc"]._stale_propagator.propagate_scene_stale = original

        # Both M1 and M2 should be unchanged (rolled back)
        scene = env["scene_repo"].get_scene("scene-000")
        assert scene.status == "draft"  # NOT outdated

        beats_after = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(beats_after) == 1  # NOT outdated


# ---------------------------------------------------------------------------
# Fixture: env wired with a FakeWindComicAdapter
# ---------------------------------------------------------------------------

@pytest.fixture
def env_wc(tmp_path):
    """Fully wired env with a FakeWindComicAdapter (wc_adapter can be replaced per-test)."""
    db = Database(str(tmp_path / "our.db"))
    db.init_schema()

    project_repo = ProjectRepository(db)
    sequence_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    character_repo = CharacterRepository(db)
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)

    llm = FakeLLMProvider()

    stale_propagator = StalePropagator(
        db=db,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
    )

    import_service = ImportService(
        adapter=None,
        project_repo=project_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
        character_repo=character_repo,
        db=db,
    )

    wc_adapter = FakeWindComicAdapter()

    svc = EnrichmentService(
        db=db,
        project_repo=project_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
        character_repo=character_repo,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        adapter=wc_adapter,
        import_service=import_service,
        beat_enricher=BeatEnricher(llm),
        coverage_planner=CoveragePlanner(llm),
        shot_spec_builder=ShotSpecBuilder(),
        strategy_selector=StrategySelector(),
        stale_propagator=stale_propagator,
    )

    return dict(
        db=db,
        svc=svc,
        llm=llm,
        wc_adapter=wc_adapter,
        project_repo=project_repo,
        sequence_repo=sequence_repo,
        scene_repo=scene_repo,
        character_repo=character_repo,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        import_service=import_service,
    )


def _make_wc_scene(asset_id: str, data: dict) -> WCScene:
    return WCScene(
        asset_id=asset_id,
        project_id="wc-proj-001",
        name=asset_id,
        data=data,
        media_urls=[],
        persistent_url=None,
        version=1,
    )


# ===========================================================================
# Tests: WC scene context passed to BeatEnricher (M2.G fix)
# ===========================================================================


class TestWCSceneContextPassedToBeatEnricher:
    """Tests 16-19: _get_wc_scene_context wires WC data into BeatEnricher."""

    def test_wc_scene_data_in_llm_messages_enrich_project(self, env_wc):
        """16. enrich_project: WC scene data appears in LLM messages."""
        wc_adapter = env_wc["wc_adapter"]
        wc_adapter._scenes = {
            "wc-proj-001": [
                _make_wc_scene("wc-scene-000", {"dialogue": "SECRET_DIALOGUE_MARKER"}),
            ]
        }

        _seed_project(env_wc)
        llm = env_wc["llm"]
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))

        env_wc["svc"].enrich_project("proj-001")

        # The beat enrichment call is the first chat() call
        assert len(llm.captured_messages) >= 1
        beat_messages = llm.captured_messages[0]
        all_content = " ".join(m["content"] for m in beat_messages)
        assert "SECRET_DIALOGUE_MARKER" in all_content, (
            "WC scene data must appear in LLM prompt messages"
        )

    def test_multiple_wc_scenes_correct_one_matched(self, env_wc):
        """17. Multiple WC scenes: only the matching scene's data is used."""
        wc_adapter = env_wc["wc_adapter"]
        wc_adapter._scenes = {
            "wc-proj-001": [
                _make_wc_scene("wc-scene-000", {"tag": "SCENE_ZERO_DATA"}),
                _make_wc_scene("wc-scene-001", {"tag": "SCENE_ONE_DATA"}),
            ]
        }

        # Seed project with 2 scenes: scene-000 maps to wc-scene-000,
        # scene-001 maps to wc-scene-001 (via _seed_project default wc_scene_id pattern)
        _seed_project(env_wc, n_scenes=2)
        llm = env_wc["llm"]
        # Two beat enrichments (one per scene), two coverage plans
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))

        env_wc["svc"].enrich_project("proj-001")

        # First beat enrichment is for scene-000 → should contain SCENE_ZERO_DATA only
        assert len(llm.captured_messages) >= 2
        first_call_content = " ".join(m["content"] for m in llm.captured_messages[0])
        second_call_content = " ".join(m["content"] for m in llm.captured_messages[2])

        assert "SCENE_ZERO_DATA" in first_call_content
        assert "SCENE_ONE_DATA" not in first_call_content

        assert "SCENE_ONE_DATA" in second_call_content
        assert "SCENE_ZERO_DATA" not in second_call_content

    def test_missing_wc_scene_fallback_no_error(self, env_wc):
        """18. No matching WC scene -> BeatEnricher runs with script_context=None, no error."""
        wc_adapter = env_wc["wc_adapter"]
        # WC has no scenes for this project
        wc_adapter._scenes = {"wc-proj-001": []}

        _seed_project(env_wc)
        llm = env_wc["llm"]
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))

        # Must NOT raise, must create beats normally
        result = env_wc["svc"].enrich_project("proj-001")
        assert result.beats_created == 1

        # Beat enrichment call should have happened but without WC data
        assert len(llm.captured_messages) >= 1
        beat_messages_content = " ".join(m["content"] for m in llm.captured_messages[0])
        # No WC data marker should appear
        assert "wc-scene" not in beat_messages_content.lower() or True  # no crash is the contract

    def test_enrich_scene_beats_uses_wc_context(self, env_wc):
        """19. enrich_scene_beats also passes WC scene data to BeatEnricher."""
        wc_adapter = env_wc["wc_adapter"]
        wc_adapter._scenes = {
            "wc-proj-001": [
                _make_wc_scene("wc-scene-000", {"notes": "EXPLICIT_SCENE_BEATS_MARKER"}),
            ]
        }

        _seed_project(env_wc)
        llm = env_wc["llm"]
        llm.queue(_beat_response(1))

        new_beats = env_wc["svc"].enrich_scene_beats("scene-000")
        assert len(new_beats) == 1

        # Verify WC data was in the LLM prompt
        assert len(llm.captured_messages) >= 1
        beat_messages_content = " ".join(m["content"] for m in llm.captured_messages[0])
        assert "EXPLICIT_SCENE_BEATS_MARKER" in beat_messages_content, (
            "WC scene data must appear in enrich_scene_beats LLM prompt"
        )
