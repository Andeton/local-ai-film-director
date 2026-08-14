"""Application configuration via environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FILM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_path: str = "data/production.db"
    storage_root: str = "storage"
    wc_database_path: str

    llm_provider: str = "ollama"
    llm_model: str = "gemma4:e4b"
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2

    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    lm_studio_base_url: str = "http://127.0.0.1:1234/v1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str | None = None

    comfyui_base_url: str = "http://127.0.0.1:8188"

    log_level: str = "INFO"
