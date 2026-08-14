"""Live Ollama enrichment smoke tests (M2.H).

Require running Ollama with gemma4:e4b model.
Marked with @pytest.mark.live — excluded from normal CI runs.
"""
from __future__ import annotations

import pytest

from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.llm.ollama import OllamaProvider
from film_director.models.canonical import Beat, Scene
from film_director.models.provenance import Provenance


def _prov() -> Provenance:
    return Provenance(
        source_system="wind_comic",
        source_project_id="proj-001",
        source_asset_id="asset-001",
        source_asset_version=1,
        imported_at="2024-01-01T00:00:00+00:00",
        source_hash="a" * 64,
    )


@pytest.mark.live
class TestBeatEnricherLive:
    @pytest.fixture
    def provider(self):
        return OllamaProvider(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
            timeout=120,
            max_retries=1,
        )

    def test_beat_enricher_real_llm(self, provider):
        """BeatEnricher with real gemma4:e4b → valid beats."""
        enricher = BeatEnricher(provider)
        scene = Scene(
            id="scene-test",
            sequence_id="seq-001",
            wc_scene_id="wc-scene-test",
            name="Hospital Exterior",
            location="Outskirts of town",
            description="A dark abandoned hospital stands ominously against the night sky. "
                        "The detective approaches cautiously, flashlight in hand.",
            order_index=0,
            status="draft",
            provenance=_prov(),
        )
        beats = enricher.enrich_scene(scene)
        assert len(beats) >= 1
        for beat in beats:
            assert isinstance(beat, Beat)
            assert beat.scene_id == "scene-test"
            assert len(beat.dramatic_action) > 0
            assert len(beat.character_intention) > 0
            assert len(beat.change) > 0


@pytest.mark.live
class TestCoveragePlannerLive:
    @pytest.fixture
    def provider(self):
        return OllamaProvider(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
            timeout=120,
            max_retries=1,
        )

    def test_coverage_planner_real_llm(self, provider):
        """CoveragePlanner with real gemma4:e4b → valid coverage."""
        planner = CoveragePlanner(provider)
        scene = Scene(
            id="scene-test",
            sequence_id="seq-001",
            wc_scene_id="wc-scene-test",
            name="Hospital Exterior",
            location="Outskirts of town",
            description="A dark abandoned hospital stands ominously.",
            order_index=0,
            status="draft",
            provenance=_prov(),
        )
        beat = Beat(
            id="beat-test",
            scene_id="scene-test",
            dramatic_action="Detective approaches the hospital",
            character_intention="Investigate the disappearances",
            change="Moving from safety to danger",
            characters=["Detective"],
            order_index=0,
            status="draft",
            source="llm",
            version=1,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        coverage = planner.plan_coverage(beat, scene)
        assert len(coverage) >= 1
        for item in coverage:
            assert hasattr(item, "shot_size") and len(item.shot_size) > 0
            assert hasattr(item, "purpose") and len(item.purpose) > 0
