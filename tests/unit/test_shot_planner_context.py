"""Tests that ShotPlanner receives and uses the project description.

Regression test for the bug where WC produced generic placeholder content
and the planner received no meaningful story context, generating unrelated
landscape shots instead of the user's original apartment/thriller scene.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from film_director.enrichment.prompts import build_shot_plan_messages
from film_director.enrichment.shot_planner import ShotPlanner, _validate_shot_plan
from film_director.llm.provider import LLMResponse
from film_director.models.canonical import CharacterReference, Scene
from film_director.models.provenance import Provenance


def _prov():
    return Provenance(
        source_system="test", source_project_id="p1",
        source_asset_id="a1", source_asset_version=1,
        imported_at="2026-01-01", source_hash="h",
    )


# The distinctive test idea — if the planner produces "landscape" or
# "precipice" shots from this input, the context was lost.
APARTMENT_IDEA = (
    "A tense nighttime scene in a small New York apartment. "
    "An exhausted man sits alone at a kitchen table when he hears someone "
    "quietly trying to unlock the front door. He freezes, turns off the lamp, "
    "and watches the hallway. The door slowly opens and a woman steps inside "
    "holding a blood-stained envelope."
)


class TestPromptIncludesProjectDescription:
    """The project description must appear in the LLM messages."""

    def test_description_in_messages(self):
        msgs = build_shot_plan_messages(
            scene_name="Main Scene",
            scene_location="Main",
            scene_description="Opening",
            characters=[],
            storyboard_notes=[],
            project_description=APARTMENT_IDEA,
        )
        user_msg = msgs[1]["content"]
        assert "New York apartment" in user_msg
        assert "blood-stained envelope" in user_msg

    def test_description_before_scene(self):
        """Project description appears before scene metadata."""
        msgs = build_shot_plan_messages(
            scene_name="Scene1",
            scene_location="Loc",
            scene_description="Desc",
            characters=[],
            storyboard_notes=[],
            project_description=APARTMENT_IDEA,
        )
        user_msg = msgs[1]["content"]
        desc_pos = user_msg.index("New York apartment")
        scene_pos = user_msg.index("Scene: Scene1")
        assert desc_pos < scene_pos

    def test_empty_description_omitted(self):
        msgs = build_shot_plan_messages(
            scene_name="S", scene_location="L", scene_description="D",
            characters=[], storyboard_notes=[], project_description="",
        )
        user_msg = msgs[1]["content"]
        assert "Story/Premise" not in user_msg

    def test_generic_scene_with_real_description(self):
        """Even with generic WC scene data, the description carries the story."""
        msgs = build_shot_plan_messages(
            scene_name="主场景",           # generic Chinese placeholder
            scene_location="主场景",       # generic
            scene_description="开场远景",  # generic "opening wide shot"
            characters=[{"name": "主角"}],  # generic "protagonist"
            storyboard_notes=[],
            project_description=APARTMENT_IDEA,
        )
        user_msg = msgs[1]["content"]
        # The distinctive idea content is present
        assert "exhausted man" in user_msg
        assert "blood-stained envelope" in user_msg


class TestShotPlannerPassesDescription:
    """ShotPlanner.plan_scene passes project_description through to prompt."""

    def test_description_reaches_llm(self):
        call_messages = []

        def _fake_chat(messages, expect_json=False):
            call_messages.append(messages)
            return LLMResponse(
                content=json.dumps({"shots": [
                    {"action": "Man sits at kitchen table",
                     "dramatic_purpose": "Establish isolation",
                     "shot_size": "medium", "characters": ["Man"],
                     "duration_sec": 5.0},
                ]}),
                parsed={"shots": [
                    {"action": "Man sits at kitchen table",
                     "dramatic_purpose": "Establish isolation",
                     "shot_size": "medium", "characters": ["Man"],
                     "duration_sec": 5.0},
                ]},
                model="test",
            )

        llm = MagicMock()
        llm.chat.side_effect = _fake_chat

        planner = ShotPlanner(llm)
        scene = Scene(
            id="s1", sequence_id="seq1", wc_scene_id="wc-s1",
            name="主场景", location="主场景", description="开场远景",
            order_index=0, provenance=_prov(),
        )

        planner.plan_scene(
            scene=scene, characters=[], storyboard_notes=[],
            project_description=APARTMENT_IDEA,
        )

        # Verify the LLM received the apartment idea
        assert len(call_messages) == 1
        user_content = call_messages[0][1]["content"]
        assert "New York apartment" in user_content
        assert "blood-stained envelope" in user_content


class TestShotPlanSizeContract:
    """Planner should produce 5-7 shots, not 10+."""

    def test_valid_5_shot_plan(self):
        parsed = {"shots": [
            {"action": f"Shot {i}", "dramatic_purpose": f"Purpose {i}",
             "shot_size": "medium", "duration_sec": 5.0}
            for i in range(5)
        ]}
        candidates, error = _validate_shot_plan(parsed)
        assert error is None
        assert len(candidates) == 5

    def test_valid_7_shot_plan(self):
        parsed = {"shots": [
            {"action": f"Shot {i}", "dramatic_purpose": f"Purpose {i}",
             "shot_size": "wide", "duration_sec": 5.0}
            for i in range(7)
        ]}
        candidates, error = _validate_shot_plan(parsed)
        assert error is None
        assert len(candidates) == 7
