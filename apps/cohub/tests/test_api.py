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
from cohub.generation import WorkflowGenerationService
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


class FakeWorkflowGenerator:
    def __init__(self):
        self.submit_count = 0
        self.stop_calls = []
        self.status = {
            "status": "completed",
            "output": json.dumps({
                "workflow": {
                    "name": "generated-api",
                    "start": "draft",
                    "nodes": {
                        "draft": {"type": "task", "prompt": "Draft report", "next": "done"},
                        "done": {"type": "end"},
                    },
                },
                "assumptions": ["Inputs arrive with the run"],
            }),
        }

    def submit(self, **kwargs):
        self.submit_count += 1
        return "designer-api-1"

    def get_status(self, external_run_id):
        return self.status

    def stop(self, external_run_id):
        self.stop_calls.append(external_run_id)
        return {"status": "stopping"}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        store = CohubStore(root / "cohub.db")
        self.store = store
        engine = WorkflowEngine(store, root / "artifacts")
        worker = WorkerService(engine, LocalExecutor(root / "artifacts"), worker_id="api-worker")
        self.generator = FakeWorkflowGenerator()
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
            workflow_generation=WorkflowGenerationService(store, self.generator),
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

    def test_workflow_draft_lifecycle_and_revision_conflict(self):
        status, draft = self.request(
            "POST",
            "/api/workflow-drafts",
            {"definition": {"name": "draft-api", "start": "missing", "nodes": {}}, "layout": {}},
        )
        self.assertEqual(status, 201)
        self.assertEqual(draft["revision"], 1)

        status, validation = self.request("POST", f"/api/workflow-drafts/{draft['id']}/validate", {})
        self.assertEqual(status, 200)
        self.assertFalse(validation["valid"])
        self.assertEqual(validation["diagnostics"][0]["path"], "nodes")

        definition = {**WORKFLOW, "name": "draft-api"}
        status, saved = self.request(
            "PUT",
            f"/api/workflow-drafts/{draft['id']}",
            {"revision": 1, "definition": definition},
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["revision"], 2)

        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request(
                "PUT",
                f"/api/workflow-drafts/{draft['id']}",
                {"revision": 1, "definition": {**definition, "description": "stale"}},
            )
        self.assertEqual(context.exception.code, 409)

        status, validation = self.request("POST", f"/api/workflow-drafts/{draft['id']}/validate", {})
        self.assertTrue(validation["valid"])
        status, published = self.request(
            "POST", f"/api/workflow-drafts/{draft['id']}/publish", {"revision": 2}
        )
        self.assertEqual(status, 201)
        self.assertEqual(published["draft"]["status"], "published")
        self.assertEqual(published["workflow"]["name"], "draft-api")

        status, listing = self.request("GET", "/api/workflow-drafts")
        self.assertEqual(listing["drafts"][0]["id"], draft["id"])
        status, reopened = self.request("GET", f"/api/workflow-drafts/{draft['id']}")
        self.assertEqual(reopened["revision"], 3)

    def test_duplicates_a_published_workflow_into_a_draft(self):
        _, workflow = self.request("POST", "/api/workflows", WORKFLOW)
        status, draft = self.request(
            "POST", "/api/workflow-drafts", {"workflow_name": "api-demo", "version": 1}
        )
        self.assertEqual(status, 201)
        self.assertEqual(draft["source_workflow_id"], workflow["id"])
        self.assertEqual(draft["definition"], workflow["definition"])

    def test_generates_and_revises_a_draft_without_auto_publish_or_duplicate_submission(self):
        _, draft = self.request(
            "POST",
            "/api/workflow-drafts",
            {"definition": {"name": "generated-api", "start": "done", "nodes": {"done": {"type": "end"}}}, "layout": {}},
        )
        status, generation = self.request(
            "POST",
            f"/api/workflow-drafts/{draft['id']}/generate",
            {
                "revision": 1,
                "prompt": "Create a reviewed report workflow",
                "provider": "openai-codex",
                "model": "gpt-5.6",
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(generation["status"], "running")
        self.assertEqual(self.generator.submit_count, 1)

        status, candidate = self.request("GET", f"/api/workflow-generations/{generation['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(candidate["status"], "candidate_ready")
        self.assertEqual(candidate["candidate"]["name"], "generated-api")
        self.assertEqual(candidate["assumptions"], ["Inputs arrive with the run"])
        _, unchanged = self.request("GET", f"/api/workflow-drafts/{draft['id']}")
        self.assertEqual(unchanged["revision"], 1)
        self.assertEqual(unchanged["definition"]["start"], "done")
        self.assertEqual(unchanged["status"], "active")
        self.assertEqual(self.generator.submit_count, 1)

        status, accepted = self.request(
            "POST",
            f"/api/workflow-generations/{generation['id']}/accept",
            {"revision": 1},
        )
        self.assertEqual(status, 200)
        self.assertEqual(accepted["generation"]["status"], "completed")
        self.assertEqual(accepted["draft"]["revision"], 2)
        self.assertEqual(accepted["draft"]["definition"]["start"], "draft")

        _, repeated = self.request("GET", f"/api/workflow-generations/{generation['id']}")
        self.assertEqual(repeated["result_revision"], 2)
        self.assertEqual(self.generator.submit_count, 1)
        _, listing = self.request("GET", f"/api/workflow-drafts/{draft['id']}/generations")
        self.assertEqual(listing["generations"][0]["id"], generation["id"])

    def test_generation_fails_closed_when_hermes_requests_approval(self):
        self.generator.status = {"status": "waiting_for_approval"}
        _, draft = self.request(
            "POST",
            "/api/workflow-drafts",
            {"definition": {"name": "unsafe-generation", "start": "done", "nodes": {"done": {"type": "end"}}}, "layout": {}},
        )
        _, generation = self.request(
            "POST",
            f"/api/workflow-drafts/{draft['id']}/revise",
            {"revision": 1, "prompt": "Add an approval"},
        )
        _, failed = self.request("GET", f"/api/workflow-generations/{generation['id']}")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("requiring approval", failed["error"])
        self.assertEqual(self.generator.stop_calls, ["designer-api-1"])
        self.assertEqual(self.store.get_workflow_draft(draft["id"])["revision"], 1)

    def test_templates_input_forms_revisions_simulation_and_analytics(self):
        status, templates = self.request("GET", "/api/workflow-templates")
        self.assertEqual(status, 200)
        self.assertEqual(len(templates["templates"]), 4)
        _, draft = self.request("POST", "/api/workflow-drafts", {"template_id": "daily-briefing"})
        self.assertEqual(draft["definition"]["input_schema"]["properties"]["date"]["type"], "string")
        status, simulation = self.request(
            "POST", f"/api/workflow-drafts/{draft['id']}/test-run",
            {"revision": 1, "input": {"date": "2026-10-18"}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(simulation["side_effect_adapters_invoked"] is False)
        self.assertTrue(any(step["approval_required"] for step in simulation["steps"]))
        _, changed = self.request(
            "PUT", f"/api/workflow-drafts/{draft['id']}",
            {"revision": 1, "definition": {**draft["definition"], "description": "changed"}, "layout": {}},
        )
        _, revisions = self.request("GET", f"/api/workflow-drafts/{draft['id']}/revisions")
        self.assertEqual([item["revision"] for item in revisions["revisions"]], [2, 1])
        _, restored = self.request(
            "POST", f"/api/workflow-drafts/{draft['id']}/restore",
            {"revision": changed["revision"], "target_revision": 1},
        )
        self.assertEqual(restored["revision"], 3)
        status, analytics = self.request("GET", "/api/analytics")
        self.assertEqual(status, 200)
        self.assertEqual(analytics["models"], [])


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
