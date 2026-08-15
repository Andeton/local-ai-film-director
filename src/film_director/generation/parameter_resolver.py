"""ParameterResolver — seed, duration, aspect, and injection building.

Builds ONE authoritative list[WorkflowInjection] that drives BOTH
workflow JSON mutation AND future GenerationRequest.parameters_snapshot.
"""
from __future__ import annotations

import copy
import secrets
from typing import TYPE_CHECKING

from film_director.errors import ParameterResolutionError, WorkflowTemplateError
from film_director.generation.h3_types import WorkflowInjection

if TYPE_CHECKING:
    from film_director.generation.h3_prompt import H3PromptV1
    from film_director.generation.h3_types import H3ReferenceBinding
    from film_director.generation.workflow_registry import WorkflowDefinition
    from film_director.models.canonical import GenerationPlan, ShotSpecificationV1

# Verified H3 aspect mapping from M3.A /object_info (section 8)
_ASPECT_MAP: dict[str, str] = {
    "1:1": "1:1 (Square)",
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "3:4": "3:4 (Portrait Standard)",
    "4:3": "4:3 (Standard)",
    "9:16": "9:16 (Portrait Widescreen)",
    "16:9": "16:9 (Widescreen)",
    "21:9": "21:9 (Ultrawide)",
}

# M3.A verified: trained frame range 124-362 at 24fps
# Validation uses the exact workflow grid expression to compute frames,
# then enforces the trained range.  Node 111 still receives seconds.
_TRAINED_MIN_FRAMES = 124
_TRAINED_MAX_FRAMES = 362

# M3.A verified: RandomNoise.noise_seed uint64
_SEED_MAX = 0xFFFFFFFFFFFFFFFF

_DEFAULT_ASPECT = "16:9"


def _seconds_to_grid_frames(seconds: float) -> int:
    """Apply the exact H3 workflow grid expression (from template node 107).

    Expression: max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17
    Snaps to the nearest 17k+5 grid point at or above round(seconds * 24).
    """
    raw = max(5, round(seconds * 24))
    return raw + (5 - (raw % 17)) % 17


class ParameterResolver:
    """Resolves generic GenerationPlan parameters to concrete H3 injections."""

    def resolve_seed(self, plan: GenerationPlan, take_number: int) -> int:
        if plan.seed_policy == "fixed":
            if plan.seed is None:
                raise ParameterResolutionError(
                    "Fixed seed policy requires plan.seed",
                    detail="seed_policy=fixed, seed=None",
                )
            return plan.seed
        # random or vary_per_take: generate a new seed
        return secrets.randbelow(_SEED_MAX + 1)

    def resolve_duration(
        self, duration_sec: float, workflow_def: WorkflowDefinition
    ) -> float:
        frames = _seconds_to_grid_frames(duration_sec)
        if frames < _TRAINED_MIN_FRAMES or frames > _TRAINED_MAX_FRAMES:
            raise ParameterResolutionError(
                f"Duration {duration_sec}s resolves to {frames} frames, "
                f"outside trained range [{_TRAINED_MIN_FRAMES}, {_TRAINED_MAX_FRAMES}]",
                detail=f"duration_sec={duration_sec}, frames={frames}",
            )
        return duration_sec

    def resolve_aspect(
        self, generic_aspect: str, workflow_def: WorkflowDefinition
    ) -> str:
        runtime = _ASPECT_MAP.get(generic_aspect)
        if runtime is None:
            raise ParameterResolutionError(
                f"Unsupported aspect ratio: {generic_aspect!r}",
                detail=f"supported={list(_ASPECT_MAP.keys())}",
            )
        return runtime

    def build_injections(
        self,
        plan: GenerationPlan,
        shot: ShotSpecificationV1,
        prompt: H3PromptV1,
        workflow_def: WorkflowDefinition,
        uploaded_bindings: list[H3ReferenceBinding],
        seed: int,
        output_prefix: str,
    ) -> list[WorkflowInjection]:
        if not output_prefix:
            raise ParameterResolutionError(
                "output_prefix must not be empty",
            )

        # Validate uploaded bindings
        materialized = workflow_def.constraints.get("materialized_reference_slots", 0)
        if len(uploaded_bindings) > materialized:
            raise ParameterResolutionError(
                f"Template has {materialized} materialized reference slot(s) "
                f"but {len(uploaded_bindings)} binding(s) provided",
                detail=f"materialized={materialized}, bindings={len(uploaded_bindings)}",
            )
        for b in uploaded_bindings:
            if not b.uploaded_filename:
                raise ParameterResolutionError(
                    f"Binding for subject {b.subject_index} has empty uploaded_filename",
                    detail=f"character_id={b.character_id}",
                )

        # Resolve parameters
        duration = self.resolve_duration(plan.duration_sec, workflow_def)
        generic_aspect = plan.resolution_intent.get("aspect", _DEFAULT_ASPECT)
        aspect = self.resolve_aspect(generic_aspect, workflow_def)

        mappings = workflow_def.parameter_mappings

        injections: list[WorkflowInjection] = []

        # 1. Prompt
        m = mappings["prompt"]
        injections.append(WorkflowInjection(
            name="prompt",
            node_id=m["node_id"],
            field=m["field"],
            value=prompt.rendered_prompt_text,
        ))

        # 2. References in binding order
        for i, b in enumerate(uploaded_bindings):
            ref_key = f"ref_image_{i}"
            m = mappings[ref_key]
            injections.append(WorkflowInjection(
                name=ref_key,
                node_id=m["node_id"],
                field=m["field"],
                value=b.uploaded_filename,
            ))

        # 3. Duration (seconds, not frames)
        m = mappings["duration"]
        injections.append(WorkflowInjection(
            name="duration",
            node_id=m["node_id"],
            field=m["field"],
            value=duration,
        ))

        # 4. Seed
        m = mappings["seed"]
        injections.append(WorkflowInjection(
            name="seed",
            node_id=m["node_id"],
            field=m["field"],
            value=seed,
        ))

        # 5. Aspect
        m = mappings["aspect"]
        injections.append(WorkflowInjection(
            name="aspect",
            node_id=m["node_id"],
            field=m["field"],
            value=aspect,
        ))

        # 6. Output prefix
        m = mappings["output_prefix"]
        injections.append(WorkflowInjection(
            name="output_prefix",
            node_id=m["node_id"],
            field=m["field"],
            value=output_prefix,
        ))

        return injections

    def apply_injections(
        self,
        template: dict,
        injections: list[WorkflowInjection],
    ) -> dict:
        result = copy.deepcopy(template)
        for inj in injections:
            node = result.get(inj.node_id)
            if node is None:
                raise WorkflowTemplateError(
                    f"Injection target node {inj.node_id!r} not found in template",
                    detail=f"injection={inj.name}",
                )
            inputs = node.get("inputs")
            if inputs is None:
                raise WorkflowTemplateError(
                    f"Node {inj.node_id!r} has no inputs section",
                    detail=f"injection={inj.name}",
                )
            inputs[inj.field] = inj.value
        return result
