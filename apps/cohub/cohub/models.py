"""Domain models shared by the workflow engine and API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
RUN_STATUSES = frozenset({"queued", "running", "waiting_for_human", "paused", "blocked", *TERMINAL_RUN_STATUSES})
STEP_STATUSES = frozenset({"pending", "ready", "leased", "running", "waiting_for_human", "retry_scheduled", "completed", "failed", "cancelled"})


@dataclass(frozen=True)
class StepResult:
    """A normalized executor result submitted to the deterministic engine."""

    status: str = "completed"
    output: dict[str, Any] = field(default_factory=dict)
    route: str | None = None
    reason: str | None = None
    artifacts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ClaimedStep:
    """A ready step leased to one worker."""

    run_id: str
    step_id: str
    attempt: int
    node: dict[str, Any]
    workflow: dict[str, Any]
    task_input: dict[str, Any]
    dependency_outputs: dict[str, Any]
    lease_owner: str
    lease_expires_at: str
