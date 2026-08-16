"""API routes for Local AI Film Director (M1–M5)."""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, model_validator

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.errors import (
    ReferenceGenerationError,
    ReferenceIngestError,
    ReferenceLifecycleError,
    ReferenceNotFoundError,
    ReferenceResolutionError,
)
from film_director.generation.comfyui_adapter import ComfyUIAdapter
from film_director.generation.generation_service import GenerationService
from film_director.models.canonical import CameraIntent, ShotSubject
from film_director.models.reference import ReferenceKind
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    ProjectRepository,
    ReferenceAssetRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.services.enrichment_service import EnrichmentService
from film_director.services.import_service import ImportService

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_UPLOAD_CHUNK = 65_536  # 64 KiB


# ---------------------------------------------------------------------------
# Request DTOs for human editing
# ---------------------------------------------------------------------------


class FromIdeaRequest(BaseModel):
    idea: str
    style: str | None = None
    aspect: str | None = None
    language: str | None = None


class BeatEditRequest(BaseModel):
    dramatic_action: str | None = None
    character_intention: str | None = None
    change: str | None = None
    characters: list[str] | None = None
    status: Literal["draft", "approved"] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if all(
            getattr(self, f) is None
            for f in ("dramatic_action", "character_intention", "change", "characters", "status")
        ):
            raise ValueError("At least one field must be provided")
        return self


class ShotEditRequest(BaseModel):
    dramatic_purpose: str | None = None
    subjects: list[ShotSubject] | None = None
    action: str | None = None
    environment: dict | None = None
    camera: CameraIntent | None = None
    lighting: dict | None = None
    audio_intent: dict | None = None
    duration_sec: float | None = None
    status: Literal["draft", "ready"] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if all(
            getattr(self, f) is None
            for f in (
                "dramatic_purpose", "subjects", "action", "environment",
                "camera", "lighting", "audio_intent", "duration_sec", "status",
            )
        ):
            raise ValueError("At least one field must be provided")
        return self


class GenerateReferenceRequest(BaseModel):
    kind: str = "character_body"
    profile_id: str | None = None
    seed: int | None = None

    @model_validator(mode="after")
    def _valid_kind(self):
        allowed = {"character_face", "character_body"}
        if self.kind not in allowed:
            raise ValueError(f"kind must be one of {allowed}")
        return self


