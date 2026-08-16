"""API integration tests for M4.G — POST /projects/from-idea.

Uses TestClient with a mocked PreproductionService to test route behavior,
error mapping, and response contract. No live WC/Ollama/ComfyUI dependency.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from film_director.config import Settings
from film_director.errors import (
    EnrichmentError,
    HumanEditConflictError,
    NormalizationError,
    WindComicPreproductionError,
    WindComicUnavailableError,
)
from film_director.main import create_app
from film_director.services.enrichment_service import EnrichmentResult
from film_director.services.import_service import ImportResult
from film_director.services.preproduction_service import PreproductionResult
from tests.fixtures.wind_comic_fixture import create_fixture_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mock_result() -> PreproductionResult:
    return PreproductionResult(
        project_id="proj-canonical-1",
        wc_project_id="wc-proj-1",
        import_result=ImportResult(
            project_id="proj-canonical-1",
            scenes_imported=2,
            characters_imported=3,
        ),
        enrichment_result=EnrichmentResult(
            project_id="proj-canonical-1",
            beats_created=2,
            shots_created=4,
            plans_created=4,
        ),
    )


@pytest.fixture
def m4_client(tmp_path):
    """TestClient with WC fixture DB and mocked PreproductionService."""
    wc_path = tmp_path / "wc.db"
    create_fixture_db(wc_path)
    s = Settings(
        _env_file=None,
        database_path=str(tmp_path / "our.db"),
        storage_root=str(tmp_path / "storage"),
        wc_database_path=str(wc_path),
        windcomic_email="test@test.com",
        windcomic_password="pass123",
    )
    app = create_app(s)
    return TestClient(app)


# ===========================================================================
# SUCCESS
# ===========================================================================

class TestSuccess:
    def test_post_returns_200(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A detective story"})
        assert r.status_code == 200

    def test_response_contains_project_id(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        body = r.json()
        assert body["project_id"] == "proj-canonical-1"

    def test_response_contains_wc_project_id(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        body = r.json()
        assert body["wc_project_id"] == "wc-proj-1"

    def test_response_contains_counts(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        body = r.json()
        assert body["scenes_imported"] == 2
        assert body["characters_imported"] == 3
        assert body["beats_created"] == 2
        assert body["shots_created"] == 4
        assert body["plans_created"] == 4

    def test_idea_forwarded_to_service(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ) as mock:
            m4_client.post("/projects/from-idea", json={
                "idea": "My great film idea",
                "style": "anime",
                "aspect": "4:3",
                "language": "en",
            })
        mock.assert_called_once_with(
            "My great film idea", style="anime", aspect="4:3", language="en",
        )

    def test_optional_fields_default_none(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ) as mock:
            m4_client.post("/projects/from-idea", json={"idea": "A story"})
        mock.assert_called_once_with("A story", style=None, aspect=None, language=None)


# ===========================================================================
# REQUEST VALIDATION
# ===========================================================================

class TestRequestValidation:
    def test_missing_idea_422(self, m4_client):
        r = m4_client.post("/projects/from-idea", json={})
        assert r.status_code == 422

    def test_malformed_body_422(self, m4_client):
        r = m4_client.post("/projects/from-idea", content=b"not json",
                           headers={"content-type": "application/json"})
        assert r.status_code == 422

    def test_blank_idea_error(self, m4_client):
        """Blank idea is caught by service-level validation → NormalizationError → 500."""
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=NormalizationError("Idea must not be blank"),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": ""})
        assert r.status_code == 500


# ===========================================================================
# ERROR MAPPING
# ===========================================================================

class TestErrorMapping:
    def test_wc_unavailable_503(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=WindComicUnavailableError("WC down"),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        assert r.status_code == 503
        assert "WC down" in r.json()["error"]

    def test_wc_preproduction_error_502(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=WindComicPreproductionError("SSE pipeline failed"),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        assert r.status_code == 502
        assert "SSE pipeline failed" in r.json()["error"]

    def test_normalization_error_500(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=NormalizationError("Missing artifacts"),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        assert r.status_code == 500

    def test_enrichment_error_422(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=EnrichmentError("LLM output invalid"),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        assert r.status_code == 422

    def test_human_edit_conflict_409(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=HumanEditConflictError("Human edits exist"),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        assert r.status_code == 409

    def test_error_response_has_safe_detail(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=WindComicPreproductionError(
                "Pipeline failed", detail="stage=script, code=TIMEOUT"
            ),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        body = r.json()
        assert "error" in body
        assert "detail" in body
        # No credentials in response
        assert "password" not in str(body).lower()
        assert "jwt" not in str(body).lower()


# ===========================================================================
# SECURITY
# ===========================================================================

class TestSecurity:
    def test_no_credentials_in_success_response(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        body_str = str(r.json()).lower()
        assert "password" not in body_str
        assert "jwt" not in body_str
        assert "token" not in body_str
        assert "bearer" not in body_str

    def test_no_credentials_in_error_response(self, m4_client):
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            side_effect=WindComicUnavailableError("Auth failed"),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        body_str = str(r.json()).lower()
        assert "password" not in body_str
        assert "pass123" not in body_str


# ===========================================================================
# BOUNDARIES
# ===========================================================================

class TestBoundaries:
    def test_route_exists(self, m4_client):
        """POST /projects/from-idea is registered."""
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "test"})
        assert r.status_code != 404
        assert r.status_code != 405

    def test_no_background_task(self, m4_client):
        """Response is final — no job_id, no polling URL."""
        with patch(
            "film_director.services.preproduction_service.PreproductionService.create_from_idea",
            return_value=_mock_result(),
        ):
            r = m4_client.post("/projects/from-idea", json={"idea": "A story"})
        body = r.json()
        assert "job_id" not in body
        assert "status_url" not in body


# ===========================================================================
# EXISTING ROUTES PRESERVED
# ===========================================================================

class TestExistingRoutes:
    def test_health_still_works(self, m4_client):
        r = m4_client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_wc_health_still_works(self, m4_client):
        r = m4_client.get("/integrations/wind-comic/health")
        assert r.status_code == 200


# ===========================================================================
# OPENAPI
# ===========================================================================

class TestOpenAPI:
    def test_openapi_schema_valid(self, m4_client):
        r = m4_client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert "/projects/from-idea" in schema["paths"]
        # No credentials in schema
        schema_str = str(schema).lower()
        assert "pass123" not in schema_str
