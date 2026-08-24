"""Bounded worker service that reconciles executor results into the engine."""

from __future__ import annotations

import logging
import uuid

from .engine import EngineError, WorkflowEngine
from .executors import StepExecutor

logger = logging.getLogger(__name__)


class WorkerService:
    def __init__(self, engine: WorkflowEngine, executor: StepExecutor, *, worker_id: str | None = None):
        self.engine = engine
        self.executor = executor
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"

    def tick(self, run_id: str) -> bool:
        """Execute at most one ready step. Return whether work was claimed."""

        claimed = self.engine.claim_next(run_id, self.worker_id)
        if claimed is None:
            return False
        try:
            result = self.executor.execute(claimed)
            if result.status != "completed":
                raise RuntimeError(f"executor returned unsupported status: {result.status}")
            self.engine.complete_step(run_id, claimed.step_id, result)
        except Exception as exc:
            logger.warning("Step %s failed: %s", claimed.step_id, exc)
            try:
                self.engine.fail_step(run_id, claimed.step_id, str(exc))
            except EngineError:
                logger.exception("Failed to reconcile step failure")
                raise
        return True

    def drain(self, run_id: str, *, max_ticks: int = 100) -> int:
        """Run ready work until blocked, terminal, or the safety bound is reached."""

        completed = 0
        while completed < max_ticks and self.tick(run_id):
            completed += 1
            status = self.engine.store.get_run(run_id)
            if status and status["status"] in {"completed", "failed", "cancelled", "waiting_for_human", "paused"}:
                break
        return completed