def create_router(
    adapter: WindComicAdapter,
    import_service: ImportService,
    project_repo: ProjectRepository,
    seq_repo: SequenceRepository,
    scene_repo: SceneRepository,
    char_repo: CharacterRepository,
    llm_provider,  # LLMProvider protocol
    # M2 services
    enrichment_service: EnrichmentService | None = None,
    beat_repo: BeatRepository | None = None,
    shot_repo: ShotRepository | None = None,
    plan_repo: GenerationPlanRepository | None = None,
    # M3 services
    generation_service: GenerationService | None = None,
    comfyui_adapter: ComfyUIAdapter | None = None,
    request_repo: GenerationRequestRepository | None = None,
    # M4 services
    preproduction_service=None,  # PreproductionService | None
    # M5 services
    ref_asset_repo: ReferenceAssetRepository | None = None,
    ref_ingest_service=None,  # ReferenceIngestService | None
    ref_generation_service=None,  # ReferenceGenerationService | None
    ref_lifecycle_service=None,  # ReferenceLifecycleService | None
    ref_selector=None,  # ReferenceSelector | None
) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # Integration health checks
    # ------------------------------------------------------------------

    @router.get("/integrations/wind-comic/health")
    def wc_health() -> dict:
        h = adapter.health()
        return asdict(h)

    @router.get("/integrations/llm/health")
    def llm_health() -> dict:
        try:
            available = llm_provider.health()
        except Exception:
            available = False
        return {"available": available, "provider": "ollama"}

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    @router.post("/imports/wind-comic/{wc_project_id}")
    def import_project(wc_project_id: str) -> dict:
        result = import_service.import_project(wc_project_id)
        return asdict(result)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    @router.get("/projects")
    def list_projects() -> list[dict]:
        projects = project_repo.list_projects()
        return [p.model_dump() for p in projects]

    @router.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return p.model_dump()

    @router.get("/projects/{project_id}/scenes")
    def get_scenes(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        sequences = seq_repo.get_sequences_by_project(project_id)
        scenes = []
        for seq in sequences:
            scenes.extend(scene_repo.get_scenes_by_sequence(seq.id))
        return [s.model_dump() for s in scenes]

    @router.get("/projects/{project_id}/characters")
    def get_characters(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        chars = char_repo.get_characters_by_project(project_id)
        return [c.model_dump() for c in chars]

    @router.get("/projects/{project_id}/storyboard")
    def get_storyboard(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        wc_project_id = p.wc_project_id
        shots = adapter.get_storyboard(wc_project_id)
        return [asdict(s) for s in shots]

    @router.get("/projects/{project_id}/changes")
    def get_changes(project_id: str) -> list[dict]:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        changes = import_service.check_for_changes(project_id)
        return [asdict(c) for c in changes]

    @router.post("/projects/{project_id}/apply-changes")
    def apply_changes(project_id: str) -> dict:
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        changes = import_service.check_for_changes(project_id)
        if enrichment_service is not None:
            result = enrichment_service.apply_source_changes(project_id, changes)
            return {"applied": len(changes), **result}
        else:
            import_service.apply_detected_changes(project_id, changes)
            return {"applied": len(changes)}

    # ------------------------------------------------------------------
    # M2 Routes
    # ------------------------------------------------------------------

    @router.post("/projects/{project_id}/enrich")
    def enrich_project(project_id: str) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        result = enrichment_service.enrich_project(project_id)
        return asdict(result)

    @router.get("/projects/{project_id}/beats")
    def get_project_beats(project_id: str) -> list[dict]:
        if beat_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        sequences = seq_repo.get_sequences_by_project(project_id)
        all_beats = []
        for seq in sequences:
            scenes = scene_repo.get_scenes_by_sequence(seq.id)
            for scene in scenes:
                all_beats.extend(beat_repo.get_current_beats_by_scene(scene.id))
        return [b.model_dump() for b in all_beats]

    @router.get("/scenes/{scene_id}/beats")
    def get_scene_beats(scene_id: str) -> list[dict]:
        if beat_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        beats = beat_repo.get_current_beats_by_scene(scene_id)
        return [b.model_dump() for b in beats]

    @router.put("/beats/{beat_id}")
    def edit_beat(beat_id: str, body: BeatEditRequest) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        patch = body.model_dump(exclude_none=True)
        result = enrichment_service.edit_beat(beat_id, patch)
        if result is None:
            raise HTTPException(status_code=404, detail="Beat not found or outdated")
        return result.model_dump()

    @router.post("/scenes/{scene_id}/enrich-beats")
    def enrich_scene_beats(scene_id: str, force: bool = Query(False)) -> list[dict]:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        new_beats = enrichment_service.enrich_scene_beats(scene_id, force=force)
        return [b.model_dump() for b in new_beats]

    @router.get("/projects/{project_id}/shots")
    def get_project_shots(project_id: str) -> list[dict]:
        if shot_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        shots = shot_repo.get_current_shots_by_project(project_id)
        return [s.model_dump() for s in shots]

    @router.get("/beats/{beat_id}/shots")
    def get_beat_shots(beat_id: str) -> list[dict]:
        if shot_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        shots = shot_repo.get_current_shots_by_beat(beat_id)
        return [s.model_dump() for s in shots]

    @router.put("/shots/{shot_id}")
    def edit_shot(shot_id: str, body: ShotEditRequest) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        patch = body.model_dump(exclude_none=True)
        result = enrichment_service.edit_shot(shot_id, patch)
        if result is None:
            raise HTTPException(status_code=404, detail="Shot not found or outdated")
        return result.model_dump()

    @router.post("/beats/{beat_id}/plan-coverage")
    def plan_beat_coverage(beat_id: str, force: bool = Query(False)) -> list[dict]:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        shots = enrichment_service.plan_beat_coverage(beat_id, force=force)
        return [s.model_dump() for s in shots]

    @router.get("/shots/{shot_id}/generation-plan")
    def get_generation_plan(shot_id: str) -> dict:
        if plan_repo is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        plan = plan_repo.get_current_plan_by_shot(shot_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Generation plan not found")
        return plan.model_dump()

    @router.post("/projects/{project_id}/assign-strategies")
    def assign_strategies(project_id: str) -> dict:
        if enrichment_service is None:
            raise HTTPException(status_code=501, detail="M2 not available")
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        count = enrichment_service.assign_strategies(project_id)
        return {"plans_created": count}

    # ------------------------------------------------------------------
    # M3 — Generation
    # ------------------------------------------------------------------

    @router.post("/shots/{shot_id}/generate")
    def generate_shot(shot_id: str) -> dict:
        """Synchronous M3 generation: Shot → H3 R2V → Take.

        No queue, no background worker. The request stays open during
        the entire H3 generation (~3-5 min on RTX 5090). Later milestones
        will add async job support.
        """
        if generation_service is None:
            raise HTTPException(status_code=501, detail="M3 generation not available")
        take = generation_service.generate_shot(shot_id)
        return {
            "take_id": take.id,
            "shot_id": take.shot_id,
            "generation_request_id": take.generation_request_id,
            "seed": take.seed,
            "video_path": take.video_path,
            "last_frame_path": take.last_frame_path,
            "status": take.status,
        }

    @router.get("/generation-requests/{request_id}")
    def get_generation_request(request_id: str) -> dict:
        if request_repo is None:
            raise HTTPException(status_code=501, detail="M3 generation not available")
        req = request_repo.get_request(request_id)
        if req is None:
            raise HTTPException(status_code=404, detail="Generation request not found")
        return req.model_dump()

    @router.get("/integrations/comfyui/health")
    def comfyui_health() -> dict:
        if comfyui_adapter is None:
            raise HTTPException(status_code=501, detail="ComfyUI not configured")
        result = comfyui_adapter.health()
        return {
            "available": True,
            "system": result.get("system", {}),
            "devices": result.get("devices", []),
        }

    # ------------------------------------------------------------------
    # M4 — Preproduction
    # ------------------------------------------------------------------

    @router.post("/projects/from-idea")
    def create_from_idea(body: FromIdeaRequest) -> dict:
        """Synchronous idea → WC pre-production → canonical import → enrichment.

        Blocks until the full pipeline completes. No background job.
        """
        if preproduction_service is None:
            raise HTTPException(status_code=501, detail="M4 preproduction not available")
        result = preproduction_service.create_from_idea(
            body.idea, style=body.style, aspect=body.aspect, language=body.language,
        )
        return {
            "project_id": result.project_id,
            "wc_project_id": result.wc_project_id,
            "scenes_imported": result.import_result.scenes_imported,
            "characters_imported": result.import_result.characters_imported,
            "beats_created": result.enrichment_result.beats_created,
            "shots_created": result.enrichment_result.shots_created,
            "plans_created": result.enrichment_result.plans_created,
        }

    # ------------------------------------------------------------------
    # M5 — Reference Management
    # ------------------------------------------------------------------

    def _m5_guard():
        if ref_asset_repo is None:
            raise HTTPException(status_code=501, detail="M5 reference management not available")

    def _resolve_character(character_id: str):
        """Resolve canonical character and its project. Raises 404 if missing."""
        _m5_guard()
        char = char_repo.get_character(character_id)
        if char is None:
            raise HTTPException(status_code=404, detail="Character not found")
        return char

    @router.get("/projects/{project_id}/references")
    def list_project_references(project_id: str) -> list[dict]:
        _m5_guard()
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        assets = ref_asset_repo.list_by_project(project_id)
        return [a.model_dump() for a in assets]

    @router.get("/characters/{character_id}/references")
    def list_character_references(character_id: str) -> list[dict]:
        char = _resolve_character(character_id)
        assets = ref_asset_repo.list_by_character(character_id)
        return [a.model_dump() for a in assets]

    @router.post("/characters/{character_id}/references/register")
    async def register_reference(
        character_id: str,
        kind: str = Form(...),
        file: UploadFile = File(...),
    ) -> dict:
        char = _resolve_character(character_id)
        if ref_ingest_service is None:
            raise HTTPException(status_code=501, detail="M5 reference management not available")

        allowed = {"character_face", "character_body"}
        if kind not in allowed:
            raise HTTPException(status_code=422, detail=f"kind must be one of {allowed}")
        ref_kind = ReferenceKind(kind)

        tmp_path = None
        try:
            # Stream upload to temp file with size limit
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".upload")
            total = 0
            try:
                with os.fdopen(tmp_fd, "wb") as tmp_f:
                    while True:
                        chunk = await file.read(_UPLOAD_CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > _MAX_UPLOAD_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"Upload exceeds {_MAX_UPLOAD_BYTES} byte limit",
                            )
                        tmp_f.write(chunk)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Upload failed: {e}")

            if total == 0:
                raise HTTPException(status_code=422, detail="Empty upload")

            result = ref_ingest_service.register_user_reference(
                project_id=char.project_id,
                kind=ref_kind,
                source_path=tmp_path,
                character_id=character_id,
            )

            if result.outcome == "invalid_image":
                raise ReferenceIngestError(
                    "Invalid image file", detail=result.detail,
                )
            if result.asset is None:
                raise ReferenceIngestError(
                    f"Ingest failed: {result.outcome}", detail=result.detail,
                )

            return result.asset.model_dump()

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @router.post("/characters/{character_id}/references/generate")
    def generate_reference(
        character_id: str,
        body: GenerateReferenceRequest,
    ) -> dict:
        char = _resolve_character(character_id)
        if ref_generation_service is None:
            raise HTTPException(status_code=501, detail="M5 reference management not available")

        ref_kind = ReferenceKind(body.kind)
        result = ref_generation_service.generate_character_reference(
            project_id=char.project_id,
            character_id=character_id,
            character_name=char.name,
            character_appearance=char.appearance,
            kind=ref_kind,
            profile_id=body.profile_id,
            seed=body.seed,
        )
        return {
            "asset": result.asset.model_dump(),
            "request_id": result.request_id,
            "execution_id": result.execution_id,
        }

    def _lifecycle_action(reference_id: str, action: str) -> dict:
        _m5_guard()
        if ref_lifecycle_service is None:
            raise HTTPException(status_code=501, detail="M5 reference management not available")
        try:
            getattr(ref_lifecycle_service, action)(reference_id)
        except ReferenceNotFoundError:
            raise HTTPException(status_code=404, detail="Reference not found")
        except ReferenceLifecycleError as e:
            raise HTTPException(status_code=409, detail=e.message)
        asset = ref_asset_repo.get(reference_id)
        return asset.model_dump()

    @router.post("/references/{reference_id}/approve")
    def approve_reference(reference_id: str) -> dict:
        return _lifecycle_action(reference_id, "approve")

    @router.post("/references/{reference_id}/reject")
    def reject_reference(reference_id: str) -> dict:
        return _lifecycle_action(reference_id, "reject")

    @router.post("/references/{reference_id}/archive")
    def archive_reference(reference_id: str) -> dict:
        return _lifecycle_action(reference_id, "archive")

    @router.post("/references/{reference_id}/pin")
    def pin_reference(reference_id: str) -> dict:
        return _lifecycle_action(reference_id, "pin")

    @router.post("/references/{reference_id}/unpin")
    def unpin_reference(reference_id: str) -> dict:
        return _lifecycle_action(reference_id, "unpin")

    @router.get("/shots/{shot_id}/selected-references")
    def get_selected_references(
        shot_id: str,
        kind: str = Query(default="character_body"),
    ) -> list[dict]:
        _m5_guard()
        if ref_selector is None or shot_repo is None:
            raise HTTPException(status_code=501, detail="M5 reference management not available")

        shot = shot_repo.get_shot(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="Shot not found")

        # Derive project_id through beat→scene→sequence→project
        from film_director.persistence.database import Database
        project_id = None
        if hasattr(ref_asset_repo, '_db'):
            with ref_asset_repo._db.connection() as conn:
                row = conn.execute(
                    """SELECT seq.project_id FROM shots s
                       JOIN beats b ON s.beat_id = b.id
                       JOIN scenes sc ON b.scene_id = sc.id
                       JOIN sequences seq ON sc.sequence_id = seq.id
                       WHERE s.id = ?""",
                    (shot_id,),
                ).fetchone()
                if row:
                    project_id = row["project_id"]
        if project_id is None:
            raise HTTPException(status_code=404, detail="Shot project not found")

        try:
            ref_kind = ReferenceKind(kind)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid kind: {kind}")

        characters = char_repo.get_characters_by_project(project_id)
        all_assets = ref_asset_repo.list_by_project(project_id)

        try:
            selected = ref_selector.select(
                shot=shot, project_id=project_id,
                kind=ref_kind, characters=characters, assets=all_assets,
            )
        except ReferenceResolutionError as e:
            raise HTTPException(status_code=409, detail=e.message)

        return [a.model_dump() for a in selected]

    return router
