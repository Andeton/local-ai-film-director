"""Frozen dataclasses for H3 R2V reference bindings and workflow injections."""
from dataclasses import dataclass
import re

_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


@dataclass(frozen=True)
class H3ReferenceBinding:
    """Frozen immutable reference binding for H3 R2V generation.

    M5.E evolution: subject_index and character fields are nullable to support
    future non-subject references. picture_index derives from authoritative
    selected-reference list position (1-based). subject_index is independent.
    """
    reference_asset_id: str = ""    # canonical ReferenceAsset ID (empty for M3 compat)
    reference_kind: str = ""        # ReferenceKind value (empty for M3 compat)
    subject_index: int | None = None  # associated ShotSubject index, may repeat/be None
    character_id: str | None = None
    character_name: str | None = None
    appearance: str | None = None
    picture_index: int = 1          # 1-based, from position in selected list
    local_path: str = ""
    content_sha256: str = ""
    uploaded_filename: str = ""

    def __post_init__(self):
        if self.picture_index < 1:
            raise ValueError("picture_index must be >= 1")
        if not self.local_path:
            raise ValueError("local_path required")
        if not _SHA256_RE.match(self.content_sha256):
            raise ValueError("content_sha256 must be 64 hex chars")


@dataclass(frozen=True)
class WorkflowInjection:
    """One concrete parameter injection record. Drives workflow JSON + parameters_snapshot."""
    name: str       # e.g., "prompt", "seed", "ref_image_0"
    node_id: str    # actual workflow node ID
    field: str      # actual field name
    value: str | int | float | bool  # actual submitted value

    def __post_init__(self):
        if not self.name:
            raise ValueError("name required")
        if not self.node_id:
            raise ValueError("node_id required")
        if not self.field:
            raise ValueError("field required")
