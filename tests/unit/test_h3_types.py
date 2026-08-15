"""Tests for H3ReferenceBinding and WorkflowInjection frozen dataclasses."""
import dataclasses
import pytest

from film_director.generation.h3_types import H3ReferenceBinding, WorkflowInjection

VALID_SHA256 = "a" * 64


def _valid_binding(**overrides) -> H3ReferenceBinding:
    defaults = dict(
        subject_index=1,
        character_id="char-1",
        character_name="Alice",
        appearance="tall, dark hair",
        picture_index=1,
        local_path="/refs/alice.jpg",
        content_sha256=VALID_SHA256,
    )
    defaults.update(overrides)
    return H3ReferenceBinding(**defaults)


# --- H3ReferenceBinding ---

def test_h3_reference_binding_valid_construction():
    b = _valid_binding()
    assert b.subject_index == 1
    assert b.character_id == "char-1"
    assert b.character_name == "Alice"
    assert b.picture_index == 1
    assert b.content_sha256 == VALID_SHA256
    assert b.uploaded_filename == ""  # default


def test_h3_reference_binding_frozen():
    b = _valid_binding()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        b.subject_index = 99  # type: ignore[misc]


def test_h3_reference_binding_invalid_subject_index():
    with pytest.raises(ValueError, match="subject_index"):
        _valid_binding(subject_index=0)


def test_h3_reference_binding_invalid_subject_index_negative():
    with pytest.raises(ValueError, match="subject_index"):
        _valid_binding(subject_index=-1)


def test_h3_reference_binding_invalid_content_sha256_short():
    with pytest.raises(ValueError, match="content_sha256"):
        _valid_binding(content_sha256="abc123")


def test_h3_reference_binding_invalid_content_sha256_uppercase():
    with pytest.raises(ValueError, match="content_sha256"):
        _valid_binding(content_sha256="A" * 64)


def test_h3_reference_binding_empty_character_id():
    with pytest.raises(ValueError, match="character_id"):
        _valid_binding(character_id="")


def test_h3_reference_binding_empty_character_name():
    with pytest.raises(ValueError, match="character_name"):
        _valid_binding(character_name="")


def test_h3_reference_binding_empty_local_path():
    with pytest.raises(ValueError, match="local_path"):
        _valid_binding(local_path="")


def test_h3_reference_binding_uploaded_filename_settable_via_replace():
    b = _valid_binding()
    b2 = dataclasses.replace(b, uploaded_filename="alice_uploaded.jpg")
    assert b2.uploaded_filename == "alice_uploaded.jpg"
    assert b.uploaded_filename == ""  # original unchanged


# --- WorkflowInjection ---

def test_workflow_injection_valid_construction():
    wi = WorkflowInjection(name="prompt", node_id="6", field="text", value="a cat")
    assert wi.name == "prompt"
    assert wi.node_id == "6"
    assert wi.field == "text"
    assert wi.value == "a cat"


def test_workflow_injection_frozen():
    wi = WorkflowInjection(name="seed", node_id="3", field="seed", value=42)
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        wi.value = 99  # type: ignore[misc]


def test_workflow_injection_empty_name_rejected():
    with pytest.raises(ValueError, match="name"):
        WorkflowInjection(name="", node_id="3", field="seed", value=42)


def test_workflow_injection_empty_node_id_rejected():
    with pytest.raises(ValueError, match="node_id"):
        WorkflowInjection(name="seed", node_id="", field="seed", value=42)


def test_workflow_injection_empty_field_rejected():
    with pytest.raises(ValueError, match="field"):
        WorkflowInjection(name="seed", node_id="3", field="", value=42)


def test_workflow_injection_supports_str_int_float_bool_values():
    assert WorkflowInjection("p", "1", "f", "text").value == "text"
    assert WorkflowInjection("p", "1", "f", 42).value == 42
    assert WorkflowInjection("p", "1", "f", 3.14).value == 3.14
    assert WorkflowInjection("p", "1", "f", True).value is True
