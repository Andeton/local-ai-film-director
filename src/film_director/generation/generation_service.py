"""GenerationService — shot-to-Take orchestration.

M3 baseline: one-shot synchronous pipeline.
M5.E evolution: ReferenceSelector + ReferenceAsset → count-based workflow
selection (v1/v2), asset-provenance reference_snapshot.
M7.C evolution: continuity-aware — chain heads use R2V, downstream shots
use FLF with predecessor's approved Take's last frame.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import uuid
from datetime import datetime, timezone

from film_director.continuity.continuity_resolver import ContinuityResolver
from film_director.continuity.continuity_service import ContinuityService
from film_director.errors import (
    ContinuityError,
    GenerationError,
    MediaProcessingError,
    ReferenceResolutionError,
    UnsupportedStrategyError,
)
from film_director.generation.comfyui_adapter import ComfyUIAdapter
from film_director.generation.generation_request import GenerationRequest, Take
from film_director.generation.h3_prompt import H3PromptBuilder, H3PromptV1
from film_director.generation.h3_reference_resolver import H3ReferenceResolver
from film_director.generation.media_utils import (
    cleanup_dir,
    create_staging_dir,
    extract_last_frame,
    make_final_dir,
    move_to_final,
    sanitize_filename,
    verify_media,
)
from film_director.generation.parameter_resolver import ParameterResolver
from film_director.generation.workflow_registry import WorkflowResolver
from film_director.models.reference import ReferenceKind
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    CharacterRepository,
    ContinuityStateRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    H3PromptRepository,
    ReferenceAssetRepository,
    ShotRepository,
    TakeRepository,
)
from film_director.services.reference_lifecycle import ReferenceSelector

logger = logging.getLogger(__name__)


class GenerationService:
    """Synchronous generation orchestrator — M5.E production path."""

    def __init__(
        self,
        db: Database,
        comfyui: ComfyUIAdapter,
        storage_root: str,
        project_root: str,
        generation_timeout: float = 600.0,
    ) -> None:
        self._db = db
        self._comfyui = comfyui
        self._storage_root = storage_root

        # Repositories
        self._shot_repo = ShotRepository(db)
        self._plan_repo = GenerationPlanRepository(db)
        self._char_repo = CharacterRepository(db)
        self._prompt_repo = H3PromptRepository(db)
        self._request_repo = GenerationRequestRepository(db)
        self._take_repo = TakeRepository(db)
        self._ref_asset_repo = ReferenceAssetRepository(db)

        # Domain components
        self._ref_resolver = H3ReferenceResolver(storage_root=storage_root)
        self._ref_selector = ReferenceSelector()
        self._prompt_builder = H3PromptBuilder()
        self._workflow_resolver = WorkflowResolver(project_root=project_root)
        self._param_resolver = ParameterResolver()

        # M7: continuity service
        self._continuity_service = ContinuityService(db, storage_root)

        self._timeout = generation_timeout
        self._finalize_callback = None  # M6: set by QueueWorker for atomic finalization

    def generate_shot(self, shot_id: str) -> Take:
        """Synchronous single-take generation (M3 backward compat).

        Delegates to generate_take with take_number=1 and plan-derived seed.
        """
        return self.generate_take(shot_id, take_number=1)

    def generate_take(
        self,
        shot_id: str,
        take_number: int = 1,
        seed_override: int | None = None,
    ) -> Take:
        """Execute generation pipeline for one take of a shot.

        When seed_override is provided (from QueueJob), it replaces the
        plan-derived seed. take_number is persisted in GenerationRequest.

        Returns the persisted Take on success.
        Raises on any failure — pre-request failures leave NO GenerationRequest.
        """
        # ---------------------------------------------------------------
        # PHASE 1: Pre-request resolution (no GenRequest on failure)
        # ---------------------------------------------------------------

        # Step 1-2: Load current shot + plan
        shot = self._shot_repo.get_shot(shot_id)
        if shot is None:
            raise GenerationError(f"Shot not found: {shot_id}")

        plan = self._plan_repo.get_current_plan_by_shot(shot_id)
        if plan is None:
            raise GenerationError(f"No current GenerationPlan for shot {shot_id}")

        if plan.shot_version != shot.version:
            raise GenerationError(
                f"Plan shot_version={plan.shot_version} != shot.version={shot.version}"
            )

        if plan.strategy not in ("REFERENCE_TO_VIDEO", "FIRST_LAST_FRAME"):
            raise UnsupportedStrategyError(
                f"Strategy {plan.strategy!r} not supported",
                detail=f"shot_id={shot_id}",
            )

        # Step 3: Resolve project context
        project_id = self._find_project_id(shot_id)

        # Step 4: M7 continuity resolution — determines chain head vs downstream
        continuity_input = self._continuity_service.resolve_for_generation(shot_id)
        is_continuity_shot = continuity_input is not None

        if is_continuity_shot:
            # ---- DOWNSTREAM CONTINUITY PATH (FLF) ----
            import dataclasses as _dc
            from film_director.continuity.continuity_binding import ContinuityBinding

            # Select FLF workflow
            workflow_def = self._workflow_resolver.resolve_for_continuity(
                has_continuity_frame=True, reference_count=0,
            )

            # Build continuity prompt (no <Picture N> tags)
            prompt = self._prompt_repo.get_current_prompt(
                shot.id, shot.version, plan.id, plan.version,
            )
            if prompt is None:
                prompt = self._prompt_builder.build_continuity_prompt(shot, plan)
                self._prompt_repo.save_prompt(prompt)

            # Load and verify FLF workflow template
            template = self._workflow_resolver.load_template(workflow_def)

            # Upload the predecessor's last frame
            frame_ext = os.path.splitext(continuity_input.frame_path)[1]
            frame_filename = f"cont_{uuid.uuid4().hex[:8]}{frame_ext}"
            uploaded_frame_name = self._comfyui.upload_image(
                continuity_input.frame_path, frame_filename,
            )

            # Build ContinuityBinding with uploaded filename
            binding = ContinuityBinding(
                continuity_state_id=continuity_input.continuity_state_id,
                upstream_shot_id=continuity_input.upstream_shot_id,
                upstream_take_id=continuity_input.upstream_take_id,
                upstream_take_number=continuity_input.upstream_take_number,
                local_path=continuity_input.frame_path,
                content_sha256=continuity_input.frame_sha256,
                continuity_revision=continuity_input.continuity_revision,
                continuity_fingerprint=continuity_input.continuity_fingerprint,
                uploaded_filename=uploaded_frame_name,
            )

            # Resolve seed
            if seed_override is not None:
                seed = seed_override
            else:
                seed = self._param_resolver.resolve_seed(plan, take_number=take_number)

            output_prefix = f"flf/{shot_id}/{uuid.uuid4().hex[:8]}"

            # Build FLF injections
            injections = self._param_resolver.build_continuity_injections(
                plan=plan,
                prompt_text=prompt.rendered_prompt_text,
                workflow_def=workflow_def,
                continuity_binding=binding,
                seed=seed,
                output_prefix=output_prefix,
            )

            # Build continuity snapshot for immutable request
            continuity_snapshot = {
                "continuity_state_id": binding.continuity_state_id,
                "upstream_shot_id": binding.upstream_shot_id,
                "upstream_take_id": binding.upstream_take_id,
                "upstream_take_number": binding.upstream_take_number,
                "upstream_last_frame_managed_path": os.path.relpath(
                    binding.local_path, self._storage_root,
                ),
                "upstream_last_frame_sha256": binding.content_sha256,
                "continuity_revision": binding.continuity_revision,
                "continuity_fingerprint": binding.continuity_fingerprint,
                "uploaded_filename": binding.uploaded_filename,
                "workflow_definition_id": workflow_def.id,
                "workflow_definition_version": workflow_def.version,
                "workflow_template_fingerprint": workflow_def.template_fingerprint,
                "first_frame_node_id": workflow_def.parameter_mappings["first_frame"]["node_id"],
                "first_frame_field": workflow_def.parameter_mappings["first_frame"]["field"],
            }

            # No character reference bindings for FLF
            uploaded_bindings = []

            # Apply injections
            submission_workflow = self._param_resolver.apply_injections(template, injections)

            # Persist continuity state
            scene_id = None
            with self._db.connection() as conn:
                scene_id = ContinuityResolver.get_scene_id_for_shot(shot_id, conn)
            if scene_id:
                self._continuity_service.persist_state(shot_id, scene_id, continuity_input)

        else:
            # ---- CHAIN HEAD PATH (R2V) — unchanged from M5.E ----
            if plan.strategy != "REFERENCE_TO_VIDEO":
                raise UnsupportedStrategyError(
                    f"Strategy {plan.strategy!r} not supported for chain head",
                    detail=f"shot_id={shot_id}",
                )

            characters = self._char_repo.get_characters_by_project(project_id)
            all_assets = self._ref_asset_repo.list_by_project(project_id)

            selected_assets = self._ref_selector.select(
                shot=shot,
                project_id=project_id,
                kind=ReferenceKind.CHARACTER_BODY,
                characters=characters,
                assets=all_assets,
            )

            workflow_def = self._workflow_resolver.resolve_for_reference_count(
                len(selected_assets),
            )

            resolved_bindings = self._ref_resolver.resolve_from_assets(
                shot, selected_assets, characters,
            )

            prompt = self._prompt_repo.get_current_prompt(
                shot.id, shot.version, plan.id, plan.version,
            )
            if prompt is None:
                prompt = self._prompt_builder.build(shot, plan, resolved_bindings)
                self._prompt_repo.save_prompt(prompt)

            template = self._workflow_resolver.load_template(workflow_def)

            uploaded_bindings = self._upload_references(resolved_bindings)

            if seed_override is not None:
                seed = seed_override
            else:
                seed = self._param_resolver.resolve_seed(plan, take_number=take_number)

            output_prefix = f"m3/{shot_id}/{uuid.uuid4().hex[:8]}"

            injections = self._param_resolver.build_injections(
                plan=plan,
                shot=shot,
                prompt=prompt,
                workflow_def=workflow_def,
                uploaded_bindings=uploaded_bindings,
                seed=seed,
                output_prefix=output_prefix,
            )

            submission_workflow = self._param_resolver.apply_injections(template, injections)
            continuity_snapshot = None

        # ---------------------------------------------------------------
        # PHASE 2: Request creation + execution (failures persist request)
        # ---------------------------------------------------------------

        request_id = f"greq{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        gen_request = GenerationRequest(
            id=request_id,
            shot_id=shot.id,
            shot_version=shot.version,
            generation_plan_id=plan.id,
            generation_plan_version=plan.version,
            prompt_artifact_id=prompt.id,
            prompt_artifact_version=prompt.version,
            workflow_definition_id=workflow_def.id,
            workflow_definition_version=workflow_def.version,
            workflow_template_fingerprint=workflow_def.template_fingerprint,
            take_number=take_number,
            parameters_snapshot=[
                {"name": i.name, "node_id": i.node_id, "field": i.field, "value": i.value}
                for i in injections
            ],
            reference_snapshot=[
                {
                    "reference_asset_id": b.reference_asset_id,
                    "reference_kind": b.reference_kind,
                    "subject_index": b.subject_index,
                    "character_id": b.character_id,
                    "character_name": b.character_name,
                    "appearance": b.appearance,
                    "picture_index": b.picture_index,
                    "local_path": b.local_path,
                    "content_sha256": b.content_sha256,
                    "uploaded_filename": b.uploaded_filename,
                }
                for b in uploaded_bindings
            ],
            seed=seed,
            continuity_snapshot=continuity_snapshot,
            status="pending",
        )

        # Step 15: Persist request BEFORE submit
        self._request_repo.create_request(gen_request)

        staging_dir = None
        final_dir = None

        try:
            # Step 16: Submit to ComfyUI
            client_id = uuid.uuid4().hex
            prompt_id = self._comfyui.submit(submission_workflow, client_id)
            self._request_repo.update_status(
                request_id, "queued",
                comfyui_prompt_id=prompt_id,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )

            # Step 17: Monitor via WebSocket
            self._request_repo.update_status(request_id, "running")
            self._comfyui.monitor(prompt_id, client_id, timeout=self._timeout)

            # Step 18: Get result from history
            output_node_id = self._get_output_node_id(workflow_def)
            result = self._comfyui.get_result(prompt_id, output_node_id)

            # Step 19: Download to staging
            staging_dir = create_staging_dir(self._storage_root, request_id)
            output_ref = result.outputs[0]
            safe_name = sanitize_filename(output_ref.filename)
            staged_video = os.path.join(staging_dir, safe_name)
            self._comfyui.download_output(output_ref, staged_video)

            # Step 20a: Verify media
            verify_media(staged_video)

            # Step 20b: Extract last frame
            last_frame_path = os.path.join(staging_dir, "last_frame.png")
            extract_last_frame(staged_video, last_frame_path)

            # Step 21: Move staging → final
            final_dir = make_final_dir(self._storage_root, project_id, shot_id, take_number)
            move_to_final(staging_dir, final_dir)

            final_video = os.path.join(final_dir, safe_name)
            final_last_frame = os.path.join(final_dir, "last_frame.png")

            # Step 22: Atomic DB finalization
            take = Take(
                id=f"take{uuid.uuid4().hex[:12]}",
                shot_id=shot.id,
                generation_request_id=request_id,
                seed=seed,
                video_path=final_video,
                audio_path=None,  # H3 muxes audio into video
                last_frame_path=final_last_frame,
                status="succeeded",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            try:
                with self._db.connection() as conn:
                    self._take_repo.save_take(take, conn=conn)
                    self._request_repo.update_status(
                        request_id, "succeeded",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        conn=conn,
                    )
                    # M6: atomic QueueJob finalization callback
                    if self._finalize_callback is not None:
                        self._finalize_callback(take, conn)
            except Exception as db_err:
                # DB finalization failed — clean up final directory
                logger.error("DB finalization failed: %s", db_err)
                if final_dir:
                    cleanup_dir(final_dir)
                self._request_repo.update_status(
                    request_id, "failed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    error=f"DB finalization failed: {db_err}",
                )
                raise GenerationError(
                    f"DB finalization failed: {db_err}",
                ) from db_err

            # Cleanup staging (files already moved)
            if staging_dir:
                cleanup_dir(staging_dir)

            return take

        except GenerationError:
            raise
        except Exception as e:
            # Post-request failure — mark failed, cleanup
            logger.error("Generation failed for %s: %s", request_id, e)
            try:
                self._request_repo.update_status(
                    request_id, "failed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    error=str(e)[:1000],
                )
            except Exception:
                logger.error("Failed to update request status to failed")
            if staging_dir:
                cleanup_dir(staging_dir)
            if final_dir and os.path.isdir(final_dir):
                cleanup_dir(final_dir)
            raise GenerationError(
                f"Generation failed: {e}",
                detail=f"request_id={request_id}",
            ) from e

    def finalize_from_result(
        self,
        request_id: str,
        shot_id: str,
        take_number: int,
        seed: int,
        prompt_id: str,
        output_node_id: str = "92",
    ) -> Take:
        """Download, validate, and finalize a completed ComfyUI result.

        Used by QueueWorker recovery to finalize a prompt that completed
        externally. Never calls submit(). Uses the exact persisted request.

        Returns the persisted Take on success.
        """
        project_id = self._find_project_id(shot_id)
        result = self._comfyui.get_result(prompt_id, output_node_id)

        staging_dir = create_staging_dir(self._storage_root, request_id)
        final_dir = None
        try:
            output_ref = result.outputs[0]
            safe_name = sanitize_filename(output_ref.filename)
            staged_video = os.path.join(staging_dir, safe_name)
            self._comfyui.download_output(output_ref, staged_video)

            verify_media(staged_video)

            last_frame_path = os.path.join(staging_dir, "last_frame.png")
            extract_last_frame(staged_video, last_frame_path)

            final_path = os.path.join(
                self._storage_root, "takes", project_id, shot_id, f"take_{take_number}",
            )
            if os.path.isdir(final_path):
                # Final dir exists — check if valid Take already there
                existing_takes = self._take_repo.get_takes_by_shot(shot_id)
                for t in existing_takes:
                    if t.generation_request_id == request_id:
                        cleanup_dir(staging_dir)
                        return t  # Already finalized
                raise GenerationError(
                    f"Final directory exists but no matching Take: {final_path}",
                )

            final_dir = make_final_dir(self._storage_root, project_id, shot_id, take_number)
            move_to_final(staging_dir, final_dir)

            final_video = os.path.join(final_dir, safe_name)
            final_last_frame = os.path.join(final_dir, "last_frame.png")

            take = Take(
                id=f"take{uuid.uuid4().hex[:12]}",
                shot_id=shot_id,
                generation_request_id=request_id,
                seed=seed,
                video_path=final_video,
                last_frame_path=final_last_frame,
                status="succeeded",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            with self._db.connection() as conn:
                self._take_repo.save_take(take, conn=conn)
                self._request_repo.update_status(
                    request_id, "succeeded",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    conn=conn,
                )
                if self._finalize_callback is not None:
                    self._finalize_callback(take, conn)

            cleanup_dir(staging_dir)
            return take

        except Exception as e:
            if staging_dir:
                cleanup_dir(staging_dir)
            if final_dir and os.path.isdir(final_dir):
                cleanup_dir(final_dir)
            raise GenerationError(
                f"Recovery finalization failed: {e}",
                detail=f"request_id={request_id}",
            ) from e

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _upload_references(self, resolved_bindings):
        uploaded = []
        for i, binding in enumerate(resolved_bindings):
            ext = os.path.splitext(binding.local_path)[1]
            filename = f"ref_{i}_{uuid.uuid4().hex[:8]}{ext}"
            actual_name = self._comfyui.upload_image(binding.local_path, filename)
            uploaded.append(dataclasses.replace(binding, uploaded_filename=actual_name))
        return uploaded

    def _get_output_node_id(self, workflow_def) -> str:
        """Get the output node ID from workflow definition mappings."""
        output_mapping = workflow_def.parameter_mappings.get("output_prefix")
        if output_mapping:
            return output_mapping["node_id"]
        return "92"  # verified fallback

    def _find_project_id(self, shot_id: str) -> str:
        """Derive project_id from shot via beat→scene→sequence→project chain."""
        with self._db.connection() as conn:
            row = conn.execute(
                """
                SELECT seq.project_id
                FROM shots s
                JOIN beats b ON s.beat_id = b.id
                JOIN scenes sc ON b.scene_id = sc.id
                JOIN sequences seq ON sc.sequence_id = seq.id
                WHERE s.id = ?
                """,
                (shot_id,),
            ).fetchone()
        if row is None:
            raise GenerationError(f"Cannot find project for shot {shot_id}")
        return row["project_id"]
