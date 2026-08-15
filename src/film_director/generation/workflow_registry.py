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
# WorkflowResolver
# ---------------------------------------------------------------------------

class WorkflowResolver:
    """Resolves generic strategies to versioned WorkflowDefinitions."""

    def __init__(self, project_root: str) -> None:
        self._project_root = project_root
        self._definitions: dict[str, WorkflowDefinition] = {
            "REFERENCE_TO_VIDEO": _build_h3_r2v_v1(project_root),
        }

    def resolve(self, strategy: str) -> WorkflowDefinition:
        defn = self._definitions.get(strategy)
        if defn is None:
            raise UnsupportedStrategyError(
                f"Strategy {strategy!r} not supported in M3",
                detail=f"strategy={strategy}",
            )
        return defn

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
