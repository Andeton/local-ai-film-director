"""WorkflowDefinition and WorkflowResolver — versioned H3 workflow registry.

M3 has exactly one production workflow definition: H3 R2V v1.
The resolver maps generic strategies to frozen workflow definitions,
loads and fingerprint-verifies templates, and provides template metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from film_director.errors import UnsupportedStrategyError, WorkflowTemplateError

_SHA256_BUF = 65_536


@dataclass(frozen=True)
class WorkflowDefinition:
    """Immutable versioned workflow contract.

    (id, version) identifies one immutable binding of template + mappings.
    """

    id: str
    version: str
    strategy: str
    template_path: str
    template_fingerprint: str
    parameter_mappings: dict = field(default_factory=dict)
    required_models: list[str] = field(default_factory=list)
    required_nodes: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# H3 R2V v1 definition — built from M3.A verified runtime contract
# ---------------------------------------------------------------------------

_H3_R2V_V1_FINGERPRINT = (
    "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4"
)

_H3_R2V_V1_MAPPINGS: dict[str, dict[str, str]] = {
    "prompt": {"node_id": "104", "field": "prompt"},
    "ref_image_0": {"node_id": "200", "field": "image"},
    "duration": {"node_id": "111", "field": "value"},
    "seed": {"node_id": "15", "field": "noise_seed"},
    "aspect": {"node_id": "115", "field": "aspect_ratio"},
    "output_prefix": {"node_id": "92", "field": "filename_prefix"},
}

_H3_R2V_V1_REQUIRED_NODES = ["104", "200", "111", "15", "115", "92", "107"]

_H3_R2V_V1_REQUIRED_MODELS = [
    "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "minimax_h3_video_vae_fp16.safetensors",
    "minimax_h3_audio_vae_fp32.safetensors",
]


def _build_h3_r2v_v1(project_root: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="h3_r2v_v1",
        version="1.0.0",
        strategy="REFERENCE_TO_VIDEO",
        template_path=os.path.join(project_root, "workflows", "h3", "r2v_v1.json"),
        template_fingerprint=_H3_R2V_V1_FINGERPRINT,
        parameter_mappings=dict(_H3_R2V_V1_MAPPINGS),
        required_models=list(_H3_R2V_V1_REQUIRED_MODELS),
        required_nodes=list(_H3_R2V_V1_REQUIRED_NODES),
        capabilities=["reference_to_video", "audio_muxed"],
        constraints={
            "max_reference_images": 9,
            "materialized_reference_slots": 1,
            "min_duration_sec": 0.21,
            "max_duration_sec": 15.08,
            "fps": 24,
            "frame_grid": "17k+5",
            "seed_max": 0xFFFFFFFFFFFFFFFF,
        },
    )


# ---------------------------------------------------------------------------
# H3 R2V v2 definition — two materialized reference slots (M5.E)
# ---------------------------------------------------------------------------

_H3_R2V_V2_FINGERPRINT = (
    "b4930400f0433fdd09f3bd4f8a20d55394050c7d4558eddd6f2e7046e110f3b9"
)

_H3_R2V_V2_MAPPINGS: dict[str, dict[str, str]] = {
    "prompt": {"node_id": "104", "field": "prompt"},
    "ref_image_0": {"node_id": "200", "field": "image"},
    "ref_image_1": {"node_id": "201", "field": "image"},
    "duration": {"node_id": "111", "field": "value"},
    "seed": {"node_id": "15", "field": "noise_seed"},
    "aspect": {"node_id": "115", "field": "aspect_ratio"},
    "output_prefix": {"node_id": "92", "field": "filename_prefix"},
}

_H3_R2V_V2_REQUIRED_NODES = ["104", "200", "201", "111", "15", "115", "92", "107"]


def _build_h3_r2v_v2(project_root: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="h3_r2v_v2",
        version="2.0.0",
        strategy="REFERENCE_TO_VIDEO",
        template_path=os.path.join(project_root, "workflows", "h3", "r2v_v2.json"),
        template_fingerprint=_H3_R2V_V2_FINGERPRINT,
        parameter_mappings=dict(_H3_R2V_V2_MAPPINGS),
        required_models=list(_H3_R2V_V1_REQUIRED_MODELS),
        required_nodes=list(_H3_R2V_V2_REQUIRED_NODES),
        capabilities=["reference_to_video", "audio_muxed", "multi_reference"],
        constraints={
            "max_reference_images": 9,
            "materialized_reference_slots": 2,
            "min_duration_sec": 0.21,
            "max_duration_sec": 15.08,
            "fps": 24,
            "frame_grid": "17k+5",
            "seed_max": 0xFFFFFFFFFFFFFFFF,
        },
    )


# ---------------------------------------------------------------------------
# H3 FLF v1 definition — first-frame continuity (M7.B)
# ---------------------------------------------------------------------------

_H3_FLF_V1_FINGERPRINT = (
    "47d6706c93865d43213a8c1bdf46b4d07a1665155cfae6a7721239b5d42c43d6"
)

_H3_FLF_V1_MAPPINGS: dict[str, dict[str, str]] = {
    "prompt": {"node_id": "104", "field": "prompt"},
    "first_frame": {"node_id": "300", "field": "image"},
    "duration": {"node_id": "111", "field": "value"},
    "seed": {"node_id": "15", "field": "noise_seed"},
    "aspect": {"node_id": "115", "field": "aspect_ratio"},
    "output_prefix": {"node_id": "92", "field": "filename_prefix"},
}

_H3_FLF_V1_REQUIRED_NODES = ["104", "300", "111", "15", "115", "92", "107"]

_H3_FLF_V1_REQUIRED_MODELS = [
    "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "minimax_h3_video_vae_fp16.safetensors",
    "minimax_h3_audio_vae_fp32.safetensors",
]


def _build_h3_flf_v1(project_root: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="h3_flf_v1",
        version="1.0.0",
        strategy="FIRST_LAST_FRAME",
        template_path=os.path.join(project_root, "workflows", "h3", "flf_v1.json"),
        template_fingerprint=_H3_FLF_V1_FINGERPRINT,
        parameter_mappings=dict(_H3_FLF_V1_MAPPINGS),
        required_models=list(_H3_FLF_V1_REQUIRED_MODELS),
        required_nodes=list(_H3_FLF_V1_REQUIRED_NODES),
        capabilities=["first_last_frame", "audio_muxed", "continuity"],
        constraints={
            "materialized_reference_slots": 0,
            "continuity_frame_slots": 1,
            "min_duration_sec": 0.21,
            "max_duration_sec": 15.08,
            "fps": 24,
            "frame_grid": "17k+5",
            "seed_max": 0xFFFFFFFFFFFFFFFF,
        },
    )


# ---------------------------------------------------------------------------
# H3 R2V Image Pack v1 — 4-slot multi-reference (M7.G.C)
# ---------------------------------------------------------------------------

_H3_R2V_IMAGE_PACK_V1_FINGERPRINT = (
    "32caca08d5f4bd0b4578efc4f709024a7d222dd933f9224d9e718bc20f4a7351"
)

_H3_R2V_IMAGE_PACK_V1_MAPPINGS: dict[str, dict[str, str]] = {
    "prompt": {"node_id": "104", "field": "prompt"},
    "ref_image_0": {"node_id": "200", "field": "image"},
    "ref_image_1": {"node_id": "201", "field": "image"},
    "ref_image_2": {"node_id": "202", "field": "image"},
    "ref_image_3": {"node_id": "203", "field": "image"},
    "duration": {"node_id": "111", "field": "value"},
    "seed": {"node_id": "15", "field": "noise_seed"},
    "aspect": {"node_id": "115", "field": "aspect_ratio"},
    "output_prefix": {"node_id": "92", "field": "filename_prefix"},
}

_H3_R2V_IMAGE_PACK_V1_REQUIRED_NODES = [
    "104", "200", "201", "202", "203", "111", "15", "115", "92", "107",
]


def _build_h3_r2v_image_pack_v1(project_root: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="h3_r2v_image_pack_v1",
        version="1.0.0",
        strategy="REFERENCE_TO_VIDEO",
        template_path=os.path.join(project_root, "workflows", "h3", "r2v_image_pack_v1.json"),
        template_fingerprint=_H3_R2V_IMAGE_PACK_V1_FINGERPRINT,
        parameter_mappings=dict(_H3_R2V_IMAGE_PACK_V1_MAPPINGS),
        required_models=list(_H3_R2V_V1_REQUIRED_MODELS),
        required_nodes=list(_H3_R2V_IMAGE_PACK_V1_REQUIRED_NODES),
        capabilities=["reference_to_video", "audio_muxed", "multi_reference", "image_pack"],
        constraints={
            "max_reference_images": 9,
            "materialized_reference_slots": 4,
            "min_duration_sec": 0.21,
            "max_duration_sec": 15.08,
            "fps": 24,
            "frame_grid": "17k+5",
            "seed_max": 0xFFFFFFFFFFFFFFFF,
        },
    )


# ---------------------------------------------------------------------------
# WorkflowResolver
# ---------------------------------------------------------------------------

class WorkflowResolver:
    """Resolves generic strategies to versioned WorkflowDefinitions."""

    def __init__(self, project_root: str) -> None:
        self._project_root = project_root
        self._v1 = _build_h3_r2v_v1(project_root)
        self._v2 = _build_h3_r2v_v2(project_root)
        self._flf_v1 = _build_h3_flf_v1(project_root)
        self._image_pack_v1 = _build_h3_r2v_image_pack_v1(project_root)
        self._definitions: dict[str, WorkflowDefinition] = {
            "REFERENCE_TO_VIDEO": self._v1,  # default: 1-ref
        }

    def resolve(self, strategy: str) -> WorkflowDefinition:
        defn = self._definitions.get(strategy)
        if defn is None:
            raise UnsupportedStrategyError(
                f"Strategy {strategy!r} not supported",
                detail=f"strategy={strategy}",
            )
        return defn

    def resolve_for_reference_count(self, reference_count: int) -> WorkflowDefinition:
        """Select R2V workflow version based on reference count."""
        if reference_count == 1:
            return self._v1
        elif reference_count == 2:
            return self._v2
        elif reference_count == 0:
            from film_director.errors import ReferenceResolutionError
            raise ReferenceResolutionError(
                "No references provided for REFERENCE_TO_VIDEO",
            )
        else:
            from film_director.errors import ParameterResolutionError
            raise ParameterResolutionError(
                f"Reference count {reference_count} exceeds maximum materialized slots (2)",
                detail=f"reference_count={reference_count}, max_slots=2",
            )

    def resolve_image_pack(self) -> WorkflowDefinition:
        """Return the 4-slot image-pack R2V workflow (M7.G.C)."""
        return self._image_pack_v1

    def resolve_for_continuity(
        self, has_continuity_frame: bool, reference_count: int,
    ) -> WorkflowDefinition:
        """Select workflow based on continuity frame presence.

        - has_continuity_frame=True → h3_flf_v1 (first-frame continuity, no ref_images)
        - has_continuity_frame=False → r2v_v1 or r2v_v2 based on reference_count
        """
        if has_continuity_frame:
            return self._flf_v1
        return self.resolve_for_reference_count(reference_count)

    def load_template(self, definition: WorkflowDefinition) -> dict:
        path = definition.template_path
        if not os.path.isfile(path):
            raise WorkflowTemplateError(
                f"Template not found: {path}",
                detail=f"path={path}",
            )

        with open(path, "rb") as f:
            raw = f.read()

        actual_fp = hashlib.sha256(raw).hexdigest()
        if actual_fp != definition.template_fingerprint:
            raise WorkflowTemplateError(
                f"Template fingerprint mismatch: expected {definition.template_fingerprint}, "
                f"got {actual_fp}",
                detail=f"expected={definition.template_fingerprint}, actual={actual_fp}",
            )

        try:
            template = json.loads(raw)
        except json.JSONDecodeError as e:
            raise WorkflowTemplateError(
                f"Template is not valid JSON: {e}",
                detail=f"path={path}",
            ) from e

        if not isinstance(template, dict):
            raise WorkflowTemplateError(
                "Template must be a JSON object",
                detail=f"type={type(template).__name__}",
            )

        for nid in definition.required_nodes:
            if nid not in template:
                raise WorkflowTemplateError(
                    f"Required node {nid!r} missing from template",
                    detail=f"node_id={nid}",
                )
            node = template[nid]
            if not isinstance(node, dict):
                raise WorkflowTemplateError(
                    f"Node {nid!r} is not a dict",
                )
            if "class_type" not in node:
                raise WorkflowTemplateError(
                    f"Node {nid!r} missing class_type",
                )

        return template

    def compute_fingerprint(self, path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_SHA256_BUF)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
