"""Tests for WindComicPreproductionClient — SSE + JWT auth (fake transport)."""
import json

import httpx
import pytest

from film_director.errors import WindComicPreproductionError, WindComicUnavailableError
from film_director.adapters.wind_comic_preproduction import (
    WindComicPreproductionClient,
    WCPreproductionResult,
)


# ---------------------------------------------------------------------------
# Helpers — fake SSE stream
# ---------------------------------------------------------------------------

def _sse_lines(*events):
    """Build raw SSE text from (event_type, data_dict) tuples."""
    lines = []
    for etype, data in events:
        lines.append(f"data: {json.dumps({'type': etype, 'data': data})}\n\n")
    return "".join(lines)


def _mock_transport(handlers):
    """Create httpx MockTransport from a dict of {path_substring: handler}."""
    def handler(request):
        url = str(request.url)
        for key, fn in handlers.items():
            if key in url:
                return fn(request)
        return httpx.Response(404, text="not found")
    return httpx.MockTransport(handler)


def _client_with(handlers, **kwargs) -> WindComicPreproductionClient:
    transport = _mock_transport(handlers)
    http_client = httpx.Client(transport=transport, base_url="http://wc:3000")
    return WindComicPreproductionClient(
        base_url="http://wc:3000",
        email="test@test.com",
        password="pass123",
        http_client=http_client,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_login_success(self):
        def login_handler(request):
            body = json.loads(request.content)
            assert body["email"] == "test@test.com"
            assert body["password"] == "pass123"
            return httpx.Response(200, json={"token": "jwt-abc-123", "user": {"id": "u1"}})

        client = _client_with({"/api/auth/login": login_handler})
        client.login()
        assert client._token == "jwt-abc-123"

    def test_login_invalid_credentials(self):
        def login_handler(request):
            return httpx.Response(401, json={"message": "Invalid credentials"})

        client = _client_with({"/api/auth/login": login_handler})
        with pytest.raises(WindComicUnavailableError, match="(?i)auth|credentials|401"):
            client.login()

    def test_login_transport_failure(self):
        def login_handler(request):
            raise httpx.ConnectError("Connection refused")

        client = _client_with({"/api/auth/login": login_handler})
        with pytest.raises(WindComicUnavailableError, match="(?i)unavailable|connect"):
            client.login()

    def test_bearer_header_on_create(self):
        captured = {}

        def login_handler(request):
            return httpx.Response(200, json={"token": "my-jwt"})

        def create_handler(request):
            captured["auth"] = request.headers.get("authorization")
            sse = _sse_lines(
                ("projectId", {"projectId": "p1"}),
                ("complete", {"projectId": "p1"}),
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        client = _client_with({
            "/api/auth/login": login_handler,
            "/api/create-stream": create_handler,
        })
        client.create_project("A detective investigates a mystery")
        assert captured["auth"] == "Bearer my-jwt"

    def test_token_cached_across_calls(self):
        login_count = {"n": 0}

        def login_handler(request):
            login_count["n"] += 1
            return httpx.Response(200, json={"token": "jwt"})

        def create_handler(request):
            sse = _sse_lines(
                ("projectId", {"projectId": "p1"}),
                ("complete", {"projectId": "p1"}),
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        client = _client_with({
            "/api/auth/login": login_handler,
            "/api/create-stream": create_handler,
        })
        client.create_project("A detective investigates a mystery one")
        client.create_project("A detective investigates a mystery two")
        assert login_count["n"] == 1  # cached, not re-logged


# ---------------------------------------------------------------------------
# SSE events
# ---------------------------------------------------------------------------

class TestSSEEvents:
    def _make_client(self, sse_text):
        def login_handler(request):
            return httpx.Response(200, json={"token": "jwt"})

        def create_handler(request):
            return httpx.Response(200, text=sse_text, headers={"content-type": "text/event-stream"})

        return _client_with({
            "/api/auth/login": login_handler,
            "/api/create-stream": create_handler,
        })

    def test_complete_returns_project_id(self):
        sse = _sse_lines(
            ("projectId", {"projectId": "wc-proj-42"}),
            ("step", {"step": "director"}),
            ("step", {"step": "writer"}),
            ("complete", {"projectId": "wc-proj-42"}),
        )
        client = self._make_client(sse)
        result = client.create_project("A detective investigates a mystery")
        assert isinstance(result, WCPreproductionResult)
        assert result.wc_project_id == "wc-proj-42"

    def test_progress_events_accepted(self):
        sse = _sse_lines(
            ("projectId", {"projectId": "p1"}),
            ("step", {"step": "director"}),
            ("plan", {"genre": "mystery", "style": "cinematic"}),
            ("step", {"step": "writer"}),
            ("script", {"title": "Test", "shots": []}),
            ("characters", [{"name": "Alice"}]),
            ("scenes", [{"name": "Office"}]),
            ("storyboardPlans", [{"shotNumber": 1}]),
            ("storyboards", [{"shotNumber": 1}]),
            ("step", {"step": "finalize"}),
            ("complete", {"projectId": "p1"}),
        )
        client = self._make_client(sse)
        result = client.create_project("A detective investigates a long mystery")
        assert result.wc_project_id == "p1"

    def test_error_event_raises(self):
        sse = _sse_lines(
            ("projectId", {"projectId": "p1"}),
            ("step", {"step": "director"}),
            ("error", {"message": "LLM failed", "code": "llm_error", "stage": "director"}),
        )
        client = self._make_client(sse)
        with pytest.raises(WindComicPreproductionError, match="(?i)LLM failed"):
            client.create_project("A detective investigates a mystery")

    def test_error_event_minimal_payload(self):
        sse = _sse_lines(
            ("error", {"message": "Unknown error"}),
        )
        client = self._make_client(sse)
        with pytest.raises(WindComicPreproductionError):
            client.create_project("A detective investigates a mystery")

    def test_stream_ends_before_terminal(self):
        sse = _sse_lines(
            ("projectId", {"projectId": "p1"}),
            ("step", {"step": "director"}),
        )
        client = self._make_client(sse)
        with pytest.raises(WindComicPreproductionError, match="(?i)terminal|complete|premature"):
            client.create_project("A detective investigates a mystery")

    def test_malformed_sse_data_skipped(self):
        sse = "data: not-json\n\ndata: {\"type\":\"projectId\",\"data\":{\"projectId\":\"p1\"}}\n\ndata: {\"type\":\"complete\",\"data\":{\"projectId\":\"p1\"}}\n\n"
        client = self._make_client(sse)
        result = client.create_project("A detective investigates a mystery")
        assert result.wc_project_id == "p1"

    def test_unknown_event_ignored(self):
        sse = _sse_lines(
            ("projectId", {"projectId": "p1"}),
            ("agentTalk", {"role": "director", "text": "thinking..."}),
            ("characterDna", {"perCharacter": []}),
            ("complete", {"projectId": "p1"}),
        )
        client = self._make_client(sse)
        result = client.create_project("A detective investigates a mystery")
        assert result.wc_project_id == "p1"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class TestTransport:
    def test_wc_unreachable(self):
        def login_handler(request):
            raise httpx.ConnectError("refused")

        client = _client_with({"/api/auth/login": login_handler})
        with pytest.raises(WindComicUnavailableError):
            client.create_project("A detective investigates a mystery")

    def test_create_non_200(self):
        def login_handler(request):
            return httpx.Response(200, json={"token": "jwt"})

        def create_handler(request):
            return httpx.Response(500, text="Internal error")

        client = _client_with({
            "/api/auth/login": login_handler,
            "/api/create-stream": create_handler,
        })
        with pytest.raises(WindComicPreproductionError, match="(?i)500|failed"):
            client.create_project("A detective investigates a mystery")

    def test_create_budget_exceeded(self):
        def login_handler(request):
            return httpx.Response(200, json={"token": "jwt"})

        def create_handler(request):
            return httpx.Response(402, json={"error": "Budget exceeded", "code": "budget_exceeded"})

        client = _client_with({
            "/api/auth/login": login_handler,
            "/api/create-stream": create_handler,
        })
        with pytest.raises(WindComicPreproductionError, match="(?i)budget|402"):
            client.create_project("A detective investigates a mystery")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_no_hardcoded_credentials(self):
        """Client must receive credentials, not hardcode them."""
        client = WindComicPreproductionClient(
            base_url="http://wc:3000",
            email="user@example.com",
            password="secret",
        )
        assert client._email == "user@example.com"
        assert client._password == "secret"

    def test_no_registration(self):
        """Client must NOT have a register method."""
        client = WindComicPreproductionClient(
            base_url="http://wc:3000",
            email="u@e.com",
            password="p",
        )
        assert not hasattr(client, "register")

    def test_no_budget_mutation(self):
        """Client must NOT have budget management methods."""
        client = WindComicPreproductionClient(
            base_url="http://wc:3000",
            email="u@e.com",
            password="p",
        )
        assert not hasattr(client, "set_budget")
        assert not hasattr(client, "update_budget")


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------

class TestRequestBody:
    def test_create_sends_idea(self):
        captured = {}

        def login_handler(request):
            return httpx.Response(200, json={"token": "jwt"})

        def create_handler(request):
            captured["body"] = json.loads(request.content)
            sse = _sse_lines(
                ("projectId", {"projectId": "p1"}),
                ("complete", {"projectId": "p1"}),
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        client = _client_with({
            "/api/auth/login": login_handler,
            "/api/create-stream": create_handler,
        })
        client.create_project(
            idea="A noir detective investigates crimes",
            style="cinematic",
            aspect="16:9",
            language="en",
        )
        assert captured["body"]["idea"] == "A noir detective investigates crimes"
        assert captured["body"]["style"] == "cinematic"
        assert captured["body"]["aspect"] == "16:9"
        assert captured["body"]["language"] == "en"
