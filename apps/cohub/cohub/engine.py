"""Deterministic workflow state machine."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .db import CohubStore, utc_now
from .models import ClaimedStep, StepResult, TERMINAL_RUN_STATUSES
from .schemas import WorkflowValidationError, canonical_json, validate_output


class EngineError(RuntimeError):
    """Raised when an execution request violates workflow state or policy."""


class WorkflowEngine:
    """Own legal routing, persistence, retries, leases, and approvals."""

    def __init__(self, store: CohubStore, artifact_root: str | Path):
        self.store = store
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.external_approval_handler: Callable[[dict[str, Any], str], None] | None = None
        self.external_cancel_handler: Callable[[str], None] | None = None

    def start_run(
        self,
        workflow_name: str,
        input_data: dict[str, Any],
        *,
        title: str | None = None,
        version: int | None = None,
        requested_provider: str | None = None,
        requested_model: str | None = None,
    ) -> dict[str, Any]:
        workflow = self.store.get_workflow(workflow_name, version)
        if not workflow:
            raise EngineError(f"workflow not found: {workflow_name}")
        task = self.store.create_task(title or workflow_name, input_data)
        run = self.store.create_run(
            task["id"],
            workflow,
            input_data,
            requested_provider=requested_provider,
            requested_model=requested_model,
        )
        self.store.transition_run(run["id"], "running", "run.started", {})
        definition = workflow["definition"]
        self._activate(run["id"], definition["start"], definition)
        return self.store.get_run(run["id"])  # type: ignore[return-value]

    def _activate(self, run_id: str, step_id: str, workflow: dict[str, Any]) -> None:
        node = workflow["nodes"][step_id]
        existing = self.store.get_step(run_id, step_id)
        if existing and existing["status"] in {"ready", "leased", "running", "waiting_for_human", "completed"}:
            return
        max_steps = workflow.get("budget", {}).get("max_steps")
        run = self.store.get_run(run_id)
        if isinstance(max_steps, int) and run and run["step_count"] >= max_steps:
            message = f"workflow exceeded max_steps budget ({max_steps})"
            self.store.transition_run(run_id, "failed", "run.budget_exceeded", {"budget": "max_steps"}, error=message)
            return
        node_type = node["type"]
        if node_type == "end":
            self.store.upsert_step(run_id, step_id, "completed", completed_at=utc_now(), output_json={})
            self.store.transition_run(run_id, "completed", "run.completed", {"end_step": step_id})
            return
        if node_type == "parallel":
            self.store.upsert_step(run_id, step_id, "completed", completed_at=utc_now(), output_json={})
            self.store.transition_run(run_id, "running", "step.completed", {"node_type": "parallel"})
            for branch in node["branches"]:
                self._activate(run_id, branch, workflow)
            return
        if node.get("side_effect"):
            payload = node.get("approval_payload")
            if not isinstance(payload, dict) or not payload:
                raise EngineError(f"side-effect node {step_id} requires approval_payload")
            self.store.upsert_step(run_id, step_id, "waiting_for_human")
            self.store.create_approval(run_id, step_id, payload)
            self.store.transition_run(run_id, "waiting_for_human", "run.waiting_for_human", {"step_id": step_id})
            return
        if node_type == "human":
            payload = node.get("payload", {"prompt": node.get("prompt", "Approval required")})
            self.store.upsert_step(run_id, step_id, "waiting_for_human")
            self.store.create_approval(run_id, step_id, payload)
            self.store.transition_run(run_id, "waiting_for_human", "run.waiting_for_human", {"step_id": step_id})
            return
        self.store.upsert_step(run_id, step_id, "ready")
        self.store.transition_run(run_id, "running", "step.ready", {"step_id": step_id, "node_type": node_type})

    def complete_step(self, run_id: str, step_id: str, result: StepResult) -> None:
        run = self._active_run(run_id)
        workflow = self.store.get_run_workflow(run_id)["definition"]
        node = workflow["nodes"].get(step_id)
        step = self.store.get_step(run_id, step_id)
        if not node or not step:
            raise EngineError(f"step not active: {step_id}")
        if step["status"] not in {"ready", "leased", "running"}:
            raise EngineError(f"step {step_id} cannot complete from {step['status']}")
        try:
            validate_output(result.output, node.get("output_schema"))
        except WorkflowValidationError as exc:
            self.store.transition_run(run_id, run["status"], "step.validation_failed", {"step_id": step_id, "error": str(exc)})
            raise EngineError(str(exc)) from exc

        route = result.route
        target: str | None = None
        if node["type"] == "decision":
            if route not in node["routes"]:
                raise EngineError(f"decision step {step_id} selected undeclared route: {route}")
            target = node["routes"][route]
        else:
            target = node.get("next")

        self.store.upsert_step(
            run_id,
            step_id,
            "completed",
            output_json=result.output,
            route=route,
            reason=result.reason,
            lease_owner=None,
            lease_expires_at=None,
            completed_at=utc_now(),
        )
        for artifact in result.artifacts:
            self.store.create_artifact(
                run_id,
                step_id,
                str(artifact["path"]),
                str(artifact["sha256"]),
                int(artifact["size"]),
            )
        self.store.transition_run(run_id, "running", "step.completed", {"step_id": step_id, "route": route})
        if target:
            self._activate(run_id, target, workflow)
        self._converge_parallel(run_id, step_id, workflow)

    def _converge_parallel(self, run_id: str, completed_step: str, workflow: dict[str, Any]) -> None:
        for parallel_id, node in workflow["nodes"].items():
            if node["type"] != "parallel" or completed_step not in node.get("branches", []):
                continue
            if all((self.store.get_step(run_id, branch) or {}).get("status") == "completed" for branch in node["branches"]):
                target = node.get("next")
                if target:
                    self._activate(run_id, target, workflow)

    def fail_step(self, run_id: str, step_id: str, error: str, *, allow_retry: bool = True) -> None:
        self._active_run(run_id)
        workflow = self.store.get_run_workflow(run_id)["definition"]
        node = workflow["nodes"][step_id]
        step = self.store.get_step(run_id, step_id)
        if not step:
            raise EngineError(f"step not active: {step_id}")
        attempt = max(1, int(step["attempt"] or 0))
        max_attempts = int(node.get("max_attempts", workflow.get("defaults", {}).get("max_attempts", 1)))
        if allow_retry and attempt < max_attempts:
            self.store.upsert_step(
                run_id,
                step_id,
                "ready",
                attempt=attempt + 1,
                error=error,
                lease_owner=None,
                lease_expires_at=None,
            )
            self.store.transition_run(run_id, "running", "step.retry_scheduled", {"step_id": step_id, "attempt": attempt + 1, "error": error})
        else:
            self.store.upsert_step(run_id, step_id, "failed", attempt=attempt, error=error, completed_at=utc_now())
            self.store.transition_run(run_id, "failed", "run.failed", {"step_id": step_id, "error": error}, error=error)

    def claim_next(self, run_id: str, worker_id: str, *, lease_seconds: int = 60) -> ClaimedStep | None:
        run = self._active_run(run_id)
        if run["status"] != "running":
            return None
        workflow = self.store.get_run_workflow(run_id)["definition"]
        max_duration = workflow.get("budget", {}).get("max_duration_seconds")
        if isinstance(max_duration, (int, float)):
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(run["created_at"])).total_seconds()
            if elapsed >= max_duration:
                message = f"workflow exceeded max_duration_seconds budget ({max_duration})"
                self.store.transition_run(
                    run_id,
                    "failed",
                    "run.budget_exceeded",
                    {"budget": "max_duration_seconds"},
                    error=message,
                )
                return None
        now = datetime.now(UTC)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        now_text = now.isoformat(timespec="milliseconds")
        with self.store.connection() as connection:
            row = connection.execute(
                """SELECT * FROM steps
                   WHERE run_id=? AND (status='ready' OR (status='leased' AND lease_expires_at<?))
                   ORDER BY rowid LIMIT 1""",
                (run_id, now_text),
            ).fetchone()
            if not row:
                return None
            attempt = max(1, int(row["attempt"] or 0))
            updated = connection.execute(
                """UPDATE steps SET status='leased',attempt=?,lease_owner=?,lease_expires_at=?,started_at=COALESCE(started_at,?),updated_at=?
                   WHERE run_id=? AND step_id=? AND (status='ready' OR (status='leased' AND lease_expires_at<?))""",
                (attempt, worker_id, expires, now_text, now_text, run_id, row["step_id"], now_text),
            )
            if updated.rowcount != 1:
                return None
            self.store._event(connection, run_id, "step.leased", {"worker_id": worker_id, "attempt": attempt}, row["step_id"])
            step_id = row["step_id"]

        workflow_record = self.store.get_run_workflow(run_id)
        workflow = workflow_record["definition"]
        completed = {
            step["step_id"]: step["output"]
            for step in self.store.get_steps(run_id)
            if step["status"] == "completed" and step.get("output") is not None
        }
        return ClaimedStep(
            run_id=run_id,
            step_id=step_id,
            attempt=attempt,
            node=workflow["nodes"][step_id],
            workflow=workflow,
            task_input=run["input"],
            dependency_outputs=completed,
            execution_selection={
                key: value
                for key, value in {
                    "provider": run.get("requested_provider"),
                    "model": run.get("requested_model"),
                }.items()
                if value
            },
            lease_owner=worker_id,
            lease_expires_at=expires,
        )

    def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        note: str | None = None,
        *,
        expected_payload_hash: str | None = None,
    ) -> dict[str, Any]:
        pending = self.store.get_approval(approval_id)
        if not pending:
            raise EngineError(f"approval not found: {approval_id}")
        if expected_payload_hash is not None and pending["payload_hash"] != expected_payload_hash:
            raise EngineError("approval payload hash does not match the reviewed payload")
        if pending["status"] != "pending":
            if pending["status"] == decision:
                return pending
            raise EngineError(f"approval already resolved as {pending['status']}")
        payload = pending["payload"]
        if payload.get("kind") == "hermes_tool":
            if self.external_approval_handler is None:
                raise EngineError("Hermes approval bridge is unavailable")
            choice = "once" if decision == "approved" else "deny"
            if choice not in payload.get("choices", []):
                raise EngineError(f"Hermes approval choice is not available: {choice}")
            # Resolve Hermes first. If local persistence then fails, a repeated
            # call is reconciled idempotently by the external handler.
            self.external_approval_handler(payload, choice)
            return self.store.resume_external_after_approval(
                approval_id,
                decision,
                note,
                int(payload["attempt"]),
                choice,
            )
        approval = self.store.resolve_approval(approval_id, decision, note)
        run_id, step_id = approval["run_id"], approval["step_id"]
        if decision == "rejected":
            self.store.upsert_step(run_id, step_id, "failed", error=note or "Approval rejected", completed_at=utc_now())
            self.store.transition_run(run_id, "failed", "run.failed", {"step_id": step_id, "reason": "approval_rejected"}, error=note or "Approval rejected")
            return approval
        workflow = self.store.get_run_workflow(run_id)["definition"]
        node = workflow["nodes"][step_id]
        if node.get("side_effect"):
            self.store.upsert_step(run_id, step_id, "ready", error=None)
            self.store.transition_run(run_id, "running", "step.ready", {"step_id": step_id, "approval_id": approval_id})
            return approval
        self.store.upsert_step(run_id, step_id, "completed", output_json={"approval": "approved"}, completed_at=utc_now())
        self.store.transition_run(run_id, "running", "step.completed", {"step_id": step_id, "approval_id": approval_id})
        if node.get("next"):
            self._activate(run_id, node["next"], workflow)
        return approval

    def pause(self, run_id: str) -> None:
        run = self._active_run(run_id)
        if run["status"] not in {"running", "waiting_for_human"}:
            raise EngineError(f"run cannot pause from {run['status']}")
        self.store.transition_run(run_id, "paused", "run.paused", {})

    def resume(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not run:
            raise EngineError(f"run not found: {run_id}")
        if run["status"] in TERMINAL_RUN_STATUSES:
            raise EngineError(f"terminal run cannot resume from {run['status']}")
        if run["status"] != "paused":
            raise EngineError(f"run cannot resume from {run['status']}")
        pending = self.store.list_approvals(run_id, "pending")
        status = "waiting_for_human" if pending else "running"
        self.store.transition_run(run_id, status, "run.resumed", {})

    def cancel(self, run_id: str) -> None:
        self._active_run(run_id)
        if self.external_cancel_handler is not None:
            self.external_cancel_handler(run_id)
        self.store.transition_run(run_id, "cancelled", "run.cancelled", {})

    def wait_for_external_approval(
        self,
        claimed: ClaimedStep,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Checkpoint an external tool gate and release the worker lease."""

        return self.store.checkpoint_external_approval(
            claimed.run_id,
            claimed.step_id,
            claimed.attempt,
            payload,
        )

    def approval_hash(self, payload: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def artifact_path(self, relative_path: str) -> Path:
        candidate = (self.artifact_root / relative_path).resolve()
        if candidate != self.artifact_root and self.artifact_root not in candidate.parents:
            raise EngineError("artifact path escapes configured root")
        return candidate

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise EngineError(f"run not found: {run_id}")
        return {
            **run,
            "steps": self.store.get_steps(run_id),
            "approvals": self.store.list_approvals(run_id),
            "external_executions": self.store.list_external_executions(run_id),
            "artifacts": self.store.list_artifacts(run_id),
            "events": self.store.list_events(run_id),
        }

    def _active_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise EngineError(f"run not found: {run_id}")
        if run["status"] in TERMINAL_RUN_STATUSES:
            raise EngineError(f"terminal run cannot be changed: {run['status']}")
        return run
