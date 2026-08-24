import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cohub.db import CohubStore
from cohub.engine import WorkflowEngine
from cohub.executors import DurableStepExecutor, HermesApiError, HermesRunsExecutor, LocalExecutor
from cohub.models import StepResult
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


class ApprovalHermesHandler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, format, *args):
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.calls.append(("POST", self.path, body))
        if self.path == "/v1/runs":
            self._json(202, {"run_id": "hermes-approval-1", "status": "started"})
        elif self.path.endswith("/approval"):
            self._json(200, {"status": "resumed"})
        elif self.path.endswith("/stop"):
            self._json(200, {"status": "stopping"})
        else:
            self._json(404, {"error": "missing"})

    def do_GET(self):
        self.__class__.calls.append(("GET", self.path, None))
        if self.path == "/v1/capabilities":
            self._json(200, {"features": {"run_approval_response": True, "approval_events": True}})
        elif self.path.endswith("/events"):
            event = {
                "event": "approval.request",
                "run_id": "hermes-approval-1",
                "command": "docker compose restart",
                "reason": "service control",
                "choices": ["once", "session", "always", "deny"],
            }
            body = f"event: approval.request\ndata: {json.dumps(event)}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(200, {"run_id": "hermes-approval-1", "status": "waiting_for_approval", "last_event": "approval.request"})


