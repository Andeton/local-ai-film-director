"""Application configuration via environment variables."""
import os

from pydantic import model_validator
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
    openrouter_model: str = "google/gemini-2.5-flash"

    @model_validator(mode="after")
    def _load_openrouter_key_from_env(self):
        """Support bare OPENROUTER_API_KEY in addition to FILM_OPENROUTER_API_KEY."""
        if not self.openrouter_api_key:
            self.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
        return self

    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_generation_timeout: int = 600

    # M6 queue worker
    queue_worker_concurrency: int = 1
    queue_worker_poll_interval_seconds: int = 5
    queue_worker_enabled: bool = True

    windcomic_base_url: str = "http://127.0.0.1:3000"
    windcomic_email: str = ""
    windcomic_password: str = ""

    log_level: str = "INFO"
