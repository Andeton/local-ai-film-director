"""Tests for M4.D — deterministic source-fact precedence in ShotSpecBuilder.

Every test in this file exercises the source_facts parameter of build_shots().
Existing M2 behavior (no source facts) is tested in test_shot_spec_builder.py.
"""
import pytest

from film_director.models.canonical import (
    Beat,
    CameraIntent,
    CharacterReference,
    Scene,
    ShotSpecificationV1,
    ShotSubject,
)
from film_director.models.provenance import Provenance
from film_director.models.source_facts import (
    DialogueIntent,
    ShotSourceFacts,
    build_shot_source_facts,
)
from film_director.models.wind_comic_dto import WCStoryboardShot
from film_director.enrichment.coverage_planner import CoverageDecision
from film_director.enrichment.shot_spec_builder import ShotSpecBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prov() -> Provenance:
    return Provenance(
        source_system="wind_comic",
        source_project_id="proj-001",
        source_asset_id="asset-001",
        source_asset_version=1,
        imported_at="2024-01-01T00:00:00+00:00",
        source_hash="a" * 64,
    )


def _scene(**kw) -> Scene:
    base = dict(
        id="scene-1", sequence_id="seq-1", wc_scene_id="wc-scene-1",
        name="Test Scene", location="Office", description="A quiet office",
        order_index=0, status="draft", provenance=_prov(),
    )
    base.update(kw)
    return Scene(**base)


def _beat(**kw) -> Beat:
    base = dict(
        id="beat-1", scene_id="scene-1",
        dramatic_action="Hero investigates", character_intention="Find clues",
        change="Confusion to clarity", characters=["Hero", "Villain"],
        order_index=0, status="draft", source="llm", version=1,
    )
    base.update(kw)
    return Beat(**base)


def _cov(**kw) -> CoverageDecision:
    base = dict(
        shot_type="establishing", shot_size="wide", angle="eye_level",
        movement="static", purpose="Set the scene", duration_sec=5.0,
    )
    base.update(kw)
    return CoverageDecision(**base)


def _char(name="Hero", char_id="char-hero", project_id="proj-001", **kw) -> CharacterReference:
    base = dict(
        id=char_id, project_id=project_id, wc_character_id=f"wc-{char_id}",
        name=name, description="", appearance="", face_ref_path=None,
        turnaround_paths=[], visual_anchors=[], status="active", provenance=_prov(),
    )
    base.update(kw)
    return CharacterReference(**base)


def _sb(shot_number=1, **kw) -> WCStoryboardShot:
    base = dict(
        asset_id=f"sb-{shot_number}", project_id="proj-001",
        shot_number=shot_number, data={}, media_urls=[], persistent_url=None,
        version=1,
    )
    base.update(kw)
    return WCStoryboardShot(**base)


def _facts(shot_number=1, **kw) -> ShotSourceFacts:
    base = dict(
        source_project_id="proj-001",
        source_script_shot_number=shot_number,
    )
    base.update(kw)
    return ShotSourceFacts(**base)


def _build(
    beat=None, coverage=None, storyboard_shots=None,
    characters=None, scene=None, source_facts=None,
):
    """Convenience: build one shot with defaults."""
    builder = ShotSpecBuilder()
    return builder.build_shots(
        beat=beat or _beat(),
        coverage=coverage or [_cov()],
        storyboard_shots=storyboard_shots or [],
        characters=characters or [],
        scene=scene or _scene(),
        source_facts=source_facts,
    )


# ===========================================================================
# BACKWARD COMPATIBILITY — no source facts
# ===========================================================================

class TestNoSourceFacts:
    """M2 behavior must be unchanged when source_facts is None / empty."""

    def test_none_source_facts_unchanged(self):
        shots = _build(source_facts=None)
        assert len(shots) == 1
        assert shots[0].action == "Hero investigates"
        assert shots[0].dramatic_purpose == "Set the scene"
        assert shots[0].lighting == {}
        assert shots[0].audio_intent == {}

    def test_empty_source_facts_unchanged(self):
        shots = _build(source_facts={})
        assert len(shots) == 1
        assert shots[0].action == "Hero investigates"

    def test_no_matching_shot_number(self):
        """Source facts exist but no match for this shot's wc_shot_number."""
        facts = {99: _facts(shot_number=99, action="Other shot action")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].wc_shot_number == 1
        assert shots[0].action == "Hero investigates"  # fallback


# ===========================================================================
# ACTION
# ===========================================================================

