"""Live M3 H3 R2V vertical-slice acceptance test.

Requires:
- Running ComfyUI 0.33.1 with H3 models
- Real canonical data (imported via WC or test fixtures)
- FFmpeg installed

Run with: pytest -m live tests/integration/test_comfyui_live.py
"""
import json
import logging
import os
import subprocess

import pytest

from film_director.generation.comfyui_adapter import ComfyUIAdapter
from film_director.generation.generation_service import GenerationService
from film_director.generation.workflow_registry import WorkflowResolver
from film_director.persistence.database import Database
from film_director.models.canonical import (
    Beat,
    CameraIntent,
    CharacterReference,
    GenerationPlan,
    ProductionProject,
    ReferenceRequirements,
    Scene,
    Sequence,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.persistence.repositories import (
    BeatRepository,
    CharacterRepository,
    GenerationPlanRepository,
    GenerationRequestRepository,
    ProjectRepository,
    SceneRepository,
    SequenceRepository,
    ShotRepository,
    TakeRepository,
)

logger = logging.getLogger(__name__)

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_eligible_shot(db: Database) -> tuple[str, str] | None:
    """Deterministic candidate selection for live M3 acceptance.

    Returns (shot_id, project_id) or None if no eligible shot exists.

    Selection policy:
    1. Current shots with current R2V plans
    2. At least one subject with valid local ref_images[0]
    3. Exactly 1 subject (materialized template capacity)
    4. Ordered by shot.id for determinism
    5. First eligible
    """
    shot_repo = ShotRepository(db)
    plan_repo = GenerationPlanRepository(db)

    with db.connection() as conn:
        rows = conn.execute("""
            SELECT s.id as shot_id, seq.project_id
            FROM shots s
            JOIN beats b ON s.beat_id = b.id
            JOIN scenes sc ON b.scene_id = sc.id
            JOIN sequences seq ON sc.sequence_id = seq.id
            JOIN generation_plans gp ON gp.shot_id = s.id
            WHERE s.status != 'outdated'
              AND b.status != 'outdated'
              AND gp.status != 'outdated'
              AND gp.strategy = 'REFERENCE_TO_VIDEO'
              AND gp.shot_version = s.version
            ORDER BY s.id
        """).fetchall()

    for row in rows:
        shot = shot_repo.get_shot(row["shot_id"])
        if shot is None or not shot.subjects:
            continue
        # M3 template has 1 materialized slot
        if len(shot.subjects) > 1:
            continue
        subj = shot.subjects[0]
        if not subj.ref_images:
            continue
        ref_path = subj.ref_images[0]
        if os.path.isfile(ref_path):
            return row["shot_id"], row["project_id"]

    return None


@pytest.mark.live
class TestM3LiveVerticalSlice:
    """Live end-to-end M3 H3 R2V generation through GenerationService."""

    def _create_live_fixture(self, db, settings):
        """Create minimal canonical data for M3 live acceptance.

        Creates one project/sequence/scene/beat/shot/character/plan with
        a real reference image for R2V generation.
        """
        # Create a reference image if needed
        ref_dir = os.path.join(settings.storage_root, "refs")
        os.makedirs(ref_dir, exist_ok=True)
        ref_path = os.path.join(ref_dir, "m3_live_ref.png")
        if not os.path.isfile(ref_path):
            # Generate a simple 768x768 reference image with FFmpeg
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "color=c=0x4488CC:s=768x768:d=0.04:r=1",
                "-frames:v", "1", ref_path,
            ]
            subprocess.run(cmd, capture_output=True, timeout=10)
            if not os.path.isfile(ref_path):
                pytest.skip("Cannot create reference image")

        prov = Provenance(
            source_system="m3_live_test", source_project_id="live",
            source_asset_id="fixture", source_asset_version=1,
            imported_at="2026-08-16T00:00:00", source_hash="live_fixture",
        )
        with db.connection() as conn:
            ProjectRepository(db).save_project(ProductionProject(
                id="proj-live", wc_project_id="wc-live", title="M3 Live Test",
                status="active", created_at="2026-08-16", updated_at="2026-08-16",
                provenance=prov,
            ), conn=conn)
            SequenceRepository(db).save_sequence(Sequence(
                id="seq-live", project_id="proj-live", name="Live Sequence", order_index=0,
            ), conn=conn)
            SceneRepository(db).save_scene(Scene(
                id="scene-live", sequence_id="seq-live", wc_scene_id="wc-scene-live",
                name="Live Scene", location="abandoned building", description="A dark corridor",
                order_index=0, provenance=prov,
            ), conn=conn)
            BeatRepository(db).save_beat(Beat(
                id="beat-live", scene_id="scene-live",
                dramatic_action="enters the corridor",
                character_intention="investigate", change="discovers evidence",
                order_index=0, created_at="2026-08-16", updated_at="2026-08-16",
            ), conn=conn)
            CharacterRepository(db).save_character(CharacterReference(
                id="char-live", project_id="proj-live", wc_character_id="wc-char-live",
                name="Detective", description="A seasoned detective in a trench coat",
                appearance="middle-aged man, dark trench coat, short grey hair, determined expression",
                provenance=prov,
            ), conn=conn)
            ShotRepository(db).save_shot(ShotSpecificationV1(
                id="shot-live", beat_id="beat-live",
                dramatic_purpose="building tension as detective enters unknown space",
                subjects=[ShotSubject(
                    character_id="char-live", name="Detective",
                    ref_images=[ref_path],
                )],
                action="walks cautiously into the dimly lit corridor, looking around",
                camera=CameraIntent(shot_size="medium", angle="eye level", movement="slow push"),
                lighting={"key": "low key", "mood": "noir"},
                audio_intent={"ambient": "distant echoes, water dripping"},
                duration_sec=5.0,
                order_index=0, version=1,
                created_at="2026-08-16", updated_at="2026-08-16",
            ), conn=conn)
            GenerationPlanRepository(db).save_plan(GenerationPlan(
                id="plan-live", shot_id="shot-live", shot_version=1,
                strategy="REFERENCE_TO_VIDEO",
                reference_requirements=ReferenceRequirements(character_refs=True),
                duration_sec=5.0, resolution_intent={"aspect": "16:9"},
                seed_policy="fixed", seed=42,
                created_at="2026-08-16", updated_at="2026-08-16",
            ), conn=conn)
        logger.info("Created M3 live acceptance fixture with ref at %s", ref_path)

    def test_real_h3_generation(self):
        """BLOCKING M3 EXIT: Real Shot → H3 R2V → Take via application path."""
        from film_director.config import Settings

        # Construct settings — locate WC DB in main repo or worktree
        main_repo = os.path.dirname(os.path.dirname(WORKTREE))
        wc_candidates = [
            os.path.join(WORKTREE, "experiments", "wind-comic", "data", "qfmj.db"),
            os.path.join(main_repo, "experiments", "wind-comic", "data", "qfmj.db"),
        ]
        wc_db = None
        for c in wc_candidates:
            if os.path.isfile(c):
                wc_db = c
                break
        if wc_db is None:
            pytest.skip(f"WC database not found in {wc_candidates}")

        settings = Settings(
            wc_database_path=wc_db,
            database_path=os.path.join(WORKTREE, "data", "production.db"),
            storage_root=os.path.join(WORKTREE, "storage"),
        )
        db = Database(settings.database_path)
        db.init_schema()

        # Ensure we have at least one eligible shot for R2V
        # First try importing from WC + enrichment
        with db.connection() as conn:
            existing = conn.execute("SELECT COUNT(*) as cnt FROM shots").fetchone()
        if existing["cnt"] == 0:
            logger.info("No shots found — creating M3 live acceptance fixture")
            self._create_live_fixture(db, settings)

        # Find eligible candidate
        candidate = _find_eligible_shot(db)
        if candidate is None:
            pytest.skip("No eligible R2V shot with valid reference found")
        shot_id, project_id = candidate

        shot = ShotRepository(db).get_shot(shot_id)
        plan = GenerationPlanRepository(db).get_current_plan_by_shot(shot_id)
        chars = CharacterRepository(db).get_characters_by_project(project_id)

        logger.info("=== M3 LIVE CANDIDATE ===")
        logger.info("Project: %s", project_id)
        logger.info("Shot: %s (version %d)", shot.id, shot.version)
        logger.info("Plan: %s (strategy=%s, seed_policy=%s, seed=%s)",
                     plan.id, plan.strategy, plan.seed_policy, plan.seed)
        logger.info("Subjects: %d", len(shot.subjects))
        for s in shot.subjects:
            logger.info("  %s (char_id=%s, refs=%s)", s.name, s.character_id, s.ref_images[:1])
        logger.info("Duration: %ss", plan.duration_sec)
        logger.info("Characters available: %d", len(chars))

        # Verify ComfyUI is reachable
        comfyui = ComfyUIAdapter(base_url=settings.comfyui_base_url)
        health = comfyui.health()
        logger.info("ComfyUI: %s", health.get("system", {}).get("python_version", "?"))

        # Execute generation through application path
        service = GenerationService(
            db=db,
            comfyui=comfyui,
            storage_root=settings.storage_root,
            project_root=WORKTREE,
            generation_timeout=settings.comfyui_generation_timeout,
        )

        take = service.generate_shot(shot_id)

        # --- Verify GenerationRequest ---
        req_repo = GenerationRequestRepository(db)
        req = req_repo.get_request(take.generation_request_id)
        assert req is not None
        assert req.status == "succeeded"
        assert req.shot_id == shot_id
        assert req.shot_version == shot.version
        assert req.generation_plan_id == plan.id
        assert req.generation_plan_version == plan.version
        assert req.workflow_definition_id == "h3_r2v_v1"
        assert req.workflow_template_fingerprint == "3893eb4ab9738c33953c016e6ae349f2a9d1e5414c0776c26f222743417206b4"
        assert req.comfyui_prompt_id  # non-empty
        assert req.seed is not None
        assert len(req.parameters_snapshot) > 0
        assert len(req.reference_snapshot) > 0
        # Reference snapshot has SHA
        assert len(req.reference_snapshot[0].get("content_sha256", "")) == 64
        assert req.reference_snapshot[0].get("uploaded_filename", "") != ""

        logger.info("=== GENERATION REQUEST ===")
        logger.info("Request ID: %s", req.id)
        logger.info("ComfyUI prompt: %s", req.comfyui_prompt_id)
        logger.info("Seed: %s", req.seed)
        logger.info("Workflow: %s v%s", req.workflow_definition_id, req.workflow_definition_version)
        logger.info("Fingerprint: %s", req.workflow_template_fingerprint)
        logger.info("Params snapshot entries: %d", len(req.parameters_snapshot))
        logger.info("Ref snapshot entries: %d", len(req.reference_snapshot))

        # --- Verify Take ---
        assert take.status == "succeeded"
        assert take.shot_id == shot_id
        assert take.seed == req.seed
        assert take.video_path is not None
        assert os.path.isfile(take.video_path)
        assert os.path.getsize(take.video_path) > 0
        assert take.last_frame_path is not None
        assert os.path.isfile(take.last_frame_path)
        assert os.path.getsize(take.last_frame_path) > 0

        logger.info("=== TAKE ===")
        logger.info("Take ID: %s", take.id)
        logger.info("Video: %s (%d bytes)", take.video_path, os.path.getsize(take.video_path))
        logger.info("Last frame: %s (%d bytes)", take.last_frame_path, os.path.getsize(take.last_frame_path))

        # --- FFprobe verification ---
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", take.video_path,
        ]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        assert probe.returncode == 0
        info = json.loads(probe.stdout)
        streams = info.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        assert len(video_streams) >= 1

        vs = video_streams[0]
        fmt = info.get("format", {})
        logger.info("=== FFPROBE ===")
        logger.info("Container: %s", fmt.get("format_name", "?"))
        logger.info("Video codec: %s", vs.get("codec_name", "?"))
        logger.info("Resolution: %sx%s", vs.get("width", "?"), vs.get("height", "?"))
        logger.info("FPS: %s", vs.get("r_frame_rate", "?"))
        logger.info("Duration: %ss", fmt.get("duration", "?"))
        if audio_streams:
            logger.info("Audio: %s @ %sHz", audio_streams[0].get("codec_name", "?"),
                        audio_streams[0].get("sample_rate", "?"))
        else:
            logger.info("Audio: none")

        # --- WC traceability ---
        wc_storyboard = shot.wc_storyboard_id
        logger.info("=== TRACEABILITY ===")
        logger.info("wc_storyboard_id: %s", wc_storyboard or "NOT AVAILABLE")

        # --- Report video path for human review ---
        logger.info("=" * 60)
        logger.info("VIDEO FOR HUMAN REVIEW: %s", take.video_path)
        logger.info("=" * 60)

        # Take persisted in DB
        take_repo = TakeRepository(db)
        persisted = take_repo.get_take(take.id)
        assert persisted is not None
        assert persisted.generation_request_id == req.id
