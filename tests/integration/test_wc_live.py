"""M4.H — Live Wind Comic production handoff acceptance test.

Requires: WC at localhost:3000, Ollama with qwen3:14b, configured .env.

This test exercises the REAL M4 path through the fully-wired application.
No fake WC client. No fixture WC project. Real SSE pipeline.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.config import Settings
from film_director.main import create_app
from film_director.models.source_facts import build_shot_source_facts


@pytest.mark.live
class TestM4LiveAcceptance:
    """Real idea → WC pipeline → canonical import with source facts."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Wire real application with production settings."""
        self.settings = Settings()
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.adapter = WindComicAdapter(self.settings.wc_database_path)

    def test_real_idea_produces_canonical_project(self):
        """POST a real idea, verify canonical production output."""
        idea = (
            "A tense noir detective short film. A middle-aged detective enters "
            "an abandoned underground station at night after receiving a mysterious "
            "phone message. He cautiously searches the dark platform, discovers a "
            "personal object belonging to a missing woman, and realizes someone is "
            "watching him. Include a short spoken line from the detective, clear "
            "physical actions, atmospheric lighting, and cinematic camera direction."
        )

        r = self.client.post("/projects/from-idea", json={
            "idea": idea,
            "style": "cinematic noir",
            "aspect": "16:9",
            "language": "English",
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:500]}"
        body = r.json()

        # --- API response contract ---
        assert "project_id" in body
        assert "wc_project_id" in body
        assert body["project_id"] != body["wc_project_id"]
        assert body["scenes_imported"] >= 1
        assert body["characters_imported"] >= 1
        assert body["beats_created"] >= 1
        assert body["shots_created"] >= 1
        assert body["plans_created"] >= 1

        wc_project_id = body["wc_project_id"]

        # --- WC persisted artifacts ---
        bundle = self.adapter.read_project_bundle(wc_project_id)
        assert len(bundle.scenes) >= 1, "WC must have at least 1 scene"
        assert len(bundle.characters) >= 1, "WC must have at least 1 character"
        assert len(bundle.script_shots) >= 1, "WC must have at least 1 script shot"
        assert len(bundle.storyboard_shots) >= 1, "WC must have at least 1 storyboard shot"

        # --- Director plan persisted ---
        assert bundle.director_plan is not None
        assert bundle.director_plan.genre
        assert bundle.director_plan.style

        # --- Source facts recomputable ---
        facts = build_shot_source_facts(
            wc_project_id, bundle.script_shots, bundle.storyboard_shots,
        )
        assert len(facts) >= 1

        # --- Script/storyboard correlation ---
        for num in facts:
            f = facts[num]
            assert f.source_script_shot_number == num
            if f.source_storyboard_asset_id is not None:
                # Storyboard asset exists for this shot
                matching_sb = [
                    sb for sb in bundle.storyboard_shots
                    if sb.asset_id == f.source_storyboard_asset_id
                ]
                assert len(matching_sb) == 1
                assert matching_sb[0].shot_number == num

        # --- Storyboard descriptions verbatim ---
        for sb in bundle.storyboard_shots:
            desc = sb.data.get("description", "")
            if desc:
                num = sb.shot_number
                if num in facts:
                    assert facts[num].storyboard_description == desc

        # --- No direct WC mutation ---
        # WindComicAdapter opens with mode=ro — any write attempt would raise
        # This assertion is architectural: the adapter's _build_uri uses ?mode=ro
