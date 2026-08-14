"""Live integration tests for Ollama provider — require running Ollama instance."""
import pytest

from film_director.llm.ollama import OllamaProvider


@pytest.mark.live
class TestOllamaLive:
    @pytest.fixture
    def provider(self):
        return OllamaProvider(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
            timeout=120,
            max_retries=1,
        )

    def test_health(self, provider):
        assert provider.health() is True

    def test_chat_plain(self, provider):
        response = provider.chat(
            messages=[{"role": "user", "content": "Reply with exactly one word: hello"}]
        )
        assert len(response.content) > 0

    def test_chat_structured_json(self, provider):
        """M1 EXIT CRITERION 5: LLMProvider.chat() returns structured JSON from Ollama."""
        response = provider.chat(
            messages=[
                {
                    "role": "user",
                    "content": (
                        'Return a JSON object with exactly these fields: '
                        '"name" (string, set to "test") and "count" (integer, set to 42). '
                        'Return ONLY the JSON object, no other text.'
                    ),
                }
            ],
            expect_json=True,
        )
        assert response.parsed is not None
        assert response.parsed["name"] == "test"
        assert response.parsed["count"] == 42
