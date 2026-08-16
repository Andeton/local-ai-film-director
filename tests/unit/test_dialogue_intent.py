"""Tests for DialogueIntent model."""
import pytest

from film_director.models.source_facts import DialogueIntent


class TestDialogueIntent:
    def test_basic_construction(self):
        d = DialogueIntent(text="Hello, world")
        assert d.text == "Hello, world"
        assert d.speaker_character_id is None
        assert d.speaker_name is None
        assert d.emotion == ""

    def test_verbatim_text_preserved(self):
        raw = "  多行对话\n第二行  "
        d = DialogueIntent(text=raw)
        assert d.text == raw  # exact, not stripped

    def test_emotion_preserved(self):
        d = DialogueIntent(text="I know.", emotion="tense")
        assert d.emotion == "tense"

    def test_resolved_speaker(self):
        d = DialogueIntent(
            text="I'll find the truth.",
            speaker_character_id="char-abc",
            speaker_name="Detective",
            emotion="determined",
        )
        assert d.speaker_character_id == "char-abc"
        assert d.speaker_name == "Detective"

    def test_unresolved_speaker_both_none(self):
        d = DialogueIntent(text="Who goes there?")
        assert d.speaker_character_id is None
        assert d.speaker_name is None

    def test_speaker_name_without_id_allowed(self):
        """Model does not enforce semantic coupling — resolution is caller's job."""
        d = DialogueIntent(text="Hi", speaker_name="Alice")
        assert d.speaker_name == "Alice"
        assert d.speaker_character_id is None

    def test_serialization_to_dict(self):
        d = DialogueIntent(text="Line", emotion="calm")
        data = d.model_dump()
        assert data["text"] == "Line"
        assert data["emotion"] == "calm"
        assert data["speaker_character_id"] is None
        assert data["speaker_name"] is None

    def test_model_does_not_perform_lookup(self):
        """DialogueIntent has no character resolution logic."""
        d = DialogueIntent(text="test")
        assert not hasattr(d, "resolve_speaker")
        assert not hasattr(d, "lookup_character")
