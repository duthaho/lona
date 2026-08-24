"""Hermes Agent plugin registration and model-facing tool handlers."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .db import CohubStore
from .engine import WorkflowEngine
from .executors import HermesRunsExecutor, LocalExecutor
from .service import WorkerService


@dataclass
class Runtime:
    store: CohubStore
    engine: WorkflowEngine
    worker: WorkerService


_runtime: Runtime | None = None
_runtime_lock = threading.Lock()


def reset_runtime() -> None:
    global _runtime
    with _runtime_lock:
        _runtime = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            data_dir = Path(os.environ.get("COHUB_DATA_DIR", Path.home() / ".hermes" / "cohub")).expanduser()
            data_dir.mkdir(parents=True, exist_ok=True)
            store = CohubStore(data_dir / "cohub.db")
            engine = WorkflowEngine(store, data_dir / "artifacts")
            base_url = os.environ.get("HERMES_API_BASE", "")
            api_key = os.environ.get("HERMES_API_KEY", "")
            executor = HermesRunsExecutor(base_url, api_key) if base_url and api_key else LocalExecutor(data_dir / "artifacts")
            _runtime = Runtime(store, engine, WorkerService(engine, executor))
        return _runtime


def _handler(operation: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[..., str]:
    def wrapped(args: dict[str, Any], **kwargs: Any) -> str:
        del kwargs
        try:
            return json.dumps({"success": True, **operation(args)}, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":"))
    return wrapped


PUBLISH_WORKFLOW = {
    "name": "cohub_publish_workflow",
    "description": "Validate and publish an immutable deterministic Cohub workflow definition. Use before starting a newly designed workflow.",
    "parameters": {"type": "object", "properties": {"workflow": {"type": "object", "description": "Complete workflow definition"}}, "required": ["workflow"]},
}
START_RUN = {
    "name": "cohub_start_run",
    "description": "Create a task and start a run pinned to a published Cohub workflow version.",
    "parameters": {"type": "object", "properties": {"workflow": {"type": "string"}, "title": {"type": "string"}, "input": {"type": "object"}, "version": {"type": "integer"}}, "required": ["workflow"]},
}
RUN_STATUS = {
    "name": "cohub_run_status",
    "description": "Read a Cohub run with its steps, approvals, artifacts, and append-only event trace.",
    "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
}
TICK_RUN = {
    "name": "cohub_tick_run",
    "description": "Claim and execute at most one ready step for a Cohub run. Returns false when the run is blocked or terminal.",
    "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
}
RESOLVE_APPROVAL = {
    "name": "cohub_resolve_approval",
    "description": "Approve or reject an exact protected-action payload using the payload SHA-256 shown to the user.",
    "parameters": {
        "type": "object",
        "properties": {
            "approval_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["approved", "rejected"]},
            "payload_hash": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["approval_id", "decision", "payload_hash"],
    },
}


def register(ctx: Any) -> None:
    """Register Cohub's deterministic workflow tools with Hermes."""

    ctx.register_tool(
        name="cohub_publish_workflow", toolset="cohub", schema=PUBLISH_WORKFLOW,
        handler=_handler(lambda args: {"workflow": get_runtime().store.publish_workflow(args["workflow"])}),
    )
    ctx.register_tool(
        name="cohub_start_run", toolset="cohub", schema=START_RUN,
        handler=_handler(lambda args: {"run": get_runtime().engine.start_run(args["workflow"], args.get("input", {}), title=args.get("title"), version=args.get("version"))}),
    )
    ctx.register_tool(
        name="cohub_run_status", toolset="cohub", schema=RUN_STATUS,
        handler=_handler(lambda args: {"run": get_runtime().engine.status(args["run_id"])}),
    )
    ctx.register_tool(
        name="cohub_tick_run", toolset="cohub", schema=TICK_RUN,
        handler=_handler(lambda args: {"worked": get_runtime().worker.tick(args["run_id"]), "run": get_runtime().engine.status(args["run_id"])}),
    )
    ctx.register_tool(
        name="cohub_resolve_approval", toolset="cohub", schema=RESOLVE_APPROVAL,
        handler=_handler(lambda args: {"approval": get_runtime().engine.resolve_approval(args["approval_id"], args["decision"], args.get("note"), expected_payload_hash=args["payload_hash"])}),
    )
