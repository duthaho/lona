import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cohub.db import CohubStore
from cohub.engine import WorkflowEngine
from cohub.executors import HermesRunsExecutor, LocalExecutor
from cohub.service import WorkerService


class FakeHermesHandler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, format, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.__class__.calls.append((self.path, body, self.headers.get("Idempotency-Key")))
        payload = json.dumps({"run_id": "hermes-1", "status": "started"}).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        payload = json.dumps({
            "run_id": "hermes-1", "status": "completed",
            "output": json.dumps({"status": "completed", "output": {"report": "done"}, "route": "approved", "reason": "verified"}),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_local_executor_returns_structured_result_and_artifact(self):
        executor = LocalExecutor(self.root)
        claimed = type("Claim", (), {
            "run_id": "run-1", "step_id": "draft", "attempt": 1,
            "node": {
                "local_result": {"output": {"report": "ready"}, "route": "approved"},
                "artifact": {"path": "run-1/report.md", "content": "# Report\n"},
            },
        })()
        result = executor.execute(claimed)
        self.assertEqual(result.output, {"report": "ready"})
        self.assertEqual(result.route, "approved")
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual((self.root / "run-1/report.md").read_text(), "# Report\n")

    def test_local_executor_can_fail_until_configured_attempt(self):
        executor = LocalExecutor(self.root)
        claimed = type("Claim", (), {"attempt": 1, "node": {"fail_until_attempt": 2}, "run_id": "r", "step_id": "s"})()
        with self.assertRaisesRegex(RuntimeError, "planned failure"):
            executor.execute(claimed)

    def test_hermes_executor_submits_correlated_run_and_maps_result(self):
        FakeHermesHandler.calls = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHermesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        executor = HermesRunsExecutor(f"http://127.0.0.1:{server.server_port}", "secret", poll_interval=0.01)
        claimed = type("Claim", (), {
            "run_id": "run-1", "step_id": "review", "attempt": 1,
            "node": {"prompt": "Review the report", "routes": {"approved": "done"}},
            "task_input": {"topic": "weekly"}, "dependency_outputs": {"draft": {"report": "text"}},
        })()
        result = executor.execute(claimed)
        self.assertEqual(result.output, {"report": "done"})
        self.assertEqual(result.route, "approved")
        self.assertEqual(FakeHermesHandler.calls[0][0], "/v1/runs")
        self.assertEqual(FakeHermesHandler.calls[0][1]["session_id"], "run-1:review:1")
        self.assertIsNone(FakeHermesHandler.calls[0][2])
        self.assertIn("Return exactly one JSON object", FakeHermesHandler.calls[0][1]["instructions"])

    def test_hermes_executor_accepts_bare_output_object(self):
        result = HermesRunsExecutor._parse_result('{"answer": 5}')
        self.assertEqual(result.output, {"answer": 5})
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.route)

    def test_hermes_executor_maps_enveloped_result(self):
        result = HermesRunsExecutor._parse_result(
            {"status": "completed", "output": {"report": "ok"}, "route": "approved", "reason": "done"}
        )
        self.assertEqual(result.output, {"report": "ok"})
        self.assertEqual(result.route, "approved")

    def test_hermes_executor_rejects_non_object_output_envelope(self):
        with self.assertRaisesRegex(RuntimeError, "must be an object"):
            HermesRunsExecutor._parse_result({"output": "not-a-dict"})

    def test_worker_claims_executes_and_completes_ready_step(self):
        store = CohubStore(self.root / "cohub.db")
        engine = WorkflowEngine(store, self.root / "artifacts")
        workflow = {
            "name": "worker", "start": "work",
            "nodes": {
                "work": {"type": "task", "local_result": {"output": {"ok": True}}, "next": "done"},
                "done": {"type": "end"},
            },
        }
        store.publish_workflow(workflow)
        run = engine.start_run("worker", {})
        worker = WorkerService(engine, LocalExecutor(self.root / "artifacts"), worker_id="test-worker")
        self.assertTrue(worker.tick(run["id"]))
        self.assertEqual(store.get_run(run["id"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
