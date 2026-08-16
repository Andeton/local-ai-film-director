"""PreproductionService — idea → Wind Comic → import → enrich (M4.E).

Synchronous orchestration: triggers WC pre-production via SSE, waits for
completion, validates persisted WC artifacts, imports into canonical model,
and enriches with source-fact precedence. One call, no persistent job entity.

SSE events are orchestration signals only — WC SQLite (via read-only
WindComicAdapter) is the authoritative import source. LFDirector never
writes to WC's SQLite database.

Timeout is owned by WindComicPreproductionClient (M4.A layer).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from film_director.errors import NormalizationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreproductionResult:
    """Result of a successful idea → production pipeline."""

    project_id: str           # canonical ProductionProject.id
    wc_project_id: str        # WC source project ID
    import_result: object     # ImportResult
    enrichment_result: object  # EnrichmentResult


class PreproductionService:
    """Orchestrates idea → WC pre-production → canonical import → enrichment.

    Dependencies are injected via constructor. No global state. Each
    create_from_idea call triggers a new WC project — no deduplication.

    Call order (invariant):
      1. Validate input
      2. WC create (auth + SSE → completion)
      3. Read persisted WC bundle (adapter, NOT SSE payloads)
      4. Validate required artifacts
      5. Import via ImportService
      6. Enrich via EnrichmentService
      7. Return result

    Errors propagate unchanged from each layer.
    """

    def __init__(
        self,
        wc_client,   # WindComicPreproductionClient
        adapter,     # WindComicAdapter (read-only)
        import_service,    # ImportService
        enrichment_service,  # EnrichmentService
    ) -> None:
        self._wc_client = wc_client
        self._adapter = adapter
        self._import_service = import_service
        self._enrichment_service = enrichment_service

    def create_from_idea(
        self,
        idea: str,
        style: str | None = None,
        aspect: str | None = None,
        language: str | None = None,
    ) -> PreproductionResult:
        """Trigger full idea → production pipeline synchronously.

        Raises:
            NormalizationError: blank idea or missing WC artifacts
            WindComicUnavailableError: WC not reachable or auth failure
            WindComicPreproductionError: WC pipeline error/timeout/SSE drop
            HumanEditConflictError: canonical project has human edits
            EnrichmentError: enrichment validation failure
        """
        # 1. Validate input
        if not idea or not idea.strip():
            raise NormalizationError(
                "Idea must not be blank",
                detail="Received empty or whitespace-only idea string",
            )

        # 2. Trigger WC pre-production (auth + SSE)
        wc_result = self._wc_client.create_project(
            idea, style=style, aspect=aspect, language=language,
        )
        wc_project_id = wc_result.wc_project_id
        logger.info("WC pre-production complete: %s", wc_project_id)

        # 3. Read persisted WC bundle (NOT SSE payloads)
        bundle = self._adapter.read_project_bundle(wc_project_id)

        # 4. Validate required artifacts
        self._validate_artifacts(wc_project_id, bundle)

        # 5. Import into canonical model
        import_result = self._import_service.import_project(wc_project_id)
        logger.info(
            "Import complete: canonical=%s, scenes=%d, chars=%d",
            import_result.project_id,
            import_result.scenes_imported,
            import_result.characters_imported,
        )

        # 6. Enrich with source-fact precedence (M4.D)
        enrichment_result = self._enrichment_service.enrich_project(
            import_result.project_id,
        )
        logger.info(
            "Enrichment complete: beats=%d, shots=%d, plans=%d",
            enrichment_result.beats_created,
            enrichment_result.shots_created,
            enrichment_result.plans_created,
        )

        # 7. Return
        return PreproductionResult(
            project_id=import_result.project_id,
            wc_project_id=wc_project_id,
            import_result=import_result,
            enrichment_result=enrichment_result,
        )

    @staticmethod
    def _validate_artifacts(wc_project_id: str, bundle) -> None:
        """Validate that WC persisted data has all required artifacts.

        Raises NormalizationError with diagnosable detail on failure.
        """
        missing: list[str] = []

        if not bundle.scenes:
            missing.append("scenes")
        if not bundle.characters:
            missing.append("characters")
        if not bundle.script_shots:
            missing.append("script shots")
        if not bundle.storyboard_shots:
            missing.append("storyboard shots")

        if missing:
            artifact_list = ", ".join(missing)
            raise NormalizationError(
                f"WC project {wc_project_id} missing required artifacts: {artifact_list}",
                detail=f"wc_project_id={wc_project_id}, missing=[{artifact_list}]",
            )