class TestActionPrecedence:
    def test_source_action_wins(self):
        facts = {1: _facts(action="Source character walks slowly")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Source character walks slowly"

    def test_absent_source_action_falls_back(self):
        facts = {1: _facts(action=None)}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Hero investigates"  # beat.dramatic_action

    def test_empty_source_action_falls_back(self):
        facts = {1: _facts(action="")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Hero investigates"

    def test_sentinel_action_falls_back(self):
        """WC template placeholder '动作' treated as not meaningful."""
        facts = {1: _facts(action="动作")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Hero investigates"


# ===========================================================================
# EMOTION / DRAMATIC PURPOSE
# ===========================================================================

class TestEmotionPrecedence:
    def test_source_emotion_wins(self):
        facts = {1: _facts(emotion="Tense anticipation")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].dramatic_purpose == "Tense anticipation"

    def test_absent_emotion_falls_back(self):
        facts = {1: _facts(emotion=None)}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].dramatic_purpose == "Set the scene"  # coverage.purpose

    def test_sentinel_emotion_falls_back(self):
        """WC template placeholder '情绪' treated as not meaningful."""
        facts = {1: _facts(emotion="情绪")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].dramatic_purpose == "Set the scene"


# ===========================================================================
# DURATION
# ===========================================================================

class TestDurationPrecedence:
    def test_source_duration_wins(self):
        facts = {1: _facts(duration_sec=12.5)}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].duration_sec == 12.5

    def test_absent_duration_uses_coverage(self):
        facts = {1: _facts(duration_sec=None)}
        sb = _sb(shot_number=1)
        shots = _build(
            coverage=[_cov(duration_sec=7.0)],
            storyboard_shots=[sb], source_facts=facts,
        )
        assert shots[0].duration_sec == 7.0

    def test_source_duration_beats_storyboard_duration(self):
        """source_facts.duration_sec > storyboard data.duration."""
        facts = {1: _facts(duration_sec=10.0)}
        sb = _sb(shot_number=1, data={"duration": 8.0})
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].duration_sec == 10.0


# ===========================================================================
# CAMERA — partial merge
# ===========================================================================

class TestCameraPartialMerge:
    def test_source_angle_wins(self):
        facts = {1: _facts(camera_angle="low angle")}
        sb = _sb(shot_number=1)
        shots = _build(
            coverage=[_cov(angle="high", movement="pan_left")],
            storyboard_shots=[sb], source_facts=facts,
        )
        assert shots[0].camera.angle == "low angle"
        assert shots[0].camera.movement == "pan_left"  # preserved from coverage

    def test_source_movement_wins(self):
        facts = {1: _facts(camera_movement="dolly in")}
        sb = _sb(shot_number=1)
        shots = _build(
            coverage=[_cov(angle="high", movement="pan_left")],
            storyboard_shots=[sb], source_facts=facts,
        )
        assert shots[0].camera.angle == "high"  # preserved from coverage
        assert shots[0].camera.movement == "dolly in"

    def test_both_source_camera_fields(self):
        facts = {1: _facts(camera_angle="bird's eye", camera_movement="crane up")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].camera.angle == "bird's eye"
        assert shots[0].camera.movement == "crane up"

    def test_absent_source_camera_uses_coverage(self):
        facts = {1: _facts(camera_angle=None, camera_movement=None)}
        sb = _sb(shot_number=1)
        shots = _build(
            coverage=[_cov(angle="dutch", movement="tracking")],
            storyboard_shots=[sb], source_facts=facts,
        )
        assert shots[0].camera.angle == "dutch"
        assert shots[0].camera.movement == "tracking"

    def test_shot_size_always_from_coverage(self):
        """Source facts don't carry shot_size; coverage always determines it."""
        facts = {1: _facts(camera_angle="low")}
        sb = _sb(shot_number=1)
        shots = _build(
            coverage=[_cov(shot_size="close_up")],
            storyboard_shots=[sb], source_facts=facts,
        )
        assert shots[0].camera.shot_size == "close_up"


# ===========================================================================
# LIGHTING
# ===========================================================================

class TestLightingPrecedence:
    def test_source_lighting_applied(self):
        facts = {1: _facts(lighting="warm golden hour")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].lighting == {"source": "warm golden hour"}

    def test_absent_lighting_uses_fallback(self):
        facts = {1: _facts(lighting=None)}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].lighting == {}

    def test_empty_lighting_uses_fallback(self):
        facts = {1: _facts(lighting="")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].lighting == {}


# ===========================================================================
# STORYBOARD DESCRIPTION (verbatim)
# ===========================================================================

