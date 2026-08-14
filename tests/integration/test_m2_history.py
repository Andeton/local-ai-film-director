"""History preservation tests for M2 enrichment (M2.G).

Verifies that re-enrichment marks old artifacts OUTDATED, never deletes,
and new artifacts get new IDs.
"""
from __future__ import annotations

import pytest

from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
from film_director.enrichment.stale_propagator import StalePropagator
from film_director.enrichment.strategy_selector import StrategySelector
from film_director.llm.provider import LLMResponse
from film_director.models.canonical import (
    Beat,
    CameraIntent,
    ProductionProject,
    Scene,
    Sequence,
    ShotSpecificationV1,
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
from film_director.services.enrichment_service import EnrichmentService
from film_director.services.import_service import ImportService


# ---------------------------------------------------------------------------
# FakeLLMProvider
# ---------------------------------------------------------------------------

class FakeLLMProvider:
    def __init__(self):
        self._responses: list = []
        self.call_count = 0

    def queue(self, response_or_exception):
        self._responses.append(response_or_exception)

    def chat(self, messages, expect_json=False):
        self.call_count += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def health(self):
        return True


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
    beats = [
        {
            "dramatic_action": f"Action {i}",
            "character_intention": f"Intent {i}",
            "change": f"Change {i}",
            "characters": [],
        }
        for i in range(n)
    ]
    return LLMResponse(content="", parsed={"beats": beats}, model="test")


def _coverage_response(n: int = 1) -> LLMResponse:
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


@pytest.fixture
def env(tmp_path):
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
        db=db, beat_repo=beat_repo, shot_repo=shot_repo,
        plan_repo=plan_repo, sequence_repo=sequence_repo, scene_repo=scene_repo,
    )

    import_service = ImportService(
        adapter=None, project_repo=project_repo, sequence_repo=sequence_repo,
        scene_repo=scene_repo, character_repo=character_repo, db=db,
    )

    svc = EnrichmentService(
        db=db, project_repo=project_repo, sequence_repo=sequence_repo,
        scene_repo=scene_repo, character_repo=character_repo,
        beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
        adapter=None, import_service=import_service,
        beat_enricher=BeatEnricher(llm), coverage_planner=CoveragePlanner(llm),
        shot_spec_builder=ShotSpecBuilder(), strategy_selector=StrategySelector(),
        stale_propagator=stale_propagator,
    )

    return dict(
        db=db, svc=svc, llm=llm,
        project_repo=project_repo, sequence_repo=sequence_repo,
        scene_repo=scene_repo, character_repo=character_repo,
        beat_repo=beat_repo, shot_repo=shot_repo, plan_repo=plan_repo,
    )


def _seed_project(env) -> str:
    project = ProductionProject(
        id="proj-001", wc_project_id="wc-proj-001", title="Test Film",
        status="active", aspect="16:9",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        provenance=_prov(),
    )
    env["project_repo"].save_project(project)

    seq = Sequence(id="seq-001", project_id="proj-001", name="Main", order_index=0)
    env["sequence_repo"].save_sequence(seq)

    scene = Scene(
        id="scene-000", sequence_id="seq-001", wc_scene_id="wc-scene-000",
        name="Scene 0", location="Location", description="Description",
        order_index=0, status="draft", provenance=_prov(),
    )
    env["scene_repo"].save_scene(scene)

    return "proj-001"


# ===========================================================================
# Tests
# ===========================================================================


