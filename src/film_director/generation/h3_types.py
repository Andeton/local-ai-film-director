"""Frozen dataclasses for H3 R2V reference bindings and workflow injections."""
from dataclasses import dataclass
import re

_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


@dataclass(frozen=True)
class H3ReferenceBinding:
    """Frozen immutable subject→reference binding. Upload creates NEW instance via replace()."""
    subject_index: int          # 1-based
    character_id: str
    character_name: str
    appearance: str
    picture_index: int          # 1-based
    local_path: str
    content_sha256: str         # SHA-256 hex of local file bytes
    uploaded_filename: str = ""

    def __post_init__(self):
        if self.subject_index < 1:
            raise ValueError("subject_index must be >= 1")
        if self.picture_index < 1:
            raise ValueError("picture_index must be >= 1")
        if not self.character_id:
            raise ValueError("character_id required")
        if not self.character_name:
            raise ValueError("character_name required")
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
