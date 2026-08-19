"""OpenRouter LLM provider — OpenAI-compatible API with API key auth."""
from __future__ import annotations

import httpx

from film_director.errors import LLMUnavailableError
from film_director.llm.provider import LLMResponse, parse_llm_json


class OpenRouterProvider:
    """LLM provider backed by OpenRouter (OpenAI-compatible API with Bearer auth)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "google/gemini-2.5-flash",
        timeout: int = 120,
        max_retries: int = 1,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries

    def chat(self, messages: list[dict], expect_json: bool = False) -> LLMResponse:
        """Send a chat request to OpenRouter.

        Raises LLMUnavailableError if all attempts fail.
        """
        payload: dict = {
            "model": self._model,
            "messages": messages,
        }
        if expect_json:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        total_attempts = 1 + self._max_retries
        last_exc: Exception | None = None

        for attempt in range(total_attempts):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(
                        f"{self._base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                content: str = data["choices"][0]["message"]["content"]
                model_name: str = data.get("model", self._model)
                parsed = parse_llm_json(content) if expect_json else None

                return LLMResponse(content=content, parsed=parsed, model=model_name)

            except (httpx.HTTPError, httpx.TransportError, KeyError, Exception) as exc:
                last_exc = exc
                continue

        raise LLMUnavailableError(
            f"OpenRouter failed after {total_attempts} attempt(s)",
            detail=str(last_exc),
        )

    def health(self) -> bool:
        """Check if OpenRouter is reachable. Never raises."""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                return resp.status_code == 200
        except Exception:
            return False
