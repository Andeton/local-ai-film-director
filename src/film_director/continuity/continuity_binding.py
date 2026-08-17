"""ContinuityBinding — immutable provenance for a first-frame continuity input.

M7.B: frozen dataclass carrying verified upstream provenance.
Used by M7.C GenerationService to inject the first frame into the FLF workflow
and persist the exact continuity snapshot in GenerationRequest.

Separate from H3ReferenceBinding (which carries character reference provenance).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ContinuityBinding:
    """Immutable binding for a continuity first-frame input.

    Carries the verified upstream provenance needed for:
    - workflow injection (uploaded_filename → LoadImage node)
    - GenerationRequest.continuity_snapshot persistence
    - downstream invalidation audit
    """

    continuity_state_id: str
    upstream_shot_id: str
    upstream_take_id: str
    upstream_take_number: int
    local_path: str                 # managed path to the last-frame file
    content_sha256: str             # SHA-256 of the frame file
    continuity_revision: int
    continuity_fingerprint: str     # from compute_continuity_fingerprint
    uploaded_filename: str = ""     # set after ComfyUI upload

    def __post_init__(self) -> None:
        if not self.continuity_state_id.strip():
            raise ValueError("continuity_state_id must not be empty")
        if not self.upstream_shot_id.strip():
            raise ValueError("upstream_shot_id must not be empty")
        if not self.upstream_take_id.strip():
            raise ValueError("upstream_take_id must not be empty")
        if self.upstream_take_number < 1:
            raise ValueError("upstream_take_number must be >= 1")
        if not self.local_path.strip():
            raise ValueError("local_path must not be empty")
        if not _SHA256_RE.match(self.content_sha256):
            raise ValueError("content_sha256 must be 64 lowercase hex chars")
        if self.continuity_revision < 0:
            raise ValueError("continuity_revision must be >= 0")
        if not _SHA256_RE.match(self.continuity_fingerprint):
            raise ValueError("continuity_fingerprint must be 64 lowercase hex chars")
