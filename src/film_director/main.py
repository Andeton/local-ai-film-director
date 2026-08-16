"""FastAPI application factory."""
import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.api.routes import create_router
from film_director.config import Settings
from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
from film_director.enrichment.stale_propagator import StalePropagator
from film_director.enrichment.strategy_selector import StrategySelector
from film_director.errors import (
    ComfyUIExecutionError,
    ComfyUIResultError,
    ComfyUIUnavailableError,
    EnrichmentError,
    FilmDirectorError,
    GenerationError,
    HumanEditConflictError,
    LLMStructuredOutputError,
    LLMUnavailableError,
    MediaProcessingError,
    NormalizationError,
    ParameterResolutionError,
    PersistenceError,
    ReferenceGenerationError,
    ReferenceIngestError,
    ReferenceLifecycleError,
    ReferenceNotFoundError,
    ReferenceResolutionError,
    UnsupportedStrategyError,
    WindComicArtifactMalformedError,
    WindComicNotFoundError,
    WindComicPreproductionError,
    WindComicSchemaError,
    WindComicUnavailableError,
    WorkflowTemplateError,
)
from film_director.llm.ollama import create_llm_provider
from film_director.persistence.database import Database
from film_director.generation.comfyui_adapter import ComfyUIAdapter
from film_director.generation.generation_service import GenerationService
from film_director.generation.workflow_registry import WorkflowResolver
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    H3PromptRepository,
    ProjectRepository,
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
    TakeRepository,
)
from film_director.adapters.wind_comic_preproduction import WindComicPreproductionClient
from film_director.services.enrichment_service import EnrichmentService
from film_director.services.import_service import ImportService
from film_director.services.preproduction_service import PreproductionService
from film_director.services.reference_lifecycle import ReferenceLifecycleService, ReferenceSelector
from film_director.services.reference_service import ReferenceIngestService

logger = logging.getLogger(__name__)

_ERROR_STATUS: dict[type, int] = {
    WindComicNotFoundError: 404,
    WindComicArtifactMalformedError: 422,
    LLMStructuredOutputError: 422,
    EnrichmentError: 422,
    HumanEditConflictError: 409,
    WindComicPreproductionError: 502,
    WindComicSchemaError: 502,
    WindComicUnavailableError: 503,
    LLMUnavailableError: 503,
    ComfyUIUnavailableError: 503,
    ComfyUIExecutionError: 502,
    ComfyUIResultError: 502,
    UnsupportedStrategyError: 422,
    ReferenceResolutionError: 422,
    ReferenceIngestError: 422,
    ReferenceGenerationError: 502,
    ReferenceNotFoundError: 404,
    ReferenceLifecycleError: 409,
    ParameterResolutionError: 422,
    WorkflowTemplateError: 500,
    GenerationError: 500,
    MediaProcessingError: 500,
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

    @app.exception_handler(sqlite3.Error)
    def handle_sqlite_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        logger.error("Database error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal database error", "detail": None},
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
    beat_repo = BeatRepository(db)
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)

    adapter = WindComicAdapter(settings.wc_database_path)

    ref_asset_repo = ReferenceAssetRepository(db)

    import_service = ImportService(
        adapter=adapter,
        project_repo=project_repo,
        sequence_repo=seq_repo,
        scene_repo=scene_repo,
        character_repo=char_repo,
        db=db,
        ref_asset_repo=ref_asset_repo,
    )

    llm_provider = create_llm_provider(settings)

    # M2 services
    stale_propagator = StalePropagator(
        db=db,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        sequence_repo=seq_repo,
        scene_repo=scene_repo,
    )

    enrichment_service = EnrichmentService(
        db=db,
        project_repo=project_repo,
        sequence_repo=seq_repo,
        scene_repo=scene_repo,
        character_repo=char_repo,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        adapter=adapter,
        import_service=import_service,
        beat_enricher=BeatEnricher(llm_provider),
        coverage_planner=CoveragePlanner(llm_provider),
        shot_spec_builder=ShotSpecBuilder(),
        strategy_selector=StrategySelector(),
        stale_propagator=stale_propagator,
    )

    # M3 services
    import os
    comfyui_adapter = ComfyUIAdapter(
        base_url=settings.comfyui_base_url,
    )
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    generation_service = GenerationService(
        db=db,
        comfyui=comfyui_adapter,
        storage_root=settings.storage_root,
        project_root=project_root,
        generation_timeout=settings.comfyui_generation_timeout,
    )
    request_repo = GenerationRequestRepository(db)

    # M5 services
    from film_director.persistence.repositories import (
        ReferenceGenerationRequestRepository as RefGenReqRepo,
        ReferenceGenerationExecutionRepository as RefGenExecRepo,
    )
    from film_director.generation.reference_generator import ReferenceGenerationService

    ref_gen_req_repo = RefGenReqRepo(db)
    ref_gen_exec_repo = RefGenExecRepo(db)
    ref_ingest_service = ReferenceIngestService(
        repo=ref_asset_repo,
        storage_root=settings.storage_root,
    )
    ref_generation_service = ReferenceGenerationService(
        asset_repo=ref_asset_repo,
        request_repo=ref_gen_req_repo,
        execution_repo=ref_gen_exec_repo,
        comfyui=comfyui_adapter,
        storage_root=settings.storage_root,
        project_root=project_root,
        db=db,
    )
    ref_lifecycle_service = ReferenceLifecycleService(ref_asset_repo)
    ref_selector = ReferenceSelector()

    # M4 services
    wc_preproduction_client = WindComicPreproductionClient(
        base_url=settings.windcomic_base_url,
        email=settings.windcomic_email,
        password=settings.windcomic_password,
    )
    preproduction_service = PreproductionService(
        wc_client=wc_preproduction_client,
        adapter=adapter,
        import_service=import_service,
        enrichment_service=enrichment_service,
    )

    router = create_router(
        adapter=adapter,
        import_service=import_service,
        project_repo=project_repo,
        seq_repo=seq_repo,
        scene_repo=scene_repo,
        char_repo=char_repo,
        llm_provider=llm_provider,
        enrichment_service=enrichment_service,
        beat_repo=beat_repo,
        shot_repo=shot_repo,
        plan_repo=plan_repo,
        generation_service=generation_service,
        comfyui_adapter=comfyui_adapter,
        request_repo=request_repo,
        preproduction_service=preproduction_service,
        ref_asset_repo=ref_asset_repo,
        ref_ingest_service=ref_ingest_service,
        ref_generation_service=ref_generation_service,
        ref_lifecycle_service=ref_lifecycle_service,
        ref_selector=ref_selector,
    )
    app.include_router(router)

    return app