class TestReEnrichHistory:

    def test_re_enrich_beats_old_become_outdated(self, env):
        """1. Re-enrich beats: old beats+shots+plans outdated, new beats current."""
        _seed_project(env)
        llm = env["llm"]

        # First enrichment
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        env["svc"].enrich_project("proj-001")

        old_beats = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(old_beats) == 1
        old_beat_id = old_beats[0].id

        old_shots = env["shot_repo"].get_current_shots_by_beat(old_beat_id)
        assert len(old_shots) == 1
        old_shot_id = old_shots[0].id

        old_plan = env["plan_repo"].get_current_plan_by_shot(old_shot_id)
        assert old_plan is not None
        old_plan_id = old_plan.id

        # Re-enrich
        llm.queue(_beat_response(1))
        env["svc"].enrich_scene_beats("scene-000")

        # Old beat should be outdated
        old_beat = env["beat_repo"].get_beat(old_beat_id)
        assert old_beat.status == "outdated"

        # Old shot should be outdated
        old_shot = env["shot_repo"].get_shot(old_shot_id)
        assert old_shot.status == "outdated"

        # Old plan should be outdated
        old_plan_reloaded = env["plan_repo"].get_plans_by_shot(old_shot_id)
        outdated_plans = [p for p in old_plan_reloaded if p.status == "outdated"]
        assert len(outdated_plans) >= 1

        # New beats should be current
        new_beats = env["beat_repo"].get_current_beats_by_scene("scene-000")
        assert len(new_beats) == 1
        assert new_beats[0].id != old_beat_id

    def test_force_re_enrich_human_beats_preserved_outdated(self, env):
        """2. Force re-enrich human beats: human beats preserved as outdated."""
        _seed_project(env)

        human_beat = Beat(
            id="beat-human-001", scene_id="scene-000",
            dramatic_action="Human action", character_intention="", change="",
            characters=[], order_index=0, status="draft", source="human",
            version=1, created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(human_beat)

        llm = env["llm"]
        llm.queue(_beat_response(1))

        env["svc"].enrich_scene_beats("scene-000", force=True)

        # Human beat preserved but outdated
        preserved = env["beat_repo"].get_beat("beat-human-001")
        assert preserved is not None
        assert preserved.status == "outdated"
        assert preserved.source == "human"

    def test_re_plan_coverage_old_shots_plans_outdated(self, env):
        """3. Re-plan coverage: old shots+plans outdated, new shots current."""
        _seed_project(env)

        beat = Beat(
            id="beat-001", scene_id="scene-000",
            dramatic_action="Test action", character_intention="", change="",
            characters=[], order_index=0, status="draft", source="llm",
            version=1, created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        llm = env["llm"]
        # First plan
        llm.queue(_coverage_response(1))
        first_shots = env["svc"].plan_beat_coverage("beat-001")
        assert len(first_shots) == 1
        old_shot_id = first_shots[0].id

        # Re-plan
        llm.queue(_coverage_response(1))
        new_shots = env["svc"].plan_beat_coverage("beat-001")
        assert len(new_shots) == 1
        new_shot_id = new_shots[0].id

        # Old shot outdated
        old = env["shot_repo"].get_shot(old_shot_id)
        assert old.status == "outdated"

        # New shot current
        current = env["shot_repo"].get_current_shots_by_beat("beat-001")
        assert len(current) == 1
        assert current[0].id == new_shot_id

    def test_force_re_plan_human_shots_preserved_outdated(self, env):
        """4. Force re-plan human shots: human shots preserved outdated."""
        _seed_project(env)

        beat = Beat(
            id="beat-001", scene_id="scene-000",
            dramatic_action="Test action", character_intention="", change="",
            characters=[], order_index=0, status="draft", source="llm",
            version=1, created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["beat_repo"].save_beat(beat)

        human_shot = ShotSpecificationV1(
            id="shot-human-001", beat_id="beat-001",
            dramatic_purpose="Human shot", action="Test",
            camera=CameraIntent(shot_size="wide"),
            order_index=0, status="draft", source="human", version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        env["shot_repo"].save_shot(human_shot)

        llm = env["llm"]
        llm.queue(_coverage_response(1))

        env["svc"].plan_beat_coverage("beat-001", force=True)

        preserved = env["shot_repo"].get_shot("shot-human-001")
        assert preserved is not None
        assert preserved.status == "outdated"
        assert preserved.source == "human"

    def test_old_records_loadable_after_re_enrichment(self, env):
        """5. All old records loadable by ID after re-enrichment."""
        _seed_project(env)
        llm = env["llm"]

        # First enrichment
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        env["svc"].enrich_project("proj-001")

        old_beats = env["beat_repo"].get_current_beats_by_scene("scene-000")
        old_beat_id = old_beats[0].id
        old_shots = env["shot_repo"].get_current_shots_by_beat(old_beat_id)
        old_shot_id = old_shots[0].id
        old_plan = env["plan_repo"].get_current_plan_by_shot(old_shot_id)
        old_plan_id = old_plan.id

        # Re-enrich
        llm.queue(_beat_response(1))
        env["svc"].enrich_scene_beats("scene-000")

        # Old records still loadable by ID
        assert env["beat_repo"].get_beat(old_beat_id) is not None
        assert env["shot_repo"].get_shot(old_shot_id) is not None
        # Plan is loadable via get_plans_by_shot
        all_plans = env["plan_repo"].get_plans_by_shot(old_shot_id)
        plan_ids = [p.id for p in all_plans]
        assert old_plan_id in plan_ids

    def test_new_artifacts_have_new_ids(self, env):
        """6. New artifacts have new IDs (never reuse old IDs)."""
        _seed_project(env)
        llm = env["llm"]

        # First enrichment
        llm.queue(_beat_response(1))
        llm.queue(_coverage_response(1))
        env["svc"].enrich_project("proj-001")

        old_beats = env["beat_repo"].get_current_beats_by_scene("scene-000")
        old_ids = {old_beats[0].id}
        old_shots = env["shot_repo"].get_current_shots_by_beat(old_beats[0].id)
        old_ids.add(old_shots[0].id)

        # Re-enrich
        llm.queue(_beat_response(1))
        new_beats = env["svc"].enrich_scene_beats("scene-000")

        new_ids = {new_beats[0].id}
        assert old_ids.isdisjoint(new_ids), "New beats must have different IDs from old"
