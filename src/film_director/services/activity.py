"""Process-local activity monitor for operator UI visibility.

Tracks the current long-running operation (create project, enrichment,
generation) so the UI can show progress. One active operation at a time.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ActivityState:
    """Current activity state. Mutable, process-local only."""
    operation: str = ""          # e.g. "creating_project", "enriching", "generating"
    stage: str = ""              # e.g. "wind_comic", "importing", "running"
    detail: str = ""             # human-readable detail
    started_at: float = 0.0      # time.time()
    status: str = "idle"         # "idle", "running", "complete", "failed"
    error: str = ""
    stages_completed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        elapsed = round(time.time() - self.started_at, 1) if self.started_at and self.status == "running" else 0
        return {
            "operation": self.operation,
            "stage": self.stage,
            "detail": self.detail,
            "status": self.status,
            "elapsed_seconds": elapsed,
            "error": self.error,
            "stages_completed": list(self.stages_completed),
        }


class ActivityMonitor:
    """Process-local singleton activity tracker."""

    def __init__(self) -> None:
        self._state = ActivityState()
        self._lock = threading.Lock()

    def start(self, operation: str, stage: str = "", detail: str = "") -> bool:
        """Start a new operation. Returns False if already busy."""
        with self._lock:
            if self._state.status == "running":
                return False
            self._state = ActivityState(
                operation=operation, stage=stage, detail=detail,
                started_at=time.time(), status="running",
            )
            return True

    def update_stage(self, stage: str, detail: str = "") -> None:
        with self._lock:
            if self._state.status == "running":
                prev = self._state.stage
                if prev and prev not in self._state.stages_completed:
                    self._state.stages_completed.append(prev)
                self._state.stage = stage
                self._state.detail = detail

    def complete(self, detail: str = "") -> None:
        with self._lock:
            if self._state.stage and self._state.stage not in self._state.stages_completed:
                self._state.stages_completed.append(self._state.stage)
            self._state.status = "complete"
            self._state.stage = ""
            self._state.detail = detail

    def fail(self, error: str) -> None:
        with self._lock:
            self._state.status = "failed"
            self._state.error = error

    def reset(self) -> None:
        with self._lock:
            self._state = ActivityState()

    def get_state(self) -> dict:
        with self._lock:
            return self._state.to_dict()

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._state.status == "running"
