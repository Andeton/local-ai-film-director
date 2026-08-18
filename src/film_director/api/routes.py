"""API routes for Local AI Film Director (M1–M7)."""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, model_validator

from film_director.adapters.wind_comic import WindComicAdapter
from film_director.errors import (
    ContinuityError,
    QueueConflictError,
    QueueJobNotFoundError,
    QueueTransitionError,
    QueueValidationError,
    ReferenceGenerationError,
    ReferenceIngestError,
    ReferenceLifecycleError,
    ReferenceNotFoundError,
    ReferenceResolutionError,
    TakeConflictError,
    TakeLifecycleError,
    TakeNotFoundError,
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


class GenerateRequest(BaseModel):
    """Optional operator overrides for generation."""
    prompt_override: str | None = None
    duration_sec: float | None = None
    seed: int | None = None


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


class CreateShotRequest(BaseModel):
    """Create a new shot in a scene."""
    scene_id: str
    action: str = ""
    dramatic_purpose: str = ""
    duration_sec: float = 5.0
    camera: CameraIntent = CameraIntent(shot_size="medium", angle="eye_level", movement="static")
    order_index: int | None = None  # None = append at end


class ReorderShotsRequest(BaseModel):
    """Reorder shots by providing ordered list of shot IDs."""
    shot_ids: list[str]


class GenerateReferenceRequest(BaseModel):
    kind: str = "character_body"
    profile_id: str | None = None
    seed: int | None = None
    prompt_override: str | None = None
    negative_prompt_override: str | None = None

    @model_validator(mode="after")
    def _valid_kind(self):
        allowed = {"character_face", "character_body"}
        if self.kind not in allowed:
            raise ValueError(f"kind must be one of {allowed}")
        return self


_SAFE_SEED_MAX = (1 << 63) - 1


class EnqueueShotRequest(BaseModel):
    takes_count: int = 3
    base_seed: int | None = None
    priority: int = 0

    @model_validator(mode="after")
    def _validate(self):
        if self.takes_count < 1 or self.takes_count > 10:
            raise ValueError("takes_count must be 1–10")
        if self.base_seed is not None and (self.base_seed < 0 or self.base_seed > _SAFE_SEED_MAX):
            raise ValueError(f"base_seed must be in [0, {_SAFE_SEED_MAX}]")
        if self.priority < 0 or self.priority > 100:
            raise ValueError("priority must be 0–100")
        return self


class ReplaceApprovedRequest(BaseModel):
    take_id: str


class EnqueueSceneRequest(BaseModel):
    takes_per_shot: int = 3
    base_seed: int | None = None
    priority: int = 0

    @model_validator(mode="after")
    def _validate(self):
        if self.takes_per_shot < 1 or self.takes_per_shot > 10:
            raise ValueError("takes_per_shot must be 1–10")
        if self.base_seed is not None and (self.base_seed < 0 or self.base_seed > _SAFE_SEED_MAX):
            raise ValueError(f"base_seed must be in [0, {_SAFE_SEED_MAX}]")
        if self.priority < 0 or self.priority > 100:
            raise ValueError("priority must be 0–100")
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
    # M6 services
    queue_service=None,  # QueueService | None
    take_service=None,  # TakeService | None
    take_repo=None,  # TakeRepository | None (for listing)
    ref_ingest_service=None,  # ReferenceIngestService | None
    ref_generation_service=None,  # ReferenceGenerationService | None
    ref_lifecycle_service=None,  # ReferenceLifecycleService | None
    ref_selector=None,  # ReferenceSelector | None
    # M7 services
    continuity_service=None,  # ContinuityService | None
    continuity_state_repo=None,  # ContinuityStateRepository | None
    activity_monitor=None,  # ActivityMonitor | None
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
        # Release LLM model after enrichment
        try:
            from film_director.services.resource_cleanup import unload_ollama_models
            unload_ollama_models()
        except Exception:
            pass
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

    @router.post("/projects/{project_id}/shots")
    def create_shot(project_id: str, body: CreateShotRequest) -> dict:
        """Create a new shot in a scene. Auto-creates beat if needed."""
        if shot_repo is None or beat_repo is None or scene_repo is None:
            raise HTTPException(status_code=501, detail="Shot management not available")
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz
        # Verify scene belongs to project
        scene = scene_repo.get_scene(body.scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene not found")
        now = _dt.now(_tz.utc).isoformat()
        # Create a beat for this shot
        beat_id = f"beat{_uuid.uuid4().hex[:12]}"
        from film_director.models.canonical import Beat
        beat = Beat(
            id=beat_id, scene_id=body.scene_id,
            dramatic_action=body.action or "New shot",
            character_intention="", change="",
            order_index=body.order_index if body.order_index is not None else 999,
            status="draft", source="human", version=1,
            created_at=now, updated_at=now,
        )
        beat_repo.save_beat(beat)
        # Determine order_index
        existing = shot_repo.get_current_shots_by_project(project_id)
        idx = body.order_index if body.order_index is not None else len(existing)
        # Create shot
        shot_id = f"shot{_uuid.uuid4().hex[:12]}"
        shot = ShotSpecificationV1(
            id=shot_id, beat_id=beat_id,
            dramatic_purpose=body.dramatic_purpose, action=body.action,
            camera=body.camera, duration_sec=body.duration_sec,
            order_index=idx, status="draft", source="human", version=1,
            created_at=now, updated_at=now,
        )
        shot_repo.save_shot(shot)
        # Create generation plan
        plan_id = f"plan{_uuid.uuid4().hex[:12]}"
        from film_director.models.canonical import GenerationPlan, ReferenceRequirements
        plan = GenerationPlan(
            id=plan_id, shot_id=shot_id, shot_version=1,
            strategy="REFERENCE_TO_VIDEO",
            reference_requirements=ReferenceRequirements(character_refs=True),
            duration_sec=body.duration_sec, seed_policy="random",
            continuity_mode="last_frame" if idx > 0 else "none",
            selection_reason="Operator-created shot",
            status="ready", version=1, created_at=now, updated_at=now,
        )
        plan_repo.save_plan(plan)
        return shot.model_dump()

    @router.delete("/shots/{shot_id}")
    def delete_shot(shot_id: str) -> dict:
        """Delete a shot that has no Takes."""
        if shot_repo is None:
            raise HTTPException(status_code=501, detail="Shot management not available")
        shot = shot_repo.get_shot(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="Shot not found")
        # Check for Takes
        if take_repo is not None:
            takes = take_repo.get_takes_by_shot(shot_id)
            if takes:
                raise HTTPException(status_code=409, detail="Cannot delete shot with existing Takes")
        shot_repo.delete_shot(shot_id)
        return {"deleted": shot_id}

    @router.post("/projects/{project_id}/reorder-shots")
    def reorder_shots(project_id: str, body: ReorderShotsRequest) -> list[dict]:
        """Reorder shots by providing the desired order of shot IDs."""
        if shot_repo is None:
            raise HTTPException(status_code=501, detail="Shot management not available")
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc).isoformat()
        shots = shot_repo.get_current_shots_by_project(project_id)
        shot_map = {s.id: s for s in shots}
        for sid in body.shot_ids:
            if sid not in shot_map:
                raise HTTPException(status_code=404, detail=f"Shot {sid} not found in project")
        # Update order_index
        with shot_repo._db.connection() as conn:
            for i, sid in enumerate(body.shot_ids):
                conn.execute(
                    "UPDATE shots SET order_index = ?, updated_at = ? WHERE id = ?",
                    (i, now, sid),
                )
        updated = shot_repo.get_current_shots_by_project(project_id)
        updated.sort(key=lambda s: s.order_index)
        return [s.model_dump() for s in updated]

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

    @router.get("/shots/{shot_id}/generation-preview")
    def get_generation_preview(
        shot_id: str,
        prompt_override: str | None = Query(default=None),
        duration_sec: float | None = Query(default=None),
        seed: int | None = Query(default=None),
    ) -> dict:
        """Dry-run preview of what generation would submit.

        Uses production resolution logic but does NOT submit to ComfyUI.
        Accepts optional operator overrides via query params.
        """
        if generation_service is None or plan_repo is None or shot_repo is None:
            raise HTTPException(status_code=501, detail="Generation not available")
        from film_director.generation.parameter_resolver import _seconds_to_grid_frames

        shot = shot_repo.get_shot(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="Shot not found")
        plan = plan_repo.get_current_plan_by_shot(shot_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No generation plan")

        # Determine project
        project_id = None
        with generation_service._db.connection() as conn:
            row = conn.execute(
                "SELECT seq.project_id FROM shots s "
                "JOIN beats b ON s.beat_id = b.id "
                "JOIN scenes sc ON b.scene_id = sc.id "
                "JOIN sequences seq ON sc.sequence_id = seq.id "
                "WHERE s.id = ?", (shot_id,),
            ).fetchone()
            if row:
                project_id = row["project_id"]

        # Determine shot index for continuity
        all_shots = shot_repo.get_current_shots_by_project(project_id) if project_id else []
        all_shots.sort(key=lambda s: s.order_index)
        shot_idx = next((i for i, s in enumerate(all_shots) if s.id == shot_id), 0)
        is_head = shot_idx == 0

        # Continuity check
        predecessor_shot = all_shots[shot_idx - 1] if shot_idx > 0 else None
        predecessor_approved = None
        predecessor_frame_url = None
        continuity_blocked = False
        if predecessor_shot and take_service:
            pred_take = take_service.get_approved_for_shot(predecessor_shot.id)
            if pred_take:
                predecessor_approved = {
                    "take_id": pred_take.id,
                    "shot_id": predecessor_shot.id,
                    "shot_index": shot_idx - 1,
                }
                if pred_take.last_frame_path:
                    predecessor_frame_url = f"/media/{pred_take.last_frame_path}"
            else:
                continuity_blocked = True

        # Resolve references
        refs = ref_asset_repo.list_by_project(project_id) if project_id and ref_asset_repo else []
        char_ref = next((r for r in refs if r.kind.value == "character_face" and r.status.value == "approved" and r.source_state.value == "current"), None)
        env_ref = next((r for r in refs if r.status.value == "approved" and r.source_state.value == "current" and r.kind.value == "storyboard"), None)

        # Workflow selection
        if is_head:
            workflow_id = "h3_r2v_v2"
            workflow_version = "2.0.0"
        else:
            workflow_id = "h3_r2v_image_pack_v1"
            workflow_version = "1.0.0"

        # Duration resolution (operator override or plan default)
        effective_duration = duration_sec if duration_sec is not None else plan.duration_sec
        frames = _seconds_to_grid_frames(effective_duration)
        actual_duration = round(frames / 24.0, 3)

        # Prompt (operator override or existing artifact)
        prompt_text = ""
        prompt_source = "generated"
        existing_prompt = None
        try:
            existing_prompt = generation_service._prompt_repo.get_current_prompt(
                shot.id, shot.version, plan.id, plan.version,
            )
        except Exception:
            pass
        if prompt_override is not None and prompt_override.strip():
            prompt_text = prompt_override
            prompt_source = "operator_override"
        elif existing_prompt:
            prompt_text = existing_prompt.rendered_prompt_text

        # Seed resolution
        effective_seed = seed
        seed_policy = "explicit" if seed is not None else plan.seed_policy
        seed_display = seed if seed is not None else plan.seed

        # Pictures
        pictures = []
        if char_ref:
            pictures.append({
                "picture_index": 1, "role": "CHARACTER IDENTITY",
                "reference_id": char_ref.id, "kind": char_ref.kind.value,
                "status": char_ref.status.value, "source_state": char_ref.source_state.value,
                "thumbnail_url": f"/media/{char_ref.managed_path}",
            })
        if env_ref:
            pictures.append({
                "picture_index": 2, "role": "ENVIRONMENT",
                "reference_id": env_ref.id, "kind": env_ref.kind.value,
                "status": env_ref.status.value, "source_state": env_ref.source_state.value,
                "thumbnail_url": f"/media/{env_ref.managed_path}",
            })
        if not is_head:
            cont_pic = {
                "picture_index": 3, "role": "CONTINUITY FRAME",
                "reference_id": None, "kind": "continuity",
                "status": "blocked" if continuity_blocked else "available",
                "source_state": "predecessor",
                "thumbnail_url": predecessor_frame_url,
            }
            if predecessor_approved:
                cont_pic["reference_id"] = predecessor_approved["take_id"]
                cont_pic["source_label"] = f"Shot {predecessor_approved['shot_index'] + 1} approved Take"
            else:
                cont_pic["source_label"] = f"BLOCKED — approve Shot {shot_idx} first"
            pictures.append(cont_pic)

        return {
            "shot_id": shot_id,
            "shot_index": shot_idx,
            "is_head": is_head,
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "strategy": plan.strategy,
            "continuity_mode": plan.continuity_mode,
            "duration_sec": effective_duration,
            "plan_duration_sec": plan.duration_sec,
            "resolved_frames": frames,
            "resolved_duration_sec": actual_duration,
            "fps": 24,
            "aspect": "16:9",
            "resolution": "1376x768",
            "seed_policy": seed_policy,
            "seed": seed_display,
            "prompt_text": prompt_text,
            "prompt_source": prompt_source,
            "pictures": pictures,
            "continuity_blocked": continuity_blocked,
            "predecessor_shot_index": shot_idx - 1 if shot_idx > 0 else None,
            "can_generate": not continuity_blocked,
        }

    @router.post("/shots/{shot_id}/generate")
    def generate_shot(shot_id: str, body: GenerateRequest = Body(default=GenerateRequest())) -> dict:
        """Synchronous generation with optional operator overrides.

        Accepts optional prompt_override, duration_sec, and seed.
        If omitted, uses plan defaults (backward compatible).
        """
        if generation_service is None:
            raise HTTPException(status_code=501, detail="M3 generation not available")
        _SQLITE_INT64_MAX = (1 << 63) - 1
        kwargs: dict = {}
        if body:
            if body.seed is not None:
                if body.seed < 0 or body.seed > _SQLITE_INT64_MAX:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Seed must be 0..{_SQLITE_INT64_MAX}, got {body.seed}",
                    )
                kwargs["seed_override"] = body.seed
            if body.prompt_override is not None:
                kwargs["prompt_override"] = body.prompt_override
            if body.duration_sec is not None:
                kwargs["duration_override"] = body.duration_sec
        take = generation_service.generate_take(shot_id, **kwargs)
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
        """Wind Comic → canonical import. Enrichment is a separate explicit action."""
        if preproduction_service is None:
            raise HTTPException(status_code=501, detail="M4 preproduction not available")
        monitor = activity_monitor
        if monitor and not monitor.start("creating_project", "wind_comic", body.idea[:80]):
            raise HTTPException(status_code=409, detail="Another operation is in progress")
        try:
            if monitor:
                monitor.update_stage("wind_comic", "Running Wind Comic pre-production...")
            result = preproduction_service.create_from_idea(
                body.idea, style=body.style, aspect=body.aspect, language=body.language,
            )
            if monitor:
                monitor.complete(f"Project {result.project_id} created")
            # Release WC Ollama model if safe
            try:
                from film_director.services.resource_cleanup import unload_ollama_models
                unload_ollama_models()
            except Exception:
                pass
            return {
                "project_id": result.project_id,
                "wc_project_id": result.wc_project_id,
                "scenes_imported": result.import_result.scenes_imported,
                "characters_imported": result.import_result.characters_imported,
                "beats_created": 0,
                "shots_created": 0,
                "plans_created": 0,
            }
        except Exception as e:
            if monitor:
                monitor.fail(str(e)[:200])
            raise

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
            prompt_override=body.prompt_override,
            negative_prompt_override=body.negative_prompt_override,
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

    # ------------------------------------------------------------------
    # M6 — Take Management & Queue
    # ------------------------------------------------------------------

    def _m6_guard():
        if queue_service is None:
            raise HTTPException(status_code=501, detail="M6 take management not available")

    def _validate_idempotency_key(request) -> str:
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise HTTPException(status_code=422, detail="Idempotency-Key header required")
        if len(key) > 128:
            raise HTTPException(status_code=422, detail="Idempotency-Key too long (max 128)")
        return key

    @router.post("/shots/{shot_id}/takes/enqueue", status_code=202)
    def enqueue_shot_takes(shot_id: str, body: EnqueueShotRequest, request: Request) -> dict:
        _m6_guard()
        if shot_repo is not None and shot_repo.get_shot(shot_id) is None:
            raise HTTPException(status_code=404, detail="Shot not found")
        key = _validate_idempotency_key(request)
        try:
            jobs = queue_service.enqueue_shot(
                shot_id=shot_id,
                idempotency_key=key,
                takes_count=body.takes_count,
                base_seed=body.base_seed,
                priority=body.priority,
            )
        except QueueValidationError as e:
            raise HTTPException(status_code=422, detail=e.message)
        except QueueConflictError as e:
            raise HTTPException(status_code=409, detail=e.message)
        return {
            "batch_id": jobs[0].batch_id if jobs else None,
            "shot_id": shot_id,
            "takes_count": len(jobs),
            "jobs": [j.model_dump() for j in jobs],
        }

    @router.post("/scenes/{scene_id}/takes/enqueue", status_code=202)
    def enqueue_scene_takes(scene_id: str, body: EnqueueSceneRequest, request: Request) -> dict:
        _m6_guard()
        if scene_repo is not None and scene_repo.get_scene(scene_id) is None:
            raise HTTPException(status_code=404, detail="Scene not found")
        key = _validate_idempotency_key(request)
        try:
            jobs = queue_service.enqueue_scene(
                scene_id=scene_id,
                idempotency_key=key,
                takes_per_shot=body.takes_per_shot,
                base_seed=body.base_seed,
                priority=body.priority,
            )
        except QueueValidationError as e:
            raise HTTPException(status_code=422, detail=e.message)
        except QueueConflictError as e:
            raise HTTPException(status_code=409, detail=e.message)
        return {
            "batch_id": jobs[0].batch_id if jobs else None,
            "scene_id": scene_id,
            "total_jobs": len(jobs),
            "jobs": [j.model_dump() for j in jobs],
        }

    @router.get("/queue/jobs")
    def list_queue_jobs(
        status: str | None = Query(default=None),
        shot_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict]:
        _m6_guard()
        from film_director.persistence.repositories import QueueJobRepository
        # Use the queue_service's internal repo for listing
        if shot_id:
            jobs = queue_service._queue_repo.list_by_shot(shot_id)
        elif status:
            valid = {"pending", "claimed", "succeeded", "failed", "cancelled"}
            if status not in valid:
                raise HTTPException(status_code=422, detail=f"Invalid status: {status}")
            jobs = queue_service._queue_repo.list_by_status(status, limit=limit)
        else:
            jobs = queue_service._queue_repo.list_by_status("pending", limit=limit)
        return [j.model_dump() for j in jobs[:limit]]

    @router.get("/queue/jobs/{job_id}")
    def get_queue_job(job_id: str) -> dict:
        _m6_guard()
        job = queue_service._queue_repo.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Queue job not found")
        return job.model_dump()

    @router.post("/queue/jobs/{job_id}/cancel")
    def cancel_queue_job(job_id: str) -> dict:
        _m6_guard()
        try:
            job = queue_service.cancel_job(job_id)
        except QueueJobNotFoundError:
            raise HTTPException(status_code=404, detail="Queue job not found")
        except QueueTransitionError as e:
            raise HTTPException(status_code=409, detail=e.message)
        return job.model_dump()

    @router.get("/shots/{shot_id}/takes")
    def list_takes(shot_id: str) -> list[dict]:
        if take_repo is None:
            raise HTTPException(status_code=501, detail="M6 take management not available")
        if shot_repo is not None:
            shot = shot_repo.get_shot(shot_id)
            if shot is None:
                raise HTTPException(status_code=404, detail="Shot not found")
        takes = take_repo.get_takes_by_shot(shot_id)
        return [t.model_dump() for t in takes]

    @router.get("/shots/{shot_id}/approved-take")
    def get_approved_take(shot_id: str) -> dict:
        if take_service is None:
            raise HTTPException(status_code=501, detail="M6 take management not available")
        take = take_service.get_approved_for_shot(shot_id)
        if take is None:
            raise HTTPException(status_code=404, detail="No approved take for this shot")
        return take.model_dump()

    def _take_lifecycle(take_id: str, action: str) -> dict:
        if take_service is None:
            raise HTTPException(status_code=501, detail="M6 take management not available")
        try:
            take = getattr(take_service, action)(take_id)
        except TakeNotFoundError:
            raise HTTPException(status_code=404, detail="Take not found")
        except TakeConflictError as e:
            raise HTTPException(status_code=409, detail=e.message)
        except TakeLifecycleError as e:
            raise HTTPException(status_code=409, detail=e.message)
        return take.model_dump()

    @router.post("/takes/{take_id}/approve")
    def approve_take(take_id: str) -> dict:
        return _take_lifecycle(take_id, "approve")

    @router.post("/takes/{take_id}/reject")
    def reject_take(take_id: str) -> dict:
        return _take_lifecycle(take_id, "reject")

    @router.post("/takes/{take_id}/favorite")
    def favorite_take(take_id: str) -> dict:
        return _take_lifecycle(take_id, "favorite")

    @router.post("/takes/{take_id}/unfavorite")
    def unfavorite_take(take_id: str) -> dict:
        return _take_lifecycle(take_id, "unfavorite")

    # ---------------------------------------------------------------
    # M7 — Continuity
    # ---------------------------------------------------------------

    @router.get("/shots/{shot_id}/continuity-state")
    def get_continuity_state(shot_id: str) -> dict:
        if continuity_state_repo is None:
            raise HTTPException(status_code=501, detail="M7 continuity not available")
        state = continuity_state_repo.get_by_shot(shot_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Continuity state not found")
        return state.model_dump()

    @router.get("/shots/{shot_id}/predecessor")
    def get_predecessor(shot_id: str) -> dict:
        if continuity_service is None:
            raise HTTPException(status_code=501, detail="M7 continuity not available")
        from film_director.continuity.continuity_resolver import ContinuityResolver
        from film_director.persistence.database import Database
        # Use the service's DB to get a connection
        db = continuity_service._db
        with db.connection() as conn:
            scene_id = ContinuityResolver.get_scene_id_for_shot(shot_id, conn)
            if scene_id is None:
                raise HTTPException(status_code=404, detail="Shot not found")
            pred = ContinuityResolver.resolve_predecessor(shot_id, scene_id, conn)
        return {"predecessor_shot_id": pred, "scene_id": scene_id}

    @router.post("/shots/{shot_id}/replace-approved")
    def replace_approved(shot_id: str, body: ReplaceApprovedRequest) -> dict:
        if take_service is None:
            raise HTTPException(status_code=501, detail="M6 take management not available")
        # Verify the replacement Take belongs to this shot
        if take_repo is not None:
            candidate = take_repo.get_take(body.take_id)
            if candidate is None:
                raise HTTPException(status_code=404, detail="Take not found")
            if candidate.shot_id != shot_id:
                raise HTTPException(status_code=409, detail="Take belongs to a different shot")
        try:
            result = take_service.replace_approved(body.take_id)
        except TakeNotFoundError:
            raise HTTPException(status_code=404, detail="Take not found")
        except TakeConflictError as e:
            raise HTTPException(status_code=409, detail=e.message)
        except TakeLifecycleError as e:
            raise HTTPException(status_code=409, detail=e.message)
        return result.model_dump()

    @router.get("/scenes/{scene_id}/continuity-chain")
    def get_continuity_chain(scene_id: str) -> list[dict]:
        if continuity_service is None or continuity_state_repo is None:
            raise HTTPException(status_code=501, detail="M7 continuity not available")
        from film_director.continuity.continuity_resolver import ContinuityResolver
        db = continuity_service._db
        with db.connection() as conn:
            ordered_ids = ContinuityResolver.get_scene_shot_order(scene_id, conn)
        if not ordered_ids:
            raise HTTPException(status_code=404, detail="Scene not found or has no shots")
        states = {s.shot_id: s for s in continuity_state_repo.get_by_scene(scene_id)}
        approved_takes = {}
        if take_repo is not None:
            for sid in ordered_ids:
                t = take_repo.get_approved_for_shot(sid)
                if t is not None:
                    approved_takes[sid] = t
        chain: list[dict] = []
        for i, sid in enumerate(ordered_ids):
            pred = ordered_ids[i - 1] if i > 0 else None
            cs = states.get(sid)
            at = approved_takes.get(sid)
            chain.append({
                "shot_id": sid,
                "order_position": i,
                "predecessor_shot_id": pred,
                "continuity_state": cs.state if cs else None,
                "continuity_revision": cs.continuity_revision if cs else None,
                "approved_take_id": at.id if at else None,
                "has_last_frame": bool(at and at.last_frame_path),
            })
        return chain

    @router.get("/scenes/{scene_id}/outdated-shots")
    def get_outdated_shots(scene_id: str) -> list[dict]:
        if continuity_state_repo is None:
            raise HTTPException(status_code=501, detail="M7 continuity not available")
        states = continuity_state_repo.get_by_scene(scene_id)
        return [
            {"shot_id": s.shot_id, "invalidation_reason": s.invalidation_reason,
             "invalidation_source_shot_id": s.invalidation_source_shot_id}
            for s in states if s.state == "outdated"
        ]

    @router.post("/shots/{shot_id}/continuity/rebuild")
    def rebuild_continuity(shot_id: str) -> dict:
        if continuity_service is None:
            raise HTTPException(status_code=501, detail="M7 continuity not available")
        try:
            state = continuity_service.rebuild_for_shot(shot_id)
        except ContinuityError as e:
            raise HTTPException(status_code=409, detail=e.message)
        return state.model_dump()

    # ------------------------------------------------------------------
    # Activity + System
    # ------------------------------------------------------------------

    @router.get("/activity")
    def get_activity() -> dict:
        if activity_monitor is None:
            return {"status": "idle", "operation": "", "stage": "", "detail": "", "elapsed_seconds": 0, "error": "", "stages_completed": []}
        return activity_monitor.get_state()

    @router.post("/activity/reset")
    def reset_activity() -> dict:
        if activity_monitor:
            activity_monitor.reset()
        return {"status": "idle"}

    @router.get("/system/status")
    def get_system_status() -> dict:
        from film_director.services.resource_cleanup import get_gpu_status, get_ollama_status
        return {
            "gpu": get_gpu_status(),
            "ollama": get_ollama_status(),
        }

    @router.post("/system/free-memory")
    def free_memory() -> dict:
        """Manually free unused GPU memory from Ollama + ComfyUI."""
        if activity_monitor and activity_monitor.is_busy:
            raise HTTPException(status_code=409, detail="Cannot free memory while an operation is active")
        from film_director.services.resource_cleanup import unload_ollama_models, free_comfyui_memory
        return {
            "ollama": unload_ollama_models(),
            "comfyui": free_comfyui_memory(),
        }

    @router.get("/projects/{project_id}/readiness")
    def get_readiness(project_id: str) -> dict:
        """Check generation readiness for a project."""
        if project_repo is None or shot_repo is None:
            raise HTTPException(status_code=501)
        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        shots = shot_repo.get_current_shots_by_project(project_id)
        refs = ref_asset_repo.list_by_project(project_id) if ref_asset_repo else []
        has_char = any(r.kind.value == "character_face" and r.status.value == "approved" and r.source_state.value == "current" for r in refs)
        has_env = any(r.status.value == "approved" and r.source_state.value == "current" and r.kind.value in ("storyboard",) for r in refs)
        has_shots = len(shots) > 0
        approved_takes = 0
        if take_repo:
            for s in shots:
                t = take_repo.get_approved_for_shot(s.id)
                if t:
                    approved_takes += 1
        missing = []
        if not has_shots:
            missing.append("Shot plan (no shots)")
        if not has_char:
            missing.append("Character reference")
        if not has_env:
            missing.append("Environment reference")
        ready = has_shots and has_char and has_env
        next_action = "Review and correct the shot plan." if not has_shots else \
                      "Prepare character and environment references." if not ready else \
                      "Generate Shot 1." if approved_takes == 0 else \
                      f"Review/approve Takes ({approved_takes}/{len(shots)} approved)."
        return {
            "ready": ready,
            "shot_count": len(shots),
            "has_character_ref": has_char,
            "has_environment_ref": has_env,
            "approved_takes": approved_takes,
            "total_shots": len(shots),
            "missing": missing,
            "next_action": next_action,
        }

    # ------------------------------------------------------------------
    # P2 — Scene Assembly
    # ------------------------------------------------------------------

    @router.post("/projects/{project_id}/build-scene")
    def build_scene(project_id: str, request: Request) -> dict:
        """Assemble all approved Takes into a single scene export."""
        if shot_repo is None or take_service is None:
            raise HTTPException(status_code=501, detail="Scene assembly not available")
        from film_director.services.scene_assembly import (
            AssemblyInput, assemble_scene, _sha256,
        )

        p = project_repo.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")

        shots = shot_repo.get_current_shots_by_project(project_id)
        shots.sort(key=lambda s: s.order_index)

        inputs: list[AssemblyInput] = []
        for shot in shots:
            approved = take_service.get_approved_for_shot(shot.id)
            if approved is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Shot {shot.order_index + 1} ({shot.id}) has no approved Take",
                )
            inputs.append(AssemblyInput(
                shot_id=shot.id,
                shot_index=shot.order_index,
                take_id=approved.id,
                video_path=approved.video_path,
                sha256=_sha256(approved.video_path),
            ))

        import os as _os
        _settings = request.app.state.settings
        output_dir = _os.path.join(_settings.storage_root, "exports", project_id)
        output_path = _os.path.join(output_dir, "scene_main_v1.mp4")

        result = assemble_scene(
            inputs=inputs,
            output_path=output_path,
            project_id=project_id,
        )

        return {
            "output_path": result.output_path,
            "output_sha256": result.output_sha256,
            "duration": result.duration,
            "resolution": result.resolution,
            "fps": result.fps,
            "video_codec": result.video_codec,
            "audio_codec": result.audio_codec,
            "size_bytes": result.size_bytes,
            "manifest_path": result.manifest_path,
            "shots_assembled": len(result.inputs),
        }

    @router.get("/projects/{project_id}/scene-export")
    def get_scene_export(project_id: str, request: Request) -> dict:
        """Return metadata of the current scene export if it exists."""
        import os as _os
        _settings = request.app.state.settings
        output_path = _os.path.join(_settings.storage_root, "exports", project_id, "scene_main_v1.mp4")
        manifest_path = _os.path.join(_settings.storage_root, "exports", project_id, "scene_main_v1_manifest.json")
        if not _os.path.isfile(output_path):
            raise HTTPException(status_code=404, detail="No scene export found")
        import json as _json
        manifest = {}
        if _os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                manifest = _json.load(f)
        return {
            "output_path": output_path,
            "media_url": f"/media/{output_path.replace(chr(92), '/')}",
            "manifest": manifest,
        }

    return router
