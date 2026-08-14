"""ShotSpecBuilder — deterministic transformer from coverage to ShotSpecificationV1 (M2.D).

ZERO LLM calls. Purely deterministic mapping.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from film_director.models.canonical import (
    Beat,
    CameraIntent,
    CharacterReference,
    Scene,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.wind_comic_dto import WCStoryboardShot
from film_director.enrichment.coverage_planner import CoverageDecision


class ShotSpecBuilder:
    """Builds ShotSpecificationV1 instances from coverage decisions.

    No LLM, no DB — pure deterministic transformation.
    """

    def build_shots(
        self,
        beat: Beat,
        coverage: list[CoverageDecision],
        storyboard_shots: list[WCStoryboardShot],
        characters: list[CharacterReference],
        scene: Scene,
        order_start: int = 0,
    ) -> list[ShotSpecificationV1]:
        subjects = self._resolve_characters(beat.characters, characters)
        now = datetime.now(timezone.utc).isoformat()

        shots: list[ShotSpecificationV1] = []
        for i, cov in enumerate(coverage):
            sb = storyboard_shots[i] if i < len(storyboard_shots) else None

            # Storyboard linkage
            wc_storyboard_id = sb.asset_id if sb else None
            wc_shot_number = sb.shot_number if sb else None
            storyboard_image_path = self._resolve_image_path(sb)

            # Duration: storyboard overrides coverage if positive
            duration = cov.duration_sec
            if sb is not None:
                sb_dur = sb.data.get("duration")
                if isinstance(sb_dur, (int, float)) and sb_dur > 0:
                    duration = float(sb_dur)

            shot_id = f"shot{uuid.uuid4().hex[:12]}"
            shots.append(
                ShotSpecificationV1(
                    id=shot_id,
                    beat_id=beat.id,
                    wc_storyboard_id=wc_storyboard_id,
                    wc_shot_number=wc_shot_number,
                    dramatic_purpose=cov.purpose,
                    subjects=list(subjects),
                    action=beat.dramatic_action,
                    environment={
                        "location": scene.location,
                        "description": scene.description,
                    },
                    camera=CameraIntent(
                        shot_size=cov.shot_size,
                        angle=cov.angle,
                        movement=cov.movement,
                    ),
                    lighting={},
                    audio_intent={},
                    duration_sec=duration,
                    continuity_inputs={},
                    storyboard_image_path=storyboard_image_path,
                    order_index=order_start + i,
                    status="draft",
                    source="generated",
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        return shots

    @staticmethod
    def _resolve_characters(
        beat_characters: list[str],
        char_refs: list[CharacterReference],
    ) -> list[ShotSubject]:
        """Resolve beat character names to ShotSubjects.

        - Deduplicate by casefold+strip (first occurrence wins)
        - Match against CharacterReference by casefold+strip
        - If multiple CharacterReference share the same casefolded name: ambiguous, omit
        - If no match: omit (no fake ID)
        """
        # Build lookup: casefolded name -> list of CharacterReference
        ref_lookup: dict[str, list[CharacterReference]] = {}
        for cr in char_refs:
            key = cr.name.strip().casefold()
            ref_lookup.setdefault(key, []).append(cr)

        seen: set[str] = set()
        subjects: list[ShotSubject] = []
        for name in beat_characters:
            key = name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)

            matches = ref_lookup.get(key, [])
            if len(matches) != 1:
                # 0 = unresolved, >1 = ambiguous — both omitted
                continue

            cr = matches[0]
            subjects.append(
                ShotSubject(
                    character_id=cr.id,
                    name=cr.name,
                    ref_images=list(cr.turnaround_paths),
                )
            )
        return subjects

    @staticmethod
    def _resolve_image_path(sb: WCStoryboardShot | None) -> str | None:
        if sb is None:
            return None
        if sb.persistent_url:
            return sb.persistent_url
        if sb.media_urls:
            return sb.media_urls[0]
        return None
