import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from cohub.api import create_server
from cohub.db import CohubStore
from cohub.engine import WorkflowEngine
from cohub.executors import LocalExecutor
from cohub.service import WorkerService


WORKFLOW = {
    "name": "api-demo", "start": "draft",
    "nodes": {
        "draft": {
            "type": "task",
            "local_result": {"output": {"report": "ready"}},
            "artifact": {"path": "demo/report.md", "content": "# Ready\n"},
            "next": "approve",
        },
        "approve": {"type": "human", "payload": {"action": "deliver", "target": "telegram"}, "next": "done"},
        "done": {"type": "end"},
    },
}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        store = CohubStore(root / "cohub.db")
        engine = WorkflowEngine(store, root / "artifacts")
        worker = WorkerService(engine, LocalExecutor(root / "artifacts"), worker_id="api-worker")
        self.server = create_server(
            "127.0.0.1",
            0,
            store,
            engine,
            worker,
            static_dir=Path("cohub/static"),
            api_token="test-token",
            model_catalog=lambda refresh=False: {
                "current": {"provider": "openrouter", "model": "free/default"},
                "providers": [
                    {
                        "provider": "openai-codex",
                        "label": "OpenAI Codex",
                        "models": [{"id": "gpt-5.6", "featured": True}],
                    }
                ],
            },
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, method, path, body=None, token: str | None = "test-token"):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return response.status, json.load(response)
            return response.status, response.read().decode()

    def test_auth_and_health(self):
        status, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("GET", "/api/health", token="wrong")
        self.assertEqual(context.exception.code, 401)

    def test_publish_start_tick_approve_and_read_run(self):
        status, workflow = self.request("POST", "/api/workflows", WORKFLOW)
        self.assertEqual(status, 201)
        self.assertEqual(workflow["version"], 1)
        status, run = self.request("POST", "/api/runs", {"workflow": "api-demo", "title": "API demo", "input": {}})
        self.assertEqual(status, 201)
        self.assertEqual(run["title"], "API demo")
        run_id = run["id"]
        status, tick = self.request("POST", f"/api/runs/{run_id}/tick", {})
        self.assertEqual(status, 200)
        self.assertTrue(tick["worked"])
        status, detail = self.request("GET", f"/api/runs/{run_id}")
        self.assertEqual(detail["title"], "API demo")
        self.assertEqual(detail["status"], "waiting_for_human")
        approval = detail["approvals"][0]
        status, _ = self.request("POST", f"/api/approvals/{approval['id']}/approve", {"payload_hash": approval["payload_hash"]})
        self.assertEqual(status, 200)
        status, detail = self.request("GET", f"/api/runs/{run_id}")
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(len(detail["artifacts"]), 1)

    def test_lists_models_and_persists_an_explicit_run_selection(self):
        self.request("POST", "/api/workflows", WORKFLOW)
        status, catalog = self.request("GET", "/api/hermes/models")
        self.assertEqual(status, 200)
        self.assertEqual(catalog["providers"][0]["provider"], "openai-codex")

        status, run = self.request(
            "POST",
            "/api/runs",
            {
                "workflow": "api-demo",
                "title": "Paid model run",
                "input": {},
                "provider": "openai-codex",
                "model": "gpt-5.6",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["requested_provider"], "openai-codex")
        self.assertEqual(run["requested_model"], "gpt-5.6")

        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request(
                "POST",
                "/api/runs",
                {"workflow": "api-demo", "input": {}, "provider": "openai-codex", "model": "missing"},
            )
        self.assertEqual(context.exception.code, 400)

    def test_overview_and_static_dashboard(self):
        self.request("POST", "/api/workflows", WORKFLOW)
        self.request("POST", "/api/runs", {"workflow": "api-demo", "input": {}})
        status, overview = self.request("GET", "/api/overview")
        self.assertEqual(status, 200)
        self.assertEqual(overview["counts"]["running"], 1)
        status, html = self.request("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn("Cohub", html)
        self.assertIn('id="root"', html)
        asset = re.search(r'<script[^>]+src="([^"]+\.js)"', html)
        if asset is None:
            self.fail("dashboard must include a bundled JavaScript asset")
        status, javascript = self.request("GET", asset.group(1), token=None)
        self.assertEqual(status, 200)
        self.assertIn("Personal coworker", javascript)
        self.assertEqual(overview["counts"]["running"], 1)

    def test_rejects_invalid_json_and_unknown_route(self):
        request = urllib.request.Request(
            self.base + "/api/workflows",
            data=b"not-json",
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(context.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("GET", "/api/missing")
        self.assertEqual(context.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
