"""FastAPI application factory."""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from film_director.config import Settings
from film_director.errors import (
    FilmDirectorError,
    LLMStructuredOutputError,
    LLMUnavailableError,
    NormalizationError,
    PersistenceError,
    WindComicArtifactMalformedError,
    WindComicNotFoundError,
    WindComicSchemaError,
    WindComicUnavailableError,
)

logger = logging.getLogger(__name__)

_ERROR_STATUS: dict[type, int] = {
    WindComicNotFoundError: 404,
    WindComicArtifactMalformedError: 422,
    LLMStructuredOutputError: 422,
    WindComicSchemaError: 502,
    WindComicUnavailableError: 503,
    LLMUnavailableError: 503,
    NormalizationError: 500,
    PersistenceError: 500,
}


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = FastAPI(title="Local AI Film Director", version="0.1.0")
    app.state.settings = settings

    @app.exception_handler(FilmDirectorError)
    def handle_error(request: Request, exc: FilmDirectorError) -> JSONResponse:
        return JSONResponse(
            status_code=_ERROR_STATUS.get(type(exc), 500),
            content={"error": exc.message, "detail": exc.detail},
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    return app
