"""Bounded worker service that reconciles executor results into the engine."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, NoReturn, cast

from .engine import EngineError, WorkflowEngine
from .executors import DurableStepExecutor, HermesApiError, StepExecutor

logger = logging.getLogger(__name__)

_EXTERNAL_METHODS = (
    "require_approval_bridge",
    "submit",
    "get_status",
    "get_approval_event",
    "resolve_approval",
    "stop",
    "parse_completed",
)


class ExternalRunUncertainError(RuntimeError):
    """External work may still be active, so retrying could duplicate effects."""


class WorkerService:
    def __init__(self, engine: WorkflowEngine, executor: StepExecutor | DurableStepExecutor, *, worker_id: str | None = None):
        self.engine = engine
        self.executor = executor
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self._external = all(hasattr(executor, method) for method in _EXTERNAL_METHODS)
        if self._external:
            self.engine.external_approval_handler = self._resolve_external_approval
            self.engine.external_cancel_handler = self._stop_active_external_runs

    def tick(self, run_id: str) -> bool:
        """Execute at most one ready step. Return whether work was claimed."""

        claimed = self.engine.claim_next(run_id, self.worker_id)
        if claimed is None:
            return False
        try:
            if self._external:
                self._tick_external(claimed)
            else:
                result = cast(StepExecutor, self.executor).execute(claimed)
                if result.status != "completed":
                    raise RuntimeError(f"executor returned unsupported status: {result.status}")
                self.engine.complete_step(run_id, claimed.step_id, result)
        except Exception as exc:
            logger.warning("Step %s failed: %s", claimed.step_id, exc)
            try:
                self.engine.fail_step(
                    run_id,
                    claimed.step_id,
                    str(exc),
                    allow_retry=not isinstance(exc, ExternalRunUncertainError),
                )
            except EngineError:
                logger.exception("Failed to reconcile step failure")
                raise
        return True

    def _tick_external(self, claimed: Any) -> None:
        executor = self.executor
        store = self.engine.store
        external = store.get_external_execution(claimed.run_id, claimed.step_id, claimed.attempt)
        if external is None:
            executor.require_approval_bridge()  # type: ignore[attr-defined]
            external_id = executor.submit(claimed)  # type: ignore[attr-defined]
            external = store.create_external_execution(
                claimed.run_id,
                claimed.step_id,
                claimed.attempt,
                str(getattr(executor, "provider", "hermes")),
                external_id,
            )
        external_id = external["external_run_id"]
        deadline = time.monotonic() + float(getattr(executor, "timeout", 300))
        poll_interval = float(getattr(executor, "poll_interval", 0.5))

        while time.monotonic() < deadline:
            status = executor.get_status(external_id)  # type: ignore[attr-defined]
            state = status.get("status")
            if state == "completed":
                result = executor.parse_completed(status)  # type: ignore[attr-defined]
                if result.status != "completed":
                    store.update_external_execution(
                        claimed.run_id,
                        claimed.step_id,
                        claimed.attempt,
                        "failed",
                        last_error=result.reason or f"unsupported status: {result.status}",
                    )
                    raise RuntimeError(f"executor returned unsupported status: {result.status}")
                store.update_external_execution(
                    claimed.run_id, claimed.step_id, claimed.attempt, "completed"
                )
                self.engine.complete_step(claimed.run_id, claimed.step_id, result)
                return
            if state == "waiting_for_approval":
                try:
                    event = executor.get_approval_event(external_id)  # type: ignore[attr-defined]
                except Exception as exc:
                    self._stop_uncertain(
                        external,
                        "Hermes approval event could not be recovered",
                        exc,
                    )
                payload = self._approval_payload(external, event)
                self.engine.wait_for_external_approval(claimed, payload)
                return
            if state in {"failed", "cancelled"}:
                message = status.get("error") or status.get("output") or "unknown error"
                store.update_external_execution(
                    claimed.run_id,
                    claimed.step_id,
                    claimed.attempt,
                    str(state),
                    last_error=str(message),
                )
                raise RuntimeError(f"Hermes run {state}: {message}")
            if state not in {"queued", "running", "started", "stopping"}:
                self._stop_uncertain(
                    external,
                    f"Hermes run returned unsupported status: {state}",
                )
            time.sleep(poll_interval)

        self._stop_uncertain(
            external,
            f"Hermes run did not finish within {getattr(executor, 'timeout', 300)} seconds",
        )

    @staticmethod
    def _approval_payload(external: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        if event.get("event") != "approval.request":
            raise RuntimeError("Hermes approval event was not an approval.request")
        advertised = event.get("choices", [])
        choices = [choice for choice in ("once", "deny") if choice in advertised]
        if "deny" not in choices:
            raise RuntimeError("Hermes approval event does not offer a fail-closed deny choice")
        review = {
            key: event[key]
            for key in ("command", "description", "reason", "tool", "cwd", "smart_denied")
            if key in event
        }
        return {
            "kind": "hermes_tool",
            "provider": external["provider"],
            "external_run_id": external["external_run_id"],
            "attempt": int(external["attempt"]),
            "choices": choices,
            "review": review,
        }

    def _resolve_external_approval(self, payload: dict[str, Any], choice: str) -> None:
        external_id = str(payload["external_run_id"])
        try:
            self.executor.resolve_approval(external_id, choice)  # type: ignore[attr-defined]
        except HermesApiError as exc:
            # Hermes returns 409 when another retry already recorded the same
            # decision. It is safe only if the run is no longer waiting.
            if exc.status_code != 409:
                raise
            status = self.executor.get_status(external_id)  # type: ignore[attr-defined]
            if status.get("status") == "waiting_for_approval":
                raise

    def _stop_one(self, external: dict[str, Any]) -> None:
        try:
            self.executor.stop(external["external_run_id"])  # type: ignore[attr-defined]
            self.engine.store.update_external_execution(
                external["run_id"],
                external["step_id"],
                int(external["attempt"]),
                "stopping",
            )
        except Exception as exc:
            self.engine.store.update_external_execution(
                external["run_id"],
                external["step_id"],
                int(external["attempt"]),
                "stop_failed",
                last_error=str(exc),
            )
            raise

    def _stop_uncertain(
        self,
        external: dict[str, Any],
        message: str,
        cause: Exception | None = None,
    ) -> NoReturn:
        try:
            self._stop_one(external)
        except Exception as stop_error:
            message = f"{message}; Hermes stop failed: {stop_error}"
        error = ExternalRunUncertainError(message)
        if cause is not None:
            raise error from cause
        raise error

    def _stop_active_external_runs(self, run_id: str) -> None:
        for external in self.engine.store.list_active_external_executions(run_id):
            self._stop_one(external)

    def drain(self, run_id: str, *, max_ticks: int = 100) -> int:
        """Run ready work until blocked, terminal, or the safety bound is reached."""

        completed = 0
        while completed < max_ticks and self.tick(run_id):
            completed += 1
            status = self.engine.store.get_run(run_id)
            if status and status["status"] in {"completed", "failed", "cancelled", "waiting_for_human", "paused"}:
                break
        return completed
