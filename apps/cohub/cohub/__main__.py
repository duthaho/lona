"""Command-line entry point for the Cohub dashboard and local demo."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .api import create_server
from .db import CohubStore
from .engine import WorkflowEngine
from .executors import HermesRunsExecutor, LocalExecutor
from .service import WorkerService


def build_runtime(data_dir: Path, executor_name: str = "local") -> tuple[CohubStore, WorkflowEngine, WorkerService]:
    data_dir.mkdir(parents=True, exist_ok=True)
    store = CohubStore(data_dir / "cohub.db")
    engine = WorkflowEngine(store, data_dir / "artifacts")
    if executor_name == "hermes":
        base_url = os.environ.get("HERMES_API_BASE", "")
        api_key = os.environ.get("HERMES_API_KEY", "")
        if not base_url or not api_key:
            raise SystemExit("HERMES_API_BASE and HERMES_API_KEY are required for the Hermes executor")
        executor = HermesRunsExecutor(base_url, api_key)
    else:
        executor = LocalExecutor(data_dir / "artifacts")
    return store, engine, WorkerService(engine, executor)


def seed(store: CohubStore, path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        workflow = json.load(handle)
    return store.publish_workflow(workflow)


def main() -> None:
    parser = argparse.ArgumentParser(prog="cohub", description="Personal coworker dashboard and deterministic workflow engine")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".hermes" / "cohub")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Start the local dashboard and API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--executor", choices=("local", "hermes"), default="local")
    serve_parser.add_argument("--api-token", default=os.environ.get("COHUB_API_TOKEN", ""))

    seed_parser = subparsers.add_parser("seed", help="Publish a workflow JSON file")
    seed_parser.add_argument("workflow", type=Path)

    demo_parser = subparsers.add_parser("demo", help="Run the bundled deterministic workflow until approval")
    demo_parser.add_argument("--approve", action="store_true", help="Approve and finish the protected delivery step")

    args = parser.parse_args()
    store, engine, worker = build_runtime(args.data_dir, getattr(args, "executor", "local"))
    if args.command == "seed":
        print(json.dumps(seed(store, args.workflow), indent=2))
        return
    if args.command == "demo":
        sample = Path(__file__).resolve().parent / "examples" / "personal-daily-briefing.json"
        workflow = seed(store, sample)
        run = engine.start_run(workflow["name"], {"source": "cli-demo"}, title="Personal daily briefing")
        worker.drain(run["id"])
        status = engine.status(run["id"])
        if args.approve and status["approvals"]:
            approval = status["approvals"][0]
            engine.resolve_approval(approval["id"], "approved", expected_payload_hash=approval["payload_hash"])
            worker.drain(run["id"])
            status = engine.status(run["id"])
        print(json.dumps(status, indent=2))
        return
    static_dir = Path(__file__).resolve().parent / "static"
    server = create_server(
        args.host,
        args.port,
        store,
        engine,
        worker,
        static_dir=static_dir,
        api_token=args.api_token,
        model_catalog=getattr(worker.executor, "get_model_catalog", None),
    )
    print(f"Cohub dashboard: http://{args.host}:{server.server_port}")
    print(f"Data directory: {args.data_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
