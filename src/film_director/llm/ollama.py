"""Ollama LLM provider implementation and factory."""
from __future__ import annotations

import httpx

from film_director.config import Settings
from film_director.errors import ConfigurationError, LLMUnavailableError
from film_director.llm.provider import LLMResponse, parse_llm_json


class OllamaProvider:
    """LLM provider backed by an Ollama instance (OpenAI-compatible API)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int = 120,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries  # additional attempts after first

    def chat(self, messages: list[dict], expect_json: bool = False) -> LLMResponse:
        """Send a chat request. Retries up to max_retries additional times on failure.

        Args:
            messages: OpenAI-compatible message list.
            expect_json: If True, request JSON-object mode and parse the response.

        Returns:
            LLMResponse with content, parsed (if expect_json), and model name.

        Raises:
            LLMUnavailableError: if all attempts fail.
        """
        payload: dict = {
            "model": self._model,
            "messages": messages,
        }
        if expect_json:
            payload["response_format"] = {"type": "json_object"}

        total_attempts = 1 + self._max_retries
        last_exc: Exception | None = None

        for attempt in range(total_attempts):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(
                        f"{self._base_url}/chat/completions",
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                content: str = data["choices"][0]["message"]["content"]
                model_name: str = data.get("model", self._model)
                parsed = parse_llm_json(content) if expect_json else None

                return LLMResponse(content=content, parsed=parsed, model=model_name)

            except (httpx.HTTPError, httpx.TransportError, KeyError) as exc:
                last_exc = exc
                continue

        raise LLMUnavailableError(
            f"Ollama at {self._base_url} failed after {total_attempts} attempt(s)",
            detail=str(last_exc),
        )

    def health(self) -> bool:
        """Return True if the Ollama /models endpoint responds with 200, False otherwise.

        Never raises.
        """
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self._base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False


def create_llm_provider(settings: Settings) -> OllamaProvider:
    """Factory that creates the appropriate LLM provider from Settings.

    In M1, only 'ollama' is supported. Other providers raise ConfigurationError.
    """
    provider = settings.llm_provider.lower()

    if provider == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    elif provider == "lm_studio":
        raise ConfigurationError(
            "LLM provider 'lm_studio' is not implemented in M1. Use 'ollama'.",
            detail="lm_studio support is planned for a future milestone.",
        )
    elif provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ConfigurationError(
                "LLM provider 'openrouter' requires OPENROUTER_API_KEY or FILM_OPENROUTER_API_KEY.",
                detail="Set OPENROUTER_API_KEY in your .env file.",
            )
        from film_director.llm.openrouter import OpenRouterProvider
        return OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            model=settings.openrouter_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    else:
        raise ConfigurationError(
            f"Unknown LLM provider: '{provider}'. Supported in M1: 'ollama'.",
            detail=f"Got llm_provider={provider!r}",
        )
