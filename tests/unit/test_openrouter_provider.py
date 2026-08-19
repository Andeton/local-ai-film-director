"""Tests for OpenRouter provider, config loading, and shot planner."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from film_director.config import Settings
from film_director.errors import LLMUnavailableError
from film_director.llm.provider import LLMResponse


# -----------------------------------------------------------------------
# Config: OPENROUTER_API_KEY loading
# -----------------------------------------------------------------------

class TestOpenRouterKeyLoading:
    """OPENROUTER_API_KEY (without FILM_ prefix) must be loaded."""

    def test_bare_openrouter_key_loaded(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FILM_WC_DATABASE_PATH", str(tmp_path / "wc.db"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-bare")
        monkeypatch.delenv("FILM_OPENROUTER_API_KEY", raising=False)
        s = Settings(_env_file=str(tmp_path / "nonexistent.env"))
        assert s.openrouter_api_key == "sk-test-bare"

    def test_film_prefixed_key_takes_priority(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FILM_WC_DATABASE_PATH", str(tmp_path / "wc.db"))
        monkeypatch.setenv("FILM_OPENROUTER_API_KEY", "sk-film-prefix")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-bare")
        s = Settings(_env_file=str(tmp_path / "nonexistent.env"))
        # FILM_ prefix wins because pydantic loads it first
        assert s.openrouter_api_key == "sk-film-prefix"

    def test_no_key_is_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FILM_WC_DATABASE_PATH", str(tmp_path / "wc.db"))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("FILM_OPENROUTER_API_KEY", raising=False)
        s = Settings(_env_file=str(tmp_path / "nonexistent.env"))
        assert s.openrouter_api_key is None

    def test_openrouter_model_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FILM_WC_DATABASE_PATH", str(tmp_path / "wc.db"))
        s = Settings(_env_file=str(tmp_path / "nonexistent.env"))
        assert s.openrouter_model == "google/gemini-2.5-flash"

    def test_openrouter_model_configurable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FILM_WC_DATABASE_PATH", str(tmp_path / "wc.db"))
        monkeypatch.setenv("FILM_OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
        s = Settings(_env_file=str(tmp_path / "nonexistent.env"))
        assert s.openrouter_model == "anthropic/claude-sonnet-4"


# -----------------------------------------------------------------------
# OpenRouter provider factory
# -----------------------------------------------------------------------

class TestOpenRouterProviderFactory:
    def _make_settings(self, provider, monkeypatch, tmp_path):
        monkeypatch.setenv("FILM_WC_DATABASE_PATH", str(tmp_path / "wc.db"))
        monkeypatch.setenv("FILM_LLM_PROVIDER", provider)
        return Settings(_env_file=str(tmp_path / "nonexistent.env"))

    def test_openrouter_without_key_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("FILM_OPENROUTER_API_KEY", raising=False)
        settings = self._make_settings("openrouter", monkeypatch, tmp_path)
        from film_director.errors import ConfigurationError
        from film_director.llm.ollama import create_llm_provider
        with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
            create_llm_provider(settings)

    def test_openrouter_with_key_creates_provider(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        settings = self._make_settings("openrouter", monkeypatch, tmp_path)
        from film_director.llm.ollama import create_llm_provider
        provider = create_llm_provider(settings)
        from film_director.llm.openrouter import OpenRouterProvider
        assert isinstance(provider, OpenRouterProvider)


# -----------------------------------------------------------------------
# OpenRouter provider request/response
# -----------------------------------------------------------------------

class TestOpenRouterProvider:
    def test_chat_success(self):
        from film_director.llm.openrouter import OpenRouterProvider

        mock_response = {
            "choices": [{"message": {"content": '{"result": "ok"}'}}],
            "model": "google/gemini-2.5-flash",
        }

        provider = OpenRouterProvider(api_key="sk-test", model="test-model")

        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = mock_response

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            MockClient.return_value = mock_client

            result = provider.chat([{"role": "user", "content": "test"}], expect_json=True)

            assert result.parsed == {"result": "ok"}
            assert result.model == "google/gemini-2.5-flash"

            # Verify auth header was sent
            call_kwargs = mock_client.post.call_args
            assert "Bearer sk-test" in str(call_kwargs)

    def test_chat_failure_raises(self):
        from film_director.llm.openrouter import OpenRouterProvider

        provider = OpenRouterProvider(api_key="sk-test", max_retries=0)

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = Exception("connection refused")
            MockClient.return_value = mock_client

            with pytest.raises(LLMUnavailableError, match="OpenRouter failed"):
                provider.chat([{"role": "user", "content": "test"}])

    def test_chat_no_silent_fallback(self):
        """Failed OpenRouter call must raise, not silently return garbage."""
        from film_director.llm.openrouter import OpenRouterProvider
        import httpx

        provider = OpenRouterProvider(api_key="sk-test", max_retries=0)

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=MagicMock(),
                response=MagicMock(status_code=429),
            )
            MockClient.return_value = mock_client

            with pytest.raises(LLMUnavailableError):
                provider.chat([{"role": "user", "content": "test"}])


# -----------------------------------------------------------------------
# Shot planner validation
# -----------------------------------------------------------------------

class TestShotPlannerValidation:
    def test_valid_shot_plan(self):
        from film_director.enrichment.shot_planner import _validate_shot_plan
        parsed = {"shots": [
            {"action": "Establishing wide shot", "dramatic_purpose": "Set the scene",
             "shot_size": "wide", "angle": "high", "movement": "slow_pan",
             "characters": ["Alice"], "duration_sec": 5.0},
        ]}
        candidates, error = _validate_shot_plan(parsed)
        assert error is None
        assert len(candidates) == 1
        assert candidates[0].action == "Establishing wide shot"

    def test_missing_shots_key(self):
        from film_director.enrichment.shot_planner import _validate_shot_plan
        _, error = _validate_shot_plan({"data": []})
        assert error == "Response missing 'shots' key"

    def test_empty_shots_list(self):
        from film_director.enrichment.shot_planner import _validate_shot_plan
        _, error = _validate_shot_plan({"shots": []})
        assert "'shots' list must be non-empty" in error

    def test_invalid_shot_size(self):
        from film_director.enrichment.shot_planner import _validate_shot_plan
        _, error = _validate_shot_plan({"shots": [
            {"action": "test", "dramatic_purpose": "test",
             "shot_size": "INVALID", "duration_sec": 5.0},
        ]})
        assert "failed validation" in error

    def test_empty_action_rejected(self):
        from film_director.enrichment.shot_planner import _validate_shot_plan
        _, error = _validate_shot_plan({"shots": [
            {"action": "", "dramatic_purpose": "test",
             "shot_size": "wide", "duration_sec": 5.0},
        ]})
        assert "failed validation" in error

    def test_duration_capped_at_15(self):
        from film_director.enrichment.shot_planner import ShotCandidate
        c = ShotCandidate(
            action="test", dramatic_purpose="test",
            shot_size="wide", duration_sec=30.0,
        )
        assert c.duration_sec == 15.0


# -----------------------------------------------------------------------
# Shot planner integration with fake LLM
# -----------------------------------------------------------------------

class TestShotPlannerWithFakeLLM:
    def _make_llm(self, shots_json):
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(
            content=json.dumps(shots_json),
            parsed=shots_json,
            model="test",
        )
        return llm

    def test_plan_scene_returns_beats_and_shots(self):
        from film_director.enrichment.shot_planner import ShotPlanner
        from film_director.models.canonical import Scene, CharacterReference
        from film_director.models.provenance import Provenance

        prov = Provenance(
            source_system="test", source_project_id="p1",
            source_asset_id="a1", source_asset_version=1,
            imported_at="2026-01-01", source_hash="h",
        )

        scene = Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="Test Scene", location="Office", description="A meeting",
            order_index=0, provenance=prov,
        )

        chars = [CharacterReference(
            id="char-1", project_id="proj-1", wc_character_id="wc-c1",
            name="Alice", description="Detective", appearance="dark hair",
            provenance=prov,
        )]

        plan_json = {"shots": [
            {"action": "Wide establishing", "dramatic_purpose": "Set scene",
             "shot_size": "wide", "characters": ["Alice"], "duration_sec": 5.0},
            {"action": "Close reaction", "dramatic_purpose": "Show emotion",
             "shot_size": "close_up", "characters": ["Alice"], "duration_sec": 4.0},
        ]}

        planner = ShotPlanner(self._make_llm(plan_json))
        beats, shots = planner.plan_scene(scene, chars, [])

        assert len(beats) == 2
        assert len(shots) == 2
        assert beats[0].scene_id == "scene-1"
        assert shots[0].action == "Wide establishing"
        assert shots[1].action == "Close reaction"
        assert shots[0].subjects[0].character_id == "char-1"

    def test_plan_scene_invalid_raises(self):
        from film_director.enrichment.shot_planner import ShotPlanner
        from film_director.models.canonical import Scene
        from film_director.models.provenance import Provenance
        from film_director.errors import EnrichmentError

        prov = Provenance(
            source_system="test", source_project_id="p1",
            source_asset_id="a1", source_asset_version=1,
            imported_at="2026-01-01", source_hash="h",
        )

        scene = Scene(
            id="scene-1", sequence_id="seq-1", wc_scene_id="wc-s1",
            name="Test", location="", description="",
            order_index=0, provenance=prov,
        )

        # Both initial and repair return bad data
        bad = {"bad": True}
        llm = MagicMock()
        llm.chat.return_value = LLMResponse(content="{}", parsed=bad, model="test")

        planner = ShotPlanner(llm)
        with pytest.raises(EnrichmentError, match="Shot plan validation failed"):
            planner.plan_scene(scene, [], [])


# -----------------------------------------------------------------------
# Enrichment: no auto-enrichment, no partial persistence
# -----------------------------------------------------------------------

class TestEnrichmentSafety:
    def test_no_auto_enrichment_on_import(self):
        """PreproductionService must NOT call enrichment automatically."""
        from film_director.services.preproduction_service import PreproductionService

        mock_wc = MagicMock()
        mock_wc.create_project.return_value = MagicMock(wc_project_id="wc-1")
        mock_adapter = MagicMock()
        mock_adapter.read_project_bundle.return_value = MagicMock(
            scenes=[MagicMock()], characters=[MagicMock()],
            script_shots=[MagicMock()], storyboard_shots=[MagicMock()],
        )
        mock_import = MagicMock()
        mock_import.import_project.return_value = MagicMock(
            project_id="proj-1", scenes_imported=1, characters_imported=1,
        )
        mock_enrich = MagicMock()

        svc = PreproductionService(
            wc_client=mock_wc, adapter=mock_adapter,
            import_service=mock_import, enrichment_service=mock_enrich,
        )
        result = svc.create_from_idea("test idea")
        # Enrichment is NOT called automatically
        mock_enrich.enrich_project.assert_not_called()
        assert result.enrichment_result is None
