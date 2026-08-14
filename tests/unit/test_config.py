from film_director.config import Settings


def test_default_settings():
    s = Settings(_env_file=None, wc_database_path="test.db")
    assert s.database_path == "data/production.db"
    assert s.llm_provider == "ollama"
    assert s.llm_model == "gemma4:e4b"
    assert s.llm_max_retries == 2
    assert s.openrouter_api_key is None


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("FILM_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("FILM_OPENROUTER_API_KEY", "sk-test")
    s = Settings(_env_file=None, wc_database_path="test.db")
    assert s.llm_provider == "openrouter"
    assert s.openrouter_api_key == "sk-test"
