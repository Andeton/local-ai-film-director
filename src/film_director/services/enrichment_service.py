"""EnrichmentService — orchestrates M2 enrichment pipeline (M2.G).

Coordinates BeatEnricher, CoveragePlanner, ShotSpecBuilder, StrategySelector
to produce the full Beat → Shot → GenerationPlan graph for a project.

Critical design rules:
1. No DB transaction during LLM calls
2. Idempotent enrich_project (reuse current beats/shots/plans)
3. History preserving (mark old OUTDATED, never delete)
4. HumanEditConflictError for force=False with human content
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from film_director.enrichment.beat_enricher import BeatEnricher
from film_director.enrichment.coverage_planner import CoveragePlanner
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder
from film_director.enrichment.stale_propagator import StalePropagator
from film_director.enrichment.strategy_selector import (
    StrategySelector,
    build_selection_context,
)
from film_director.errors import HumanEditConflictError
from film_director.models.canonical import (
    Beat,
    GenerationPlan,
    ShotSpecificationV1,
)
from film_director.persistence.database import Database
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
)
from film_director.services.import_service import ChangeDetection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichmentResult:
    project_id: str
    beats_created: int
    shots_created: int
    plans_created: int


class EnrichmentService:
    """Orchestrates the full M2 enrichment pipeline."""

    def __init__(
        self,
        db: Database,
        project_repo: ProjectRepository,
        sequence_repo: SequenceRepository,
        scene_repo: SceneRepository,
        character_repo: CharacterRepository,
        beat_repo: BeatRepository,
        shot_repo: ShotRepository,
        plan_repo: GenerationPlanRepository,
        adapter,  # WindComicAdapter
        import_service,  # ImportService
        beat_enricher: BeatEnricher,
        coverage_planner: CoveragePlanner,
        shot_spec_builder: ShotSpecBuilder,
        strategy_selector: StrategySelector,
        stale_propagator: StalePropagator,
    ) -> None:
        self._db = db
        self._project_repo = project_repo
        self._sequence_repo = sequence_repo
        self._scene_repo = scene_repo
        self._character_repo = character_repo
        self._beat_repo = beat_repo
        self._shot_repo = shot_repo
        self._plan_repo = plan_repo
        self._adapter = adapter
        self._import_service = import_service
        self._beat_enricher = beat_enricher
        self._coverage_planner = coverage_planner
        self._shot_spec_builder = shot_spec_builder
        self._strategy_selector = strategy_selector
        self._stale_propagator = stale_propagator

    # ------------------------------------------------------------------
    # enrich_project — idempotent full-project enrichment
    # ------------------------------------------------------------------

    def enrich_project(self, project_id: str) -> EnrichmentResult:
        """Idempotent enrichment orchestrator.

        Phase 1 (reads + LLM, NO transaction):
          - For each scene: reuse current beats or generate new via LLM
          - For each beat: reuse current shots or plan coverage + build shots
          - For each shot: reuse current plan or select strategy

        Phase 2 (ONE write transaction):
          - Persist all new beats, shots, plans atomically
        """
        # --- Read project state ---
        project = self._project_repo.get_project(project_id)
        if project is None:
            return EnrichmentResult(project_id=project_id, beats_created=0,
                                    shots_created=0, plans_created=0)

        sequences = self._sequence_repo.get_sequences_by_project(project_id)
        characters = self._character_repo.get_characters_by_project(project_id)

        # Collect all scenes
        scenes = []
        for seq in sequences:
            scenes.extend(self._scene_repo.get_scenes_by_sequence(seq.id))

        # --- Phase 1: LLM work in memory ---
        new_beats: list[Beat] = []
        new_shots: list[ShotSpecificationV1] = []
        new_plans: list[GenerationPlan] = []

        for scene in scenes:
            # Reuse current beats if they exist
            current_beats = self._beat_repo.get_current_beats_by_scene(scene.id)
            if current_beats:
                beats_for_scene = current_beats
            else:
                # Read WC context BEFORE LLM call, outside any transaction
                wc_context = self._get_wc_scene_context(scene, project.wc_project_id)
                # LLM call — outside any transaction
                beats_for_scene = self._beat_enricher.enrich_scene(
                    scene, script_context=wc_context,
                )
                new_beats.extend(beats_for_scene)

            for beat in beats_for_scene:
                # Reuse current shots if they exist
                current_shots = self._shot_repo.get_current_shots_by_beat(beat.id)
                if current_shots:
                    shots_for_beat = current_shots
                else:
                    # LLM call — outside any transaction
                    coverage = self._coverage_planner.plan_coverage(beat, scene)
                    shots_for_beat = self._shot_spec_builder.build_shots(
                        beat=beat,
                        coverage=coverage,
                        storyboard_shots=[],  # No reliable scene linkage in WC data
                        characters=characters,
                        scene=scene,
                    )
                    new_shots.extend(shots_for_beat)

                for shot in shots_for_beat:
                    # Reuse current plan if version matches
                    current_plan = self._plan_repo.get_current_plan_by_shot(shot.id)
                    if current_plan is not None:
                        continue
                    # Deterministic — no LLM
                    ctx = build_selection_context(shot)
                    plan = self._strategy_selector.select_strategy(
                        ctx, shot, project.aspect,
                    )
                    new_plans.append(plan)

        # --- Phase 2: ONE write transaction ---
        if new_beats or new_shots or new_plans:
            with self._db.connection() as conn:
                for beat in new_beats:
                    self._beat_repo.save_beat(beat, conn=conn)
                for shot in new_shots:
                    self._shot_repo.save_shot(shot, conn=conn)
                for plan in new_plans:
                    self._plan_repo.save_plan(plan, conn=conn)

        return EnrichmentResult(
            project_id=project_id,
            beats_created=len(new_beats),
            shots_created=len(new_shots),
            plans_created=len(new_plans),
        )

    # ------------------------------------------------------------------
    # enrich_scene_beats — explicit re-enrichment
    # ------------------------------------------------------------------

    def enrich_scene_beats(self, scene_id: str, force: bool = False) -> list[Beat]:
        """Re-enrich a scene's beats.

        If current beats exist with source='human' and force=False,
        raises HumanEditConflictError.

        Otherwise: generate new beats via LLM, then ONE transaction:
        mark old beats+shots+plans outdated, save new beats.
        """
        scene = self._scene_repo.get_scene(scene_id)
        if scene is None:
            return []

        current_beats = self._beat_repo.get_current_beats_by_scene(scene_id)

        # Check for human-edited beats
        human_beats = [b for b in current_beats if b.source == "human"]
        if human_beats and not force:
            raise HumanEditConflictError(
                "Cannot re-enrich: human-edited beats exist",
                detail=f"scene_id={scene_id}, human_beat_ids={[b.id for b in human_beats]}",
            )

        # Resolve wc_project_id and read WC context BEFORE LLM call, outside transaction
        project_id = self._find_project_id_for_scene(scene)
        wc_context = None
        if project_id is not None:
            project = self._project_repo.get_project(project_id)
            if project is not None:
                wc_context = self._get_wc_scene_context(scene, project.wc_project_id)

        # LLM work — outside transaction
        new_beats = self._beat_enricher.enrich_scene(scene, script_context=wc_context)

        # ONE transaction: mark old outdated, save new
        with self._db.connection() as conn:
            # Mark old beats (and cascade to shots/plans) outdated
            for beat in current_beats:
                self._beat_repo.mark_outdated(beat.id, conn=conn)
                # Cascade: mark shots and plans for this beat outdated
                current_shots = self._shot_repo.get_current_shots_by_beat(beat.id, conn=conn)
                self._shot_repo.mark_shots_outdated_by_beat(beat.id, conn=conn)
                for shot in current_shots:
                    self._plan_repo.mark_plan_outdated_by_shot(shot.id, conn=conn)

            # Save new beats
            for beat in new_beats:
                self._beat_repo.save_beat(beat, conn=conn)

        return new_beats

    # ------------------------------------------------------------------
    # plan_beat_coverage — explicit re-plan
    # ------------------------------------------------------------------

    def plan_beat_coverage(self, beat_id: str, force: bool = False) -> list[ShotSpecificationV1]:
        """Re-plan coverage for a beat.

        If current shots exist with source='human' and force=False,
        raises HumanEditConflictError.
        """
        beat = self._beat_repo.get_beat(beat_id)
        if beat is None:
            return []

        scene = self._scene_repo.get_scene(beat.scene_id)
        if scene is None:
            return []

        project_id = self._find_project_id_for_scene(scene)
        characters = self._character_repo.get_characters_by_project(project_id) if project_id else []

        current_shots = self._shot_repo.get_current_shots_by_beat(beat_id)

        # Check for human-edited shots
        human_shots = [s for s in current_shots if s.source == "human"]
        if human_shots and not force:
            raise HumanEditConflictError(
                "Cannot re-plan: human-edited shots exist",
                detail=f"beat_id={beat_id}, human_shot_ids={[s.id for s in human_shots]}",
            )

        # LLM work — outside transaction
        coverage = self._coverage_planner.plan_coverage(beat, scene)
        new_shots = self._shot_spec_builder.build_shots(
            beat=beat,
            coverage=coverage,
            storyboard_shots=[],
            characters=characters,
            scene=scene,
        )

        # ONE transaction: mark old outdated, save new
        with self._db.connection() as conn:
            for shot in current_shots:
                self._shot_repo.mark_outdated(shot.id, conn=conn)
                self._plan_repo.mark_plan_outdated_by_shot(shot.id, conn=conn)

            for shot in new_shots:
                self._shot_repo.save_shot(shot, conn=conn)

        return new_shots

    # ------------------------------------------------------------------
    # assign_strategies — re-assign all generation plans
    # ------------------------------------------------------------------

    def assign_strategies(self, project_id: str) -> int:
        """For every current shot: mark current plan outdated, create new plan.

        ONE transaction. Returns count of plans created.
        """
        project = self._project_repo.get_project(project_id)
        if project is None:
            return 0

        current_shots = self._shot_repo.get_current_shots_by_project(project_id)

        # Build plans in memory (deterministic, no LLM)
        new_plans: list[GenerationPlan] = []
        old_plan_ids: list[str] = []
        for shot in current_shots:
            current_plan = self._plan_repo.get_current_plan_by_shot(shot.id)
            if current_plan is not None:
                old_plan_ids.append(current_plan.id)

            ctx = build_selection_context(shot)
            plan = self._strategy_selector.select_strategy(ctx, shot, project.aspect)
            new_plans.append(plan)

        # ONE transaction
        with self._db.connection() as conn:
            for plan_id in old_plan_ids:
                self._plan_repo.mark_outdated(plan_id, conn=conn)
            for plan in new_plans:
                self._plan_repo.save_plan(plan, conn=conn)

        return len(new_plans)

    # ------------------------------------------------------------------
    # apply_stale_cascade — maps ChangeDetection to StalePropagator
    # ------------------------------------------------------------------

    def apply_stale_cascade(
        self,
        project_id: str,
        changes: list[ChangeDetection],
        conn: sqlite3.Connection,
    ) -> int:
        """Map ChangeDetection items to StalePropagator calls.

        Uses the supplied conn (caller owns the transaction).
        """
        total = 0
        for change in changes:
            if change.change_type == "added":
                # Added entities → propagate project stale (production spec incomplete)
                total += self._stale_propagator.propagate_project_stale(
                    project_id, conn=conn,
                )
            elif change.entity_type == "project":
                total += self._stale_propagator.propagate_project_stale(
                    project_id, conn=conn,
                )
            elif change.entity_type == "scene" and change.entity_id:
                total += self._stale_propagator.propagate_scene_stale(
                    change.entity_id, conn=conn,
                )
            elif change.entity_type == "character" and change.entity_id:
                total += self._stale_propagator.propagate_character_stale(
                    change.entity_id, project_id, conn=conn,
                )
        return total

    # ------------------------------------------------------------------
    # apply_source_changes — atomic M1 + M2 change application
    # ------------------------------------------------------------------

    def apply_source_changes(
        self,
        project_id: str,
        changes: list[ChangeDetection],
    ) -> dict:
        """Apply M1 changes and M2 stale cascade in ONE transaction.

        Returns summary dict with m1_applied and m2_stale_count.
        """
        with self._db.connection() as conn:
            # M1: apply detected changes (mark entities outdated)
            self._import_service.apply_detected_changes(
                project_id, changes, conn=conn,
            )
            # M2: cascade stale marks to beats/shots/plans
            stale_count = self.apply_stale_cascade(project_id, changes, conn)

        return {
            "m1_applied": len(changes),
            "m2_stale_count": stale_count,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_project_id_for_scene(self, scene) -> str | None:
        """Walk scene -> sequence -> project to find the project_id."""
        # We need to find which project this scene belongs to.
        # scene.sequence_id -> find the sequence -> sequence.project_id
        # Since SequenceRepository doesn't have a get_sequence method,
        # we need to search through projects.
        # Simpler: look at all projects and their sequences
        projects = self._project_repo.list_projects()
        for project in projects:
            sequences = self._sequence_repo.get_sequences_by_project(project.id)
            for seq in sequences:
                if seq.id == scene.sequence_id:
                    return project.id
        return None

    def _get_wc_scene_context(self, scene, wc_project_id: str) -> dict | None:
        """Look up matching WC source scene data for BeatEnricher context.

        Matches by WCScene.asset_id == scene.wc_scene_id.
        Returns WCScene.data dict if found, None if not found.
        WC integration errors (Unavailable, Schema, Malformed) propagate unchanged.

        MUST be called outside any write transaction.
        Does NOT mutate WCScene.data or the canonical Scene.
        """
        if self._adapter is None or scene.wc_scene_id is None:
            return None
        wc_scenes = self._adapter.get_scenes(wc_project_id)
        for wc_scene in wc_scenes:
            if wc_scene.asset_id == scene.wc_scene_id:
                return wc_scene.data
        return None
