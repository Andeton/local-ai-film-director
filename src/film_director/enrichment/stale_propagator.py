"""Stale propagation — transactional cascade marking downstream M2 artifacts OUTDATED.

No regeneration, no LLM calls, no deletion, no API changes.
Only marks status → 'outdated' on Beats, Shots, and GenerationPlans.
"""
from __future__ import annotations

import logging
import sqlite3

from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    GenerationPlanRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)

logger = logging.getLogger(__name__)


class StalePropagator:
    """Cascade-marks downstream M2 artifacts OUTDATED when upstream entities change."""

    def __init__(
        self,
        db: Database,
        beat_repo: BeatRepository,
        shot_repo: ShotRepository,
        plan_repo: GenerationPlanRepository,
        sequence_repo: SequenceRepository,
        scene_repo: SceneRepository,
    ) -> None:
        self._db = db
        self._beat_repo = beat_repo
        self._shot_repo = shot_repo
        self._plan_repo = plan_repo
        self._sequence_repo = sequence_repo
        self._scene_repo = scene_repo

    # ------------------------------------------------------------------
    # Public methods — all return count of newly-outdated rows
    # ------------------------------------------------------------------

    def propagate_scene_stale(self, scene_id: str, conn: sqlite3.Connection | None = None) -> int:
        """Mark current Beats for scene → OUTDATED, their Shots → OUTDATED, their Plans → OUTDATED.

        Does NOT change the Scene itself (M1 owns).
        """
        if conn is not None:
            return self._do_scene_stale(scene_id, conn)
        with self._db.connection() as c:
            return self._do_scene_stale(scene_id, c)

    def propagate_character_stale(
        self, character_id: str, project_id: str, conn: sqlite3.Connection | None = None
    ) -> int:
        """Find current shots where any subject.character_id == character_id → OUTDATED, their Plans → OUTDATED.

        Does NOT mark Beats outdated.
        """
        if conn is not None:
            return self._do_character_stale(character_id, project_id, conn)
        with self._db.connection() as c:
            return self._do_character_stale(character_id, project_id, c)

    def propagate_beat_stale(self, beat_id: str, conn: sqlite3.Connection | None = None) -> int:
        """Current Shots for this Beat → OUTDATED, their Plans → OUTDATED.

        Does NOT mark the Beat itself.
        """
        if conn is not None:
            return self._do_beat_stale(beat_id, conn)
        with self._db.connection() as c:
            return self._do_beat_stale(beat_id, c)

    def propagate_shot_stale(self, shot_id: str, conn: sqlite3.Connection | None = None) -> int:
        """Current Plan(s) for this Shot → OUTDATED.

        Does NOT mark the Shot itself.
        """
        if conn is not None:
            return self._do_shot_stale(shot_id, conn)
        with self._db.connection() as c:
            return self._do_shot_stale(shot_id, c)

    def propagate_project_stale(self, project_id: str, conn: sqlite3.Connection | None = None) -> int:
        """All current Beats across project→sequences→scenes → OUTDATED, their Shots → OUTDATED, their Plans → OUTDATED.

        Does NOT touch ProductionProject/Sequence/Scene/CharacterReference (M1 owns).
        """
        if conn is not None:
            return self._do_project_stale(project_id, conn)
        with self._db.connection() as c:
            return self._do_project_stale(project_id, c)

    # ------------------------------------------------------------------
    # Internal cascade implementations
    # ------------------------------------------------------------------

    def _do_scene_stale(self, scene_id: str, conn: sqlite3.Connection) -> int:
        count = 0
        # Get current (non-outdated) beats for this scene
        current_beats = self._beat_repo.get_current_beats_by_scene(scene_id, conn=conn)
        count += len(current_beats)

        # Get ALL beats (including already-outdated) to find all shots underneath
        all_beats = self._beat_repo.get_beats_by_scene(scene_id, conn=conn)

        # Mark all beats in scene as outdated (bulk)
        self._beat_repo.mark_beats_outdated_by_scene(scene_id, conn=conn)

        # For each beat, cascade to shots and plans
        for beat in all_beats:
            count += self._cascade_shots_for_beat(beat.id, conn)

        return count

    def _do_character_stale(self, character_id: str, project_id: str, conn: sqlite3.Connection) -> int:
        count = 0
        # Get all current shots in the project
        current_shots = self._shot_repo.get_current_shots_by_project(project_id, conn=conn)

        # Filter to shots that reference this character
        matching_shots = [
            shot for shot in current_shots
            if any(s.character_id == character_id for s in shot.subjects)
        ]

        for shot in matching_shots:
            # Mark shot outdated
            self._shot_repo.mark_outdated(shot.id, conn=conn)
            count += 1
            # Mark its plan(s) outdated
            count += self._cascade_plans_for_shot(shot.id, conn)

        return count

    def _do_beat_stale(self, beat_id: str, conn: sqlite3.Connection) -> int:
        return self._cascade_shots_for_beat(beat_id, conn)

    def _do_shot_stale(self, shot_id: str, conn: sqlite3.Connection) -> int:
        return self._cascade_plans_for_shot(shot_id, conn)

    def _do_project_stale(self, project_id: str, conn: sqlite3.Connection) -> int:
        count = 0
        sequences = self._sequence_repo.get_sequences_by_project(project_id, conn=conn)
        for seq in sequences:
            scenes = self._scene_repo.get_scenes_by_sequence(seq.id, conn=conn)
            for scene in scenes:
                count += self._do_scene_stale(scene.id, conn)
        return count

    # ------------------------------------------------------------------
    # Shared cascade helpers
    # ------------------------------------------------------------------

    def _cascade_shots_for_beat(self, beat_id: str, conn: sqlite3.Connection) -> int:
        """Mark current shots for a beat as outdated, and their plans."""
        count = 0
        current_shots = self._shot_repo.get_current_shots_by_beat(beat_id, conn=conn)
        count += len(current_shots)

        # Mark all shots for this beat outdated (bulk)
        self._shot_repo.mark_shots_outdated_by_beat(beat_id, conn=conn)

        # For each current shot, cascade to plans
        for shot in current_shots:
            count += self._cascade_plans_for_shot(shot.id, conn)

        return count

    def _cascade_plans_for_shot(self, shot_id: str, conn: sqlite3.Connection) -> int:
        """Mark current plan(s) for a shot as outdated."""
        plan = self._plan_repo.get_current_plan_by_shot(shot_id, conn=conn)
        if plan is not None:
            self._plan_repo.mark_outdated(plan.id, conn=conn)
            return 1
        return 0