class DurableHermesStub(DurableStepExecutor):
    provider = "hermes"
    timeout = 1.0
    poll_interval = 0.001

    def __init__(self, *, missing_event=False, state="waiting_for_approval", timeout=1.0, conflict_on_resolve=False):
        self.submit_count = 0
        self.resolve_calls = []
        self.stop_calls = []
        self.state = state
        self.missing_event = missing_event
        self.timeout = timeout
        self.conflict_on_resolve = conflict_on_resolve

    def require_approval_bridge(self):
        return None

    def submit(self, claimed):
        self.submit_count += 1
        return "hermes-durable-1"

    def get_status(self, external_run_id):
        if self.state == "completed":
            return {"status": "completed", "output": '{"output":{"deployed":true}}'}
        return {"status": self.state, "last_event": "approval.request"}

    def get_approval_event(self, external_run_id):
        if self.missing_event:
            raise RuntimeError("Hermes approval event could not be recovered")
        return {
            "event": "approval.request",
            "command": "docker compose restart",
            "reason": "service control",
            "choices": ["once", "session", "always", "deny"],
        }

    def resolve_approval(self, external_run_id, choice):
        self.resolve_calls.append((external_run_id, choice))
        self.state = "completed"
        if self.conflict_on_resolve:
            raise HermesApiError(409, "approval_not_pending")
        return {"status": "resumed"}

    def stop(self, external_run_id):
        self.stop_calls.append(external_run_id)
        self.state = "cancelled"
        return {"status": "stopping"}

    def parse_completed(self, status):
        return StepResult(output={"deployed": True})


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

    def test_hermes_client_bridges_approval_event_response_and_stop(self):
        ApprovalHermesHandler.calls = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), ApprovalHermesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        executor = HermesRunsExecutor(f"http://127.0.0.1:{server.server_port}", "secret", poll_interval=0.01)
        claimed = type("Claim", (), {
            "run_id": "run-1", "step_id": "deploy", "attempt": 1,
            "node": {"prompt": "Deploy"}, "task_input": {}, "dependency_outputs": {},
        })()

        executor.require_approval_bridge()
        external_id = executor.submit(claimed)
        status = executor.get_status(external_id)
        event = executor.get_approval_event(external_id)
        executor.resolve_approval(external_id, "once")
        executor.stop(external_id)

        self.assertEqual(status["status"], "waiting_for_approval")
        self.assertEqual(event["event"], "approval.request")
        self.assertEqual(event["command"], "docker compose restart")
        self.assertIn(("POST", "/v1/runs/hermes-approval-1/approval", {"choice": "once"}), ApprovalHermesHandler.calls)
        self.assertIn(("POST", "/v1/runs/hermes-approval-1/stop", {}), ApprovalHermesHandler.calls)

    def test_hermes_executor_preserves_failed_control_envelope_without_output(self):
        result = HermesRunsExecutor._parse_result({"status": "failed", "reason": "provider error"})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output, {})
        self.assertEqual(result.reason, "provider error")

    def test_hermes_executor_maps_enveloped_result(self):
        result = HermesRunsExecutor._parse_result(
            {"status": "completed", "output": {"report": "ok"}, "route": "approved", "reason": "done"}
        )
        self.assertEqual(result.output, {"report": "ok"})
        self.assertEqual(result.route, "approved")

    def test_hermes_executor_rejects_non_object_output_envelope(self):
        with self.assertRaisesRegex(RuntimeError, "must be an object"):
            HermesRunsExecutor._parse_result({"output": "not-a-dict"})

    def test_worker_persists_hermes_approval_and_resumes_same_external_run_after_restart(self):
        store = CohubStore(self.root / "cohub.db")
        engine = WorkflowEngine(store, self.root / "artifacts")
        workflow = {
            "name": "durable", "start": "deploy",
            "nodes": {
                "deploy": {"type": "task", "prompt": "Deploy", "next": "done"},
                "done": {"type": "end"},
            },
        }
        store.publish_workflow(workflow)
        run = engine.start_run("durable", {})
        executor = DurableHermesStub()
        worker = WorkerService(engine, executor, worker_id="worker-one")

        self.assertTrue(worker.tick(run["id"]))
        detail = engine.status(run["id"])
        self.assertEqual(detail["status"], "waiting_for_human")
        self.assertEqual(detail["steps"][0]["status"], "waiting_for_human")
        self.assertIsNone(detail["steps"][0]["lease_owner"])
        approval = detail["approvals"][0]
        self.assertEqual(approval["payload"]["kind"], "hermes_tool")
        self.assertEqual(approval["payload"]["external_run_id"], "hermes-durable-1")
        self.assertEqual(approval["payload"]["choices"], ["once", "deny"])

        restarted_engine = WorkflowEngine(CohubStore(self.root / "cohub.db"), self.root / "artifacts")
        restarted_worker = WorkerService(restarted_engine, executor, worker_id="worker-two")
        restarted_engine.resolve_approval(
            approval["id"], "approved", expected_payload_hash=approval["payload_hash"]
        )
        self.assertEqual(executor.resolve_calls, [("hermes-durable-1", "once")])
        self.assertTrue(restarted_worker.tick(run["id"]))
        self.assertEqual(restarted_engine.status(run["id"])["status"], "completed")
        self.assertEqual(executor.submit_count, 1)
        duplicate = restarted_engine.resolve_approval(
            approval["id"], "approved", expected_payload_hash=approval["payload_hash"]
        )
        self.assertEqual(duplicate["status"], "approved")
        self.assertEqual(executor.resolve_calls, [("hermes-durable-1", "once")])

    def test_rejecting_hermes_tool_approval_sends_deny_and_resumes_same_run(self):
        store = CohubStore(self.root / "deny.db")
        engine = WorkflowEngine(store, self.root / "deny-artifacts")
        store.publish_workflow({
            "name": "deny", "start": "work",
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        run = engine.start_run("deny", {})
        executor = DurableHermesStub()
        worker = WorkerService(engine, executor, worker_id="worker")
        worker.tick(run["id"])
        approval = engine.status(run["id"])["approvals"][0]

        engine.resolve_approval(
            approval["id"], "rejected", expected_payload_hash=approval["payload_hash"]
        )
        self.assertEqual(executor.resolve_calls, [("hermes-durable-1", "deny")])
        self.assertEqual(engine.status(run["id"])["status"], "running")
        self.assertTrue(worker.tick(run["id"]))
        self.assertEqual(engine.status(run["id"])["status"], "completed")

    def test_external_approval_409_is_idempotent_after_hermes_already_resumed(self):
        store = CohubStore(self.root / "conflict.db")
        engine = WorkflowEngine(store, self.root / "conflict-artifacts")
        store.publish_workflow({
            "name": "conflict", "start": "work",
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        run = engine.start_run("conflict", {})
        executor = DurableHermesStub(conflict_on_resolve=True)
        WorkerService(engine, executor, worker_id="worker").tick(run["id"])
        approval = engine.status(run["id"])["approvals"][0]

        resolved = engine.resolve_approval(
            approval["id"], "approved", expected_payload_hash=approval["payload_hash"]
        )
        self.assertEqual(resolved["status"], "approved")
        self.assertEqual(engine.status(run["id"])["status"], "running")

    def test_worker_fails_closed_and_stops_when_approval_event_is_missing(self):
        store = CohubStore(self.root / "missing.db")
        engine = WorkflowEngine(store, self.root / "missing-artifacts")
        store.publish_workflow({
            "name": "missing", "start": "work",
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        run = engine.start_run("missing", {})
        executor = DurableHermesStub(missing_event=True)
        worker = WorkerService(engine, executor, worker_id="worker")

        self.assertTrue(worker.tick(run["id"]))
        self.assertEqual(engine.status(run["id"])["status"], "failed")
        self.assertEqual(executor.stop_calls, ["hermes-durable-1"])

    def test_cancelling_cohub_run_stops_active_hermes_run(self):
        store = CohubStore(self.root / "cancel.db")
        engine = WorkflowEngine(store, self.root / "cancel-artifacts")
        store.publish_workflow({
            "name": "cancel", "start": "work",
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        run = engine.start_run("cancel", {})
        executor = DurableHermesStub()
        WorkerService(engine, executor, worker_id="worker").tick(run["id"])

        engine.cancel(run["id"])
        self.assertEqual(executor.stop_calls, ["hermes-durable-1"])
        external = store.get_external_execution(run["id"], "work", 1)
        self.assertEqual(external["status"], "stopping")

    def test_external_timeout_stops_hermes_before_failing_step(self):
        store = CohubStore(self.root / "timeout.db")
        engine = WorkflowEngine(store, self.root / "timeout-artifacts")
        store.publish_workflow({
            "name": "timeout", "start": "work", "defaults": {"max_attempts": 3},
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        run = engine.start_run("timeout", {})
        executor = DurableHermesStub(state="running", timeout=0.002)

        WorkerService(engine, executor, worker_id="worker").tick(run["id"])
        self.assertEqual(executor.stop_calls, ["hermes-durable-1"])
        detail = engine.status(run["id"])
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["steps"][0]["attempt"], 1)

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
