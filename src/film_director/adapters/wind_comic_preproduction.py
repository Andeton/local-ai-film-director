"""Wind Comic preproduction client — SSE orchestration + JWT auth.

Triggers WC pre-production via POST /api/create-stream, observes the
SSE event stream for completion/error, and returns the WC project ID.
Does NOT normalize WC data into canonical models — that belongs to
ImportService. SSE payloads are orchestration signals only; WC SQLite
remains the authoritative import source.

LFDirector never writes to WC's SQLite database.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from film_director.errors import WindComicPreproductionError, WindComicUnavailableError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_SSE_TIMEOUT = 600.0


@dataclass(frozen=True)
class WCPreproductionResult:
    """Result of a successful WC pre-production pipeline run."""

    wc_project_id: str


class WindComicPreproductionClient:
    """HTTP SSE client for Wind Comic's /api/create-stream pipeline.

    Authenticate once via JWT, then trigger project creation and observe
    the SSE event stream for completion. Credentials are injected via
    constructor — never hardcoded or auto-registered.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:3000",
        email: str = "",
        password: str = "",
        http_client: httpx.Client | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        sse_timeout: float = _DEFAULT_SSE_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._client = http_client
        self._timeout = timeout
        self._sse_timeout = sse_timeout
        self._token: str | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(base_url=self._base_url, timeout=self._timeout)

    def _owns_client(self) -> bool:
        return self._client is None

    # -----------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------

    def login(self) -> None:
        """Authenticate to WC and cache JWT token."""
        client = self._get_client()
        try:
            resp = client.post(
                "/api/auth/login",
                json={"email": self._email, "password": self._password},
            )
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise WindComicUnavailableError(
                f"Wind Comic unavailable: {e}",
                detail=f"base_url={self._base_url}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raise WindComicUnavailableError(
                f"WC auth failed: HTTP {resp.status_code}",
                detail=resp.text[:500] if resp.text else None,
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise WindComicUnavailableError(
                f"WC auth response not valid JSON: {e}",
            ) from e

        token = data.get("token")
        if not token:
            raise WindComicUnavailableError(
                "WC auth response missing 'token'",
            )
        self._token = token
        logger.debug("WC auth successful")

    # -----------------------------------------------------------------
    # Create project
    # -----------------------------------------------------------------

    def create_project(
        self,
        idea: str,
        style: str | None = None,
        aspect: str | None = None,
        language: str | None = None,
    ) -> WCPreproductionResult:
        """Trigger WC pre-production and wait for completion via SSE.

        Returns the WC project ID on success.
        Raises WindComicPreproductionError on pipeline failure/timeout.
        Raises WindComicUnavailableError on transport/auth failure.
        """
        if self._token is None:
            self.login()

        body: dict = {"idea": idea}
        if style is not None:
            body["style"] = style
        if aspect is not None:
            body["aspect"] = aspect
        if language is not None:
            body["language"] = language

        client = self._get_client()
        try:
            resp = client.post(
                "/api/create-stream",
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._sse_timeout,
            )
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise WindComicUnavailableError(
                f"WC create-stream transport failure: {e}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raise WindComicPreproductionError(
                f"WC create-stream failed: HTTP {resp.status_code}",
                detail=resp.text[:500] if resp.text else None,
            )

        return self._parse_sse(resp.text)

    # -----------------------------------------------------------------
    # SSE parsing
    # -----------------------------------------------------------------

    def _parse_sse(self, raw: str) -> WCPreproductionResult:
        """Parse SSE text and extract terminal result."""
        wc_project_id: str | None = None

        for line in raw.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue

            payload_str = line[len("data:"):].strip()
            try:
                envelope = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed SSE data: %s", payload_str[:100])
                continue

            if not isinstance(envelope, dict):
                continue

            event_type = envelope.get("type", "")
            event_data = envelope.get("data", {})

            if event_type == "projectId":
                if isinstance(event_data, dict):
                    wc_project_id = event_data.get("projectId")

            elif event_type == "complete":
                if isinstance(event_data, dict) and not wc_project_id:
                    wc_project_id = event_data.get("projectId")
                if not wc_project_id:
                    raise WindComicPreproductionError(
                        "WC complete event but no project ID captured",
                    )
                return WCPreproductionResult(wc_project_id=wc_project_id)

            elif event_type == "error":
                msg = "Unknown WC pipeline error"
                detail = None
                if isinstance(event_data, dict):
                    msg = event_data.get("message", msg)
                    stage = event_data.get("stage", "")
                    code = event_data.get("code", "")
                    detail = f"stage={stage}, code={code}" if stage or code else None
                raise WindComicPreproductionError(msg, detail=detail)

            # All other events (step, plan, script, characters, etc.)
            # are progress signals — accepted and ignored for M4.A.

        # Stream ended without terminal event
        raise WindComicPreproductionError(
            "WC SSE stream ended without terminal complete/error event",
            detail=f"captured_project_id={wc_project_id}",
        )
