"""Unit tests for LLM provider abstraction."""
import pytest

from film_director.errors import ConfigurationError, LLMStructuredOutputError
from film_director.config import Settings
from film_director.llm.provider import LLMResponse, parse_llm_json
from film_director.llm.ollama import OllamaProvider, create_llm_provider


class TestLLMResponse:
    def test_creation_without_parsed(self):
        r = LLMResponse(content="hello", parsed=None, model="gemma4:e4b")
        assert r.content == "hello"
        assert r.parsed is None
        assert r.model == "gemma4:e4b"

    def test_creation_with_parsed(self):
        r = LLMResponse(content='{"x": 1}', parsed={"x": 1}, model="gemma4:e4b")
        assert r.parsed == {"x": 1}

    def test_frozen(self):
        r = LLMResponse(content="hi", parsed=None, model="m")
        with pytest.raises(Exception):
            r.content = "changed"  # type: ignore


class TestParseLlmJson:
    def test_valid_json(self):
        result = parse_llm_json('{"name": "test", "count": 42}')
        assert result == {"name": "test", "count": 42}

    def test_markdown_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = parse_llm_json(raw)
        assert result == {"key": "value"}

    def test_leading_prose(self):
        raw = 'Here is the JSON you asked for:\n{"answer": true}'
        result = parse_llm_json(raw)
        assert result == {"answer": True}

    def test_trailing_prose(self):
        raw = '{"score": 99}\nHope that helps!'
        result = parse_llm_json(raw)
        assert result == {"score": 99}

    def test_leading_and_trailing_prose(self):
        raw = 'Sure thing! Here you go: {"x": 1} — let me know if you need more.'
        result = parse_llm_json(raw)
        assert result == {"x": 1}

    def test_invalid_raises_error(self):
        with pytest.raises(LLMStructuredOutputError):
            parse_llm_json("this is not json at all")

    def test_empty_raises_error(self):
        with pytest.raises(LLMStructuredOutputError):
            parse_llm_json("")

    def test_no_shape_validation(self):
        """parse_llm_json returns whatever dict the LLM produced — no schema enforcement."""
        raw = '{"unexpected_field": "unexpected_value", "nested": {"deep": 99}}'
        result = parse_llm_json(raw)
        assert result["unexpected_field"] == "unexpected_value"
        assert result["nested"]["deep"] == 99

    def test_whitespace_only_raises_error(self):
        with pytest.raises(LLMStructuredOutputError):
            parse_llm_json("   \n  ")


class TestCreateLlmProvider:
    def _make_settings(self, provider: str) -> Settings:
        return Settings(
            _env_file=None,
            llm_provider=provider,
            wc_database_path="/tmp/fake.db",
        )

    def test_ollama_returns_ollama_provider(self):
        settings = self._make_settings("ollama")
        provider = create_llm_provider(settings)
        assert isinstance(provider, OllamaProvider)

    def test_lm_studio_raises_configuration_error(self):
        settings = self._make_settings("lm_studio")
        with pytest.raises(ConfigurationError) as exc_info:
            create_llm_provider(settings)
        assert "lm_studio" in str(exc_info.value)
        assert "M1" in str(exc_info.value)

    def test_openrouter_raises_configuration_error(self):
        settings = self._make_settings("openrouter")
        with pytest.raises(ConfigurationError) as exc_info:
            create_llm_provider(settings)
        assert "openrouter" in str(exc_info.value)
        assert "M1" in str(exc_info.value)

    def test_unknown_provider_raises_configuration_error(self):
        settings = self._make_settings("some_unknown_provider")
        with pytest.raises(ConfigurationError):
            create_llm_provider(settings)