class TestStoryboardDescription:
    def test_verbatim_preservation(self):
        desc = "A cinematic wide shot of the hero, camera angle: low, lighting: warm"
        facts = {1: _facts(storyboard_description=desc)}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].environment["storyboard_description"] == desc

    def test_existing_environment_keys_preserved(self):
        facts = {1: _facts(storyboard_description="Storyboard text here")}
        sb = _sb(shot_number=1)
        shots = _build(
            scene=_scene(location="Rooftop", description="Night sky"),
            storyboard_shots=[sb], source_facts=facts,
        )
        env = shots[0].environment
        assert env["location"] == "Rooftop"
        assert env["description"] == "Night sky"
        assert env["storyboard_description"] == "Storyboard text here"

    def test_empty_storyboard_description_not_added(self):
        facts = {1: _facts(storyboard_description="")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert "storyboard_description" not in shots[0].environment


# ===========================================================================
# DIALOGUE
# ===========================================================================

class TestDialogue:
    def test_dialogue_text_preserved_verbatim(self):
        facts = {1: _facts(dialogue_text="Where am I?")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        di = shots[0].audio_intent["dialogue"]
        assert di["text"] == "Where am I?"

    def test_dialogue_emotion_preserved(self):
        facts = {1: _facts(dialogue_text="I know.", emotion="Determined")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        di = shots[0].audio_intent["dialogue"]
        assert di["emotion"] == "Determined"

    def test_sentinel_emotion_empty_in_dialogue(self):
        facts = {1: _facts(dialogue_text="Hello.", emotion="情绪")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        di = shots[0].audio_intent["dialogue"]
        assert di["emotion"] == ""

    def test_no_dialogue_no_audio_intent(self):
        facts = {1: _facts(dialogue_text=None)}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].audio_intent == {} or "dialogue" not in shots[0].audio_intent

    def test_empty_dialogue_no_audio_intent(self):
        facts = {1: _facts(dialogue_text="")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].audio_intent == {} or "dialogue" not in shots[0].audio_intent


# ===========================================================================
# DIALOGUE — speaker resolution
# ===========================================================================

class TestDialogueSpeaker:
    def test_unique_single_character_speaker_resolved(self):
        """1 source character + unique canonical match → speaker set."""
        hero = _char(name="Hero", char_id="char-hero")
        facts = {1: _facts(dialogue_text="Let's go!", characters=["Hero"])}
        sb = _sb(shot_number=1)
        shots = _build(
            storyboard_shots=[sb], characters=[hero], source_facts=facts,
        )
        di = shots[0].audio_intent["dialogue"]
        assert di["speaker_character_id"] == "char-hero"
        assert di["speaker_name"] == "Hero"

    def test_zero_source_characters_speaker_none(self):
        facts = {1: _facts(dialogue_text="Hello.", characters=[])}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        di = shots[0].audio_intent["dialogue"]
        assert di["speaker_character_id"] is None
        assert di["speaker_name"] is None

    def test_multiple_source_characters_speaker_none(self):
        """Multiple source characters → cannot determine speaker."""
        hero = _char(name="Hero", char_id="char-hero")
        villain = _char(name="Villain", char_id="char-villain")
        facts = {1: _facts(dialogue_text="Stop!", characters=["Hero", "Villain"])}
        sb = _sb(shot_number=1)
        shots = _build(
            storyboard_shots=[sb], characters=[hero, villain], source_facts=facts,
        )
        di = shots[0].audio_intent["dialogue"]
        assert di["speaker_character_id"] is None
        assert di["speaker_name"] is None

    def test_single_character_no_canonical_match_speaker_none(self):
        """1 source character but zero canonical matches."""
        facts = {1: _facts(dialogue_text="Hello.", characters=["Unknown"])}
        sb = _sb(shot_number=1)
        shots = _build(
            storyboard_shots=[sb], characters=[], source_facts=facts,
        )
        di = shots[0].audio_intent["dialogue"]
        assert di["speaker_character_id"] is None
        assert di["speaker_name"] is None

    def test_single_character_ambiguous_canonical_speaker_none(self):
        """1 source character but multiple canonical matches → ambiguous."""
        char1 = _char(name="Hero", char_id="char-hero-1")
        char2 = _char(name="hero", char_id="char-hero-2")
        facts = {1: _facts(dialogue_text="Go!", characters=["Hero"])}
        sb = _sb(shot_number=1)
        shots = _build(
            storyboard_shots=[sb], characters=[char1, char2], source_facts=facts,
        )
        di = shots[0].audio_intent["dialogue"]
        assert di["speaker_character_id"] is None
        assert di["speaker_name"] is None

    def test_another_project_character_does_not_interfere(self):
        """Character with same name but different project_id must not match."""
        hero_ours = _char(name="Hero", char_id="char-hero", project_id="proj-001")
        # Characters passed to build_shots are always project-scoped by the caller
        # So a char from another project would not be in the list.
        # This test verifies that project scoping is handled by the caller.
        facts = {1: _facts(dialogue_text="Hi!", characters=["Hero"])}
        sb = _sb(shot_number=1)
        shots = _build(
            storyboard_shots=[sb], characters=[hero_ours], source_facts=facts,
        )
        di = shots[0].audio_intent["dialogue"]
        assert di["speaker_character_id"] == "char-hero"

    def test_speaker_casefold_match(self):
        """Speaker resolution uses casefold+strip."""
        hero = _char(name="Hero", char_id="char-hero")
        facts = {1: _facts(dialogue_text="Hi!", characters=["  HERO  "])}
        sb = _sb(shot_number=1)
        shots = _build(
            storyboard_shots=[sb], characters=[hero], source_facts=facts,
        )
        di = shots[0].audio_intent["dialogue"]
        assert di["speaker_character_id"] == "char-hero"
        # speaker_name should be the SOURCE character name, not canonical
        assert di["speaker_name"] == "  HERO  "


# ===========================================================================
# SUBJECTS — source character precedence
# ===========================================================================

class TestSubjectPrecedence:
    def test_source_characters_resolved_as_subjects(self):
        """Source characters take precedence over beat characters."""
        hero = _char(name="Hero", char_id="char-hero")
        villain = _char(name="Villain", char_id="char-villain")
        facts = {1: _facts(characters=["Villain"])}
        sb = _sb(shot_number=1)
        shots = _build(
            beat=_beat(characters=["Hero"]),
            storyboard_shots=[sb],
            characters=[hero, villain],
            source_facts=facts,
        )
        subjects = shots[0].subjects
        assert len(subjects) == 1
        assert subjects[0].character_id == "char-villain"

    def test_all_source_characters_resolved_uses_source(self):
        hero = _char(name="Hero", char_id="char-hero")
        villain = _char(name="Villain", char_id="char-villain")
        facts = {1: _facts(characters=["Hero", "Villain"])}
        sb = _sb(shot_number=1)
        shots = _build(
            beat=_beat(characters=["Hero"]),
            storyboard_shots=[sb],
            characters=[hero, villain],
            source_facts=facts,
        )
        assert len(shots[0].subjects) == 2

    def test_partial_resolution_falls_back_to_beat(self):
        """If any source character doesn't resolve, fall back to beat characters."""
        hero = _char(name="Hero", char_id="char-hero")
        facts = {1: _facts(characters=["Hero", "Unknown"])}
        sb = _sb(shot_number=1)
        shots = _build(
            beat=_beat(characters=["Hero"]),
            storyboard_shots=[sb],
            characters=[hero],
            source_facts=facts,
        )
        # Falls back to beat resolution (Hero resolves, Unknown omitted)
        subjects = shots[0].subjects
        assert len(subjects) == 1
        assert subjects[0].character_id == "char-hero"

    def test_empty_source_characters_uses_beat(self):
        hero = _char(name="Hero", char_id="char-hero")
        facts = {1: _facts(characters=[])}
        sb = _sb(shot_number=1)
        shots = _build(
            beat=_beat(characters=["Hero"]),
            storyboard_shots=[sb],
            characters=[hero],
            source_facts=facts,
        )
        assert len(shots[0].subjects) == 1
        assert shots[0].subjects[0].character_id == "char-hero"

    def test_ambiguous_source_character_not_guessed(self):
        """Ambiguous canonical match → character omitted from subjects."""
        char1 = _char(name="Hero", char_id="char-hero-1")
        char2 = _char(name="hero", char_id="char-hero-2")
        facts = {1: _facts(characters=["Hero"])}
        sb = _sb(shot_number=1)
        shots = _build(
            beat=_beat(characters=[]),
            storyboard_shots=[sb],
            characters=[char1, char2],
            source_facts=facts,
        )
        # Ambiguous → partial resolution → falls back to beat (empty)
        assert len(shots[0].subjects) == 0

    def test_no_source_facts_uses_beat_characters(self):
        """Without source facts, existing beat-character resolution works."""
        hero = _char(name="Hero", char_id="char-hero")
        shots = _build(
            beat=_beat(characters=["Hero"]),
            characters=[hero],
            source_facts=None,
        )
        assert len(shots[0].subjects) == 1
        assert shots[0].subjects[0].character_id == "char-hero"


# ===========================================================================
# CORRELATION — explicit key, not index
# ===========================================================================

class TestCorrelation:
    def test_explicit_wc_shot_number_key(self):
        """Source fact looked up by wc_shot_number, not by index."""
        # Shot has wc_shot_number=5 via storyboard, source_facts keyed by 5
        facts = {5: _facts(shot_number=5, action="Explicit key action")}
        sb = _sb(shot_number=5)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Explicit key action"
        assert shots[0].wc_shot_number == 5

    def test_no_wc_shot_number_no_source_facts_applied(self):
        """Without storyboard linkage, no wc_shot_number → no source fact applied."""
        facts = {1: _facts(action="Should not apply")}
        shots = _build(storyboard_shots=[], source_facts=facts)
        assert shots[0].wc_shot_number is None
        assert shots[0].action == "Hero investigates"

    def test_multiple_shots_correct_correlation(self):
        """Each shot gets its own source fact by wc_shot_number."""
        facts = {
            1: _facts(shot_number=1, action="Action for shot 1"),
            2: _facts(shot_number=2, action="Action for shot 2"),
        }
        sbs = [_sb(shot_number=1), _sb(shot_number=2)]
        shots = _build(
            coverage=[_cov(), _cov()],
            storyboard_shots=sbs,
            source_facts=facts,
        )
        assert shots[0].action == "Action for shot 1"
        assert shots[1].action == "Action for shot 2"


# ===========================================================================
# RECOMPUTATION — source facts can be rebuilt
# ===========================================================================

class TestRecomputation:
    def test_source_facts_recomputable_from_wc_data(self):
        """build_shot_source_facts produces identical output on repeated calls."""
        from film_director.models.wind_comic_dto import WCScriptShot, WCStoryboardShot

        script = [WCScriptShot(1, "desc", ["Hero"], "Hello", "walks", "happy")]
        sb = [WCStoryboardShot("sb-1", "proj", 1, {"description": "A shot"}, [], None, 1)]
        facts_a = build_shot_source_facts("proj", script, sb)
        facts_b = build_shot_source_facts("proj", script, sb)
        assert facts_a[1] == facts_b[1]

    def test_enrichment_does_not_depend_on_import_memory(self):
        """Source facts should be obtainable from WC bundle at any time,
        not only during the initial import."""
        # This is a design test — source_facts parameter proves the contract:
        # caller builds fresh source facts and passes them in.
        facts = {1: _facts(action="Fresh source action")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Fresh source action"


# ===========================================================================
# HUMAN EDIT PRESERVATION
# ===========================================================================

class TestHumanEditPreservation:
    def test_build_shots_creates_generated_source(self):
        """build_shots always creates source='generated' — human edit check
        is EnrichmentService's responsibility, not ShotSpecBuilder's."""
        facts = {1: _facts(action="Source action")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].source == "generated"


# ===========================================================================
# SENTINEL VALUES
# ===========================================================================

class TestSentinelValues:
    def test_action_sentinel_dong_zuo(self):
        facts = {1: _facts(action="动作")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Hero investigates"  # fallback

    def test_emotion_sentinel_qing_xu(self):
        facts = {1: _facts(emotion="情绪")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].dramatic_purpose == "Set the scene"  # fallback

    def test_non_sentinel_chinese_preserved(self):
        """Non-sentinel Chinese text must NOT be filtered."""
        facts = {1: _facts(action="英雄走进房间")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "英雄走进房间"

    def test_whitespace_sentinel_falls_back(self):
        """Whitespace-only action treated as empty."""
        facts = {1: _facts(action="   ")}
        sb = _sb(shot_number=1)
        shots = _build(storyboard_shots=[sb], source_facts=facts)
        assert shots[0].action == "Hero investigates"


# ===========================================================================
# MIXED — some fields from source, others from fallback
# ===========================================================================

class TestMixedPrecedence:
    def test_mixed_source_and_fallback(self):
        """Some source fields present, others absent → mix of sources."""
        facts = {1: _facts(
            action="Source action",
            emotion=None,  # fallback to coverage.purpose
            duration_sec=8.0,
            camera_angle="bird's eye",
            camera_movement=None,  # fallback to coverage.movement
            lighting=None,
            storyboard_description="A beautiful scene",
        )}
        sb = _sb(shot_number=1)
        shots = _build(
            coverage=[_cov(purpose="Establish mood", movement="tracking", angle="high")],
            storyboard_shots=[sb],
            source_facts=facts,
        )
        s = shots[0]
        assert s.action == "Source action"
        assert s.dramatic_purpose == "Establish mood"  # fallback
        assert s.duration_sec == 8.0
        assert s.camera.angle == "bird's eye"
        assert s.camera.movement == "tracking"  # fallback
        assert s.lighting == {}  # fallback
        assert s.environment["storyboard_description"] == "A beautiful scene"
