"""FastAPI application factory."""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.api.routes import create_router
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
from film_director.llm.ollama import create_llm_provider
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    CharacterRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
)
from film_director.services.import_service import ImportService

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

    # --- Dependency wiring ---
    db = Database(settings.database_path)
    db.init_schema()

    project_repo = ProjectRepository(db)
    seq_repo = SequenceRepository(db)
    scene_repo = SceneRepository(db)
    char_repo = CharacterRepository(db)

    adapter = WindComicAdapter(settings.wc_database_path)

    import_service = ImportService(
        adapter=adapter,
        project_repo=project_repo,
        sequence_repo=seq_repo,
        scene_repo=scene_repo,
        character_repo=char_repo,
    )

    llm_provider = create_llm_provider(settings)

    router = create_router(
        adapter=adapter,
        import_service=import_service,
        project_repo=project_repo,
        seq_repo=seq_repo,
        scene_repo=scene_repo,
        char_repo=char_repo,
        llm_provider=llm_provider,
    )
    app.include_router(router)

    return app
