"""ReferenceGenerationService — ComfyUI character reference image generation (M5.C).

Generates character reference images using versioned/selectable workflow
profiles. Both Z-Image Turbo and Krea 2 Turbo are supported as selectable
profiles. ReferenceGenerationRequest captures the exact immutable input
snapshot; ReferenceGenerationExecution tracks mutable lifecycle.

No approval/selection (M5.D). No H3 binding (M5.E). No API (M5.G).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from film_director.errors import ReferenceGenerationError
from film_director.generation.comfyui_adapter import ComfyUIAdapter
from film_director.models.reference import (
    ReferenceAsset,
    ReferenceGenerationExecution,
    ReferenceGenerationRequest,
    ReferenceKind,
    ReferenceSource,
    ReferenceSourceState,
    ReferenceStatus,
)
from film_director.persistence.repositories import (
    ReferenceAssetRepository,
    ReferenceGenerationExecutionRepository,
    ReferenceGenerationRequestRepository,
)
from film_director.services.reference_service import _compute_sha256, _validate_image

logger = logging.getLogger(__name__)

_SHA256_BUF = 65_536
_DEFAULT_NEGATIVE = "text, labels, watermark, blurry, low quality, deformed, split panels, grid, multiple views, character sheet"


# ---------------------------------------------------------------------------
# Generator Profile — versioned selectable image model configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceGeneratorProfile:
    """Immutable versioned configuration for one reference image generator."""

    id: str
    version: str
    display_name: str
    template_filename: str
    template_fingerprint: str
    prompt_node_id: str
    prompt_field: str
    negative_node_id: str
    negative_field: str
    seed_node_id: str
    seed_field: str
    output_node_id: str
    output_prefix_field: str
    default_steps: int = 8
    default_cfg: float = 1.5
    default_width: int = 1024
    default_height: int = 1024
    recommended_default: bool = False
    required_models: list[str] = field(default_factory=list)
    required_nodes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Frozen profile definitions
# ---------------------------------------------------------------------------

_Z_IMAGE_TURBO_V1 = ReferenceGeneratorProfile(
    id="z_image_turbo_v1",
    version="1.0.0",
    display_name="Z-Image Turbo",
    template_filename="z_image_turbo_v1.json",
    template_fingerprint="0f2217c2798581cc5ad3eb6e41e987163208439c92a54592ef487aaa77db72ac",
    prompt_node_id="4",
    prompt_field="text",
    negative_node_id="5",
    negative_field="text",
    seed_node_id="7",
    seed_field="seed",
    output_node_id="9",
    output_prefix_field="filename_prefix",
    default_steps=8,
    default_cfg=1.5,
    default_width=1024,
    default_height=1024,
    recommended_default=True,
    required_models=["z_image_turbo_bf16.safetensors", "qwen_3_4b.safetensors", "ae.safetensors"],
    required_nodes=["UNETLoader", "CLIPLoader", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage"],
)

_KREA2_TURBO_V1 = ReferenceGeneratorProfile(
    id="krea2_turbo_v1",
    version="1.0.0",
    display_name="Krea 2 Turbo",
    template_filename="krea2_turbo_v1.json",
    template_fingerprint="a93f03a8a9efa6437f163a4d3f358a783a5352e215efe979d5c46c0d19657f8d",
    prompt_node_id="4",
    prompt_field="text",
    negative_node_id="5",
    negative_field="text",
    seed_node_id="7",
    seed_field="seed",
    output_node_id="9",
    output_prefix_field="filename_prefix",
    default_steps=8,
    default_cfg=1.0,
    default_width=1024,
    default_height=1024,
    recommended_default=False,
    required_models=["krea2_turbo_fp8_scaled.safetensors", "Qwen3-VL-4B-Instruct-abliterated-fp8_scaled.safetensors", "qwen_image_vae.safetensors"],
    required_nodes=["UNETLoader", "CLIPLoader", "Krea2PromptWeight", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage"],
)

_PROFILES: dict[str, ReferenceGeneratorProfile] = {
    _Z_IMAGE_TURBO_V1.id: _Z_IMAGE_TURBO_V1,
    _KREA2_TURBO_V1.id: _KREA2_TURBO_V1,
}


def get_reference_generator_profiles() -> dict[str, ReferenceGeneratorProfile]:
    """Return all registered reference generator profiles."""
    return dict(_PROFILES)


def _get_default_profile() -> ReferenceGeneratorProfile:
    for p in _PROFILES.values():
        if p.recommended_default:
            return p
    return next(iter(_PROFILES.values()))


# ---------------------------------------------------------------------------
# Generation result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceGenerationResult:
    asset: ReferenceAsset
    request_id: str
    execution_id: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_prompt(name: str, appearance: str, kind: ReferenceKind) -> str:
    """Build a deterministic character reference prompt."""
    pose = "full body standing pose" if kind == ReferenceKind.CHARACTER_BODY else "portrait headshot"
    return (
        f"A character reference photo of {name}. {appearance}. "
        f"{pose}, neutral studio lighting, clean neutral background. "
        f"High detail, cinematic quality, no text, no labels"
    )


def _compute_appearance_hash(appearance: str) -> str:
    return hashlib.sha256(appearance.encode("utf-8")).hexdigest()


class ReferenceGenerationService:
    """Generates character reference images via ComfyUI using selectable profiles."""

    def __init__(
        self,
        asset_repo: ReferenceAssetRepository,
        request_repo: ReferenceGenerationRequestRepository,
        execution_repo: ReferenceGenerationExecutionRepository,
        comfyui: ComfyUIAdapter,
        storage_root: str,
        project_root: str,
    ) -> None:
        self._asset_repo = asset_repo
        self._request_repo = request_repo
        self._execution_repo = execution_repo
        self._comfyui = comfyui
        self._storage_root = storage_root
        self._project_root = project_root

    def generate_character_reference(
        self,
        project_id: str,
        character_id: str,
        character_name: str,
        character_appearance: str,
        kind: ReferenceKind = ReferenceKind.CHARACTER_BODY,
        profile_id: str | None = None,
        seed: int | None = None,
    ) -> ReferenceGenerationResult:
        """Generate a character reference image via ComfyUI.

        Returns ReferenceGenerationResult with CANDIDATE asset on success.
        Raises ReferenceGenerationError on failure.
        """
        # 1. Resolve profile
        if profile_id:
            profile = _PROFILES.get(profile_id)
            if profile is None:
                raise ReferenceGenerationError(
                    f"Unknown generator profile: {profile_id}",
                    detail=f"available={list(_PROFILES.keys())}",
                )
        else:
            profile = _get_default_profile()

        # 2. Build prompt
        prompt_text = _build_prompt(character_name, character_appearance, kind)
        negative_text = _DEFAULT_NEGATIVE
        appearance_hash = _compute_appearance_hash(character_appearance)
        actual_seed = seed if seed is not None else int.from_bytes(os.urandom(4), "big")

        # 3. Create immutable request
        now = _now_iso()
        request_id = f"rgreq_{uuid.uuid4().hex[:12]}"
        gen_request = ReferenceGenerationRequest(
            id=request_id,
            project_id=project_id,
            character_id=character_id,
            requested_kind=kind,
            source_appearance_hash=appearance_hash,
            prompt=prompt_text,
            negative_prompt=negative_text,
            workflow_definition_id=profile.id,
            workflow_definition_version=profile.version,
            workflow_template_fingerprint=profile.template_fingerprint,
            parameters_snapshot=[
                {"name": "steps", "value": profile.default_steps},
                {"name": "cfg", "value": profile.default_cfg},
                {"name": "width", "value": profile.default_width},
                {"name": "height", "value": profile.default_height},
                {"name": "sampler", "value": "euler"},
                {"name": "scheduler", "value": "simple"},
            ],
            seed=actual_seed,
            created_at=now,
        )
        self._request_repo.create(gen_request)

        # 4. Create pending execution
        execution_id = f"rgexe_{uuid.uuid4().hex[:12]}"
        execution = ReferenceGenerationExecution(
            id=execution_id,
            request_id=request_id,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        self._execution_repo.create(execution)

        # 5. Load and inject workflow template
        template_path = os.path.join(
            self._project_root, "workflows", "reference", profile.template_filename,
        )
        try:
            with open(template_path) as f:
                workflow = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._fail_execution(execution_id, f"template load failed: {e}")
            raise ReferenceGenerationError(
                f"Failed to load workflow template: {e}",
                detail=f"template={template_path}",
            ) from e

        # Inject parameters
        workflow = copy.deepcopy(workflow)
        workflow[profile.prompt_node_id]["inputs"][profile.prompt_field] = prompt_text
        workflow[profile.negative_node_id]["inputs"][profile.negative_field] = negative_text
        workflow[profile.seed_node_id]["inputs"][profile.seed_field] = actual_seed

        output_prefix = f"ref_gen/{project_id}/{character_id}/{uuid.uuid4().hex[:8]}"
        workflow[profile.output_node_id]["inputs"][profile.output_prefix_field] = output_prefix

        # 6. Submit to ComfyUI
        client_id = uuid.uuid4().hex
        try:
            comfyui_prompt_id = self._comfyui.submit(workflow, client_id)
            self._execution_repo.update_status(
                execution_id, status="running",
                comfyui_prompt_id=comfyui_prompt_id,
                submitted_at=_now_iso(),
            )
        except Exception as e:
            self._fail_execution(execution_id, f"submit failed: {e}")
            raise ReferenceGenerationError(
                f"ComfyUI submission failed: {e}",
            ) from e

        # 7. Monitor via WebSocket + get result (same pattern as M3 GenerationService)
        try:
            self._comfyui.monitor(comfyui_prompt_id, client_id, timeout=600.0)
            gen_result = self._comfyui.get_result(
                comfyui_prompt_id, profile.output_node_id,
            )
        except Exception as e:
            self._fail_execution(execution_id, f"execution failed: {e}")
            raise ReferenceGenerationError(
                f"ComfyUI execution failed: {e}",
            ) from e

        if not gen_result.outputs:
            self._fail_execution(execution_id, "no output images")
            raise ReferenceGenerationError("ComfyUI produced no output images")

        # 8. Download output to managed storage
        asset_id = f"ref_{uuid.uuid4().hex[:12]}"
        managed_relative = os.path.join("references", project_id, asset_id, "original.png")
        managed_absolute = os.path.join(self._storage_root, managed_relative)

        # Path confinement
        if not Path(managed_absolute).resolve().is_relative_to(Path(self._storage_root).resolve()):
            self._fail_execution(execution_id, "path confinement violation")
            raise ReferenceGenerationError("Path confinement violation")

        os.makedirs(os.path.dirname(managed_absolute), exist_ok=True)
        try:
            self._comfyui.download_output(gen_result.outputs[0], managed_absolute)
        except Exception as e:
            self._fail_execution(execution_id, f"download failed: {e}")
            raise ReferenceGenerationError(
                f"Failed to download generated image: {e}",
            ) from e

        # 9. Validate downloaded image
        img_info = _validate_image(managed_absolute)
        if img_info is None:
            try:
                os.remove(managed_absolute)
            except OSError:
                pass
            self._fail_execution(execution_id, "generated file is not a valid image")
            raise ReferenceGenerationError("Generated file is not a valid image")

        width, height, fmt = img_info
        content_sha256 = _compute_sha256(managed_absolute)

        # 10. Create ReferenceAsset
        asset = ReferenceAsset(
            id=asset_id,
            project_id=project_id,
            character_id=character_id,
            shot_id=None,
            kind=kind,
            source=ReferenceSource.GENERATED,
            managed_path=managed_relative,
            content_sha256=content_sha256,
            source_provenance=request_id,
            source_fingerprint=appearance_hash,
            status=ReferenceStatus.CANDIDATE,
            source_state=ReferenceSourceState.CURRENT,
            pinned=False,
            width=width,
            height=height,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self._asset_repo.save(asset)

        # 11. Mark execution succeeded
        self._execution_repo.update_status(
            execution_id, status="succeeded",
            output_reference_asset_id=asset_id,
            completed_at=_now_iso(),
        )

        return ReferenceGenerationResult(
            asset=asset,
            request_id=request_id,
            execution_id=execution_id,
        )

    def _fail_execution(self, execution_id: str, error: str) -> None:
        try:
            self._execution_repo.update_status(
                execution_id, status="failed",
                error=error, completed_at=_now_iso(),
            )
        except Exception:
            logger.warning("Failed to update execution %s to failed", execution_id, exc_info=True)
