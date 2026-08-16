"""ShotSpecBuilder — deterministic transformer from coverage to ShotSpecificationV1 (M2.D + M4.D).

ZERO LLM calls. Purely deterministic mapping.

M4.D adds source-fact precedence: when ShotSourceFacts are provided,
upstream facts override LLM/coverage defaults field-by-field.

Source precedence (M4.D frozen):
  EXPLICIT HUMAN CANONICAL EDIT (EnrichmentService responsibility)
  > EXPLICIT NORMALIZED SOURCE FACT
  > LLM / COVERAGE INFERENCE
  > FALLBACK / DEFAULT
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
from film_director.models.source_facts import DialogueIntent, ShotSourceFacts
from film_director.models.wind_comic_dto import WCStoryboardShot
from film_director.enrichment.coverage_planner import CoverageDecision


# ---------------------------------------------------------------------------
# Sentinel values — exact WC template placeholders treated as "not meaningful"
# ---------------------------------------------------------------------------

_WC_TEMPLATE_SENTINELS = frozenset({"动作", "情绪"})


def _is_meaningful(value: str | None) -> bool:
    """Return True if value is non-empty, non-whitespace, non-sentinel."""
    if not value or not value.strip():
        return False
    return value.strip() not in _WC_TEMPLATE_SENTINELS


class ShotSpecBuilder:
    """Builds ShotSpecificationV1 instances from coverage decisions.

    No LLM, no DB — pure deterministic transformation.

    When source_facts are provided (M4.D), upstream pre-production facts
    take precedence over coverage/beat defaults on a per-field basis.
    Source facts are looked up by explicit wc_shot_number key, NOT by
    array index.
    """

    def build_shots(
        self,
        beat: Beat,
        coverage: list[CoverageDecision],
        storyboard_shots: list[WCStoryboardShot],
        characters: list[CharacterReference],
        scene: Scene,
        order_start: int = 0,
        source_facts: dict[int, ShotSourceFacts] | None = None,
    ) -> list[ShotSpecificationV1]:
        beat_subjects = self._resolve_characters(beat.characters, characters)
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

            # --- M4.D: source-fact precedence (by explicit wc_shot_number key) ---
            sf = None
            if source_facts and wc_shot_number is not None:
                sf = source_facts.get(wc_shot_number)

            # Action: source fact > beat.dramatic_action
            action = beat.dramatic_action
            if sf is not None and _is_meaningful(sf.action):
                action = sf.action  # type: ignore[assignment]

            # Dramatic purpose: source emotion > coverage.purpose
            dramatic_purpose = cov.purpose
            if sf is not None and _is_meaningful(sf.emotion):
                dramatic_purpose = sf.emotion  # type: ignore[assignment]

            # Duration: source fact > storyboard > coverage
            if sf is not None and sf.duration_sec is not None:
                duration = sf.duration_sec

            # Camera: partial merge (source subfield > coverage subfield)
            cam_angle = cov.angle
            cam_movement = cov.movement
            if sf is not None:
                if sf.camera_angle is not None:
                    cam_angle = sf.camera_angle
                if sf.camera_movement is not None:
                    cam_movement = sf.camera_movement

            # Lighting: source > {}
            lighting: dict = {}
            if sf is not None and _is_meaningful(sf.lighting):
                lighting = {"source": sf.lighting}

            # Audio intent: dialogue from source facts
            audio_intent: dict = {}
            if sf is not None and _is_meaningful(sf.dialogue_text):
                dialogue_emotion = sf.emotion if _is_meaningful(sf.emotion) else ""
                speaker_id, speaker_name = self._resolve_speaker(
                    sf.characters, characters,
                )
                di = DialogueIntent(
                    text=sf.dialogue_text,  # type: ignore[arg-type]
                    speaker_character_id=speaker_id,
                    speaker_name=speaker_name,
                    emotion=dialogue_emotion,
                )
                audio_intent["dialogue"] = di.model_dump()

            # Environment: scene location/description + storyboard_description
            environment: dict = {
                "location": scene.location,
                "description": scene.description,
            }
            if sf is not None and sf.storyboard_description:
                environment["storyboard_description"] = sf.storyboard_description

            # Subjects: source characters (if all resolve) > beat characters
            subjects = self._resolve_subjects_with_source(
                sf, beat, characters,
            ) if sf is not None else list(beat_subjects)

            shot_id = f"shot{uuid.uuid4().hex[:12]}"
            shots.append(
                ShotSpecificationV1(
                    id=shot_id,
                    beat_id=beat.id,
                    wc_storyboard_id=wc_storyboard_id,
                    wc_shot_number=wc_shot_number,
                    dramatic_purpose=dramatic_purpose,
                    subjects=subjects,
                    action=action,
                    environment=environment,
                    camera=CameraIntent(
                        shot_size=cov.shot_size,
                        angle=cam_angle,
                        movement=cam_movement,
                    ),
                    lighting=lighting,
                    audio_intent=audio_intent,
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
    def _resolve_speaker(
        source_characters: list[str],
        char_refs: list[CharacterReference],
    ) -> tuple[str | None, str | None]:
        """Resolve dialogue speaker from source characters.

        Returns (speaker_character_id, speaker_name) or (None, None).

        Speaker is resolved ONLY when:
        1. source_characters has exactly ONE entry
        2. That name resolves uniquely (casefold+strip) to one canonical
           CharacterReference in the provided list (project-scoped by caller)

        Otherwise both fields are None. NEVER parses dialogue text.
        """
        if len(source_characters) != 1:
            return None, None

        name = source_characters[0]
        key = name.strip().casefold()

        # Build lookup
        ref_lookup: dict[str, list[CharacterReference]] = {}
        for cr in char_refs:
            cr_key = cr.name.strip().casefold()
            ref_lookup.setdefault(cr_key, []).append(cr)

        matches = ref_lookup.get(key, [])
        if len(matches) != 1:
            return None, None

        return matches[0].id, name

    def _resolve_subjects_with_source(
        self,
        sf: ShotSourceFacts,
        beat: Beat,
        char_refs: list[CharacterReference],
    ) -> list[ShotSubject]:
        """Resolve subjects with source-fact precedence.

        If source characters are non-empty AND all resolve uniquely,
        use source-resolved subjects. Otherwise fall back to beat characters.
        """
        if not sf.characters:
            # Empty source characters → use beat characters
            return list(self._resolve_characters(beat.characters, char_refs))

        # Try resolving all source characters
        source_subjects = self._resolve_characters(sf.characters, char_refs)

        # Check if ALL non-empty source character names resolved uniquely
        # Build expected count: deduplicated source names
        seen: set[str] = set()
        unique_count = 0
        for name in sf.characters:
            key = name.strip().casefold()
            if key not in seen:
                seen.add(key)
                unique_count += 1

        if len(source_subjects) == unique_count:
            # All resolved → use source subjects
            return source_subjects

        # Partial resolution → fall back to beat characters
        return list(self._resolve_characters(beat.characters, char_refs))

    @staticmethod
    def _resolve_image_path(sb: WCStoryboardShot | None) -> str | None:
        if sb is None:
            return None
        if sb.persistent_url:
            return sb.persistent_url
        if sb.media_urls:
            return sb.media_urls[0]
        return None
