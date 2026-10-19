"""Seed representative UI data and start a disposable Cohub server."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cohub.api import create_server
from cohub.__main__ import build_runtime
from cohub.generation import WorkflowGenerationService


WORKFLOW = {
    "name": "release-briefing",
    "description": "Prepare and review a release briefing.",
    "start": "draft",
    "nodes": {
        "draft": {
            "type": "task",
            "local_result": {"output": {"briefing": "ready"}},
            "artifact": {"path": "briefing/report.md", "content": "# Release ready\n"},
            "next": "approve",
        },
        "approve": {
            "type": "human",
            "payload": {"action": "deliver_briefing", "target": "telegram"},
            "next": "done",
        },
        "done": {"type": "end"},
    },
}

MODEL_CATALOG = {
    "current": {"provider": "openrouter", "model": "free/default"},
    "providers": [
        {
            "provider": "openai-codex",
            "label": "OpenAI Codex",
            "models": [{"id": "gpt-5.6", "featured": True}],
        }
    ],
}


class UiWorkflowGenerator:
    def submit(self, **kwargs):
        return f"ui-designer-{kwargs['generation_id']}"

    def get_status(self, external_run_id):
        return {
            "status": "completed",
            "output": json.dumps({
                "workflow": {
                    "name": "generated-briefing",
                    "description": "Generated safely for review.",
                    "start": "prepare",
                    "nodes": {
                        "prepare": {"type": "task", "prompt": "Prepare the briefing", "next": "review"},
                        "review": {"type": "human", "payload": {"action": "review"}, "next": "done"},
                        "done": {"type": "end"},
                    },
                },
                "assumptions": ["The run supplies source notes"],
            }),
        }

    def stop(self, external_run_id):
        return {"status": "stopping"}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cohub-ui-") as temporary:
        store, engine, worker = build_runtime(Path(temporary), "local")
        store.publish_workflow(WORKFLOW)
        waiting = engine.start_run("release-briefing", {"release": "2026.08"}, title="Review the release briefing")
        worker.drain(waiting["id"])
        completed = engine.start_run("release-briefing", {}, title="Previous release briefing")
        worker.drain(completed["id"])
        approval = engine.status(completed["id"])["approvals"][0]
        engine.resolve_approval(approval["id"], "approved", expected_payload_hash=approval["payload_hash"])
        static_dir = Path(__file__).resolve().parents[1] / "cohub" / "static"
        server = create_server(
            "127.0.0.1",
            18765,
            store,
            engine,
            worker,
            static_dir=static_dir,
            model_catalog=lambda refresh=False: MODEL_CATALOG,
            workflow_generation=WorkflowGenerationService(store, UiWorkflowGenerator()),
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
