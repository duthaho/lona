import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cohub.db import CohubStore
from cohub.engine import EngineError, WorkflowEngine
from cohub.models import StepResult


class WorkflowEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CohubStore(Path(self.temp.name) / "cohub.db")
        self.engine = WorkflowEngine(self.store, Path(self.temp.name) / "artifacts")

    def start(self, workflow):
        self.store.publish_workflow(workflow)
        return self.engine.start_run(workflow["name"], {"request": "test"}, title="Test run")

    def test_start_schedules_only_start_node_and_task_completion_advances(self):
        run = self.start({
            "name": "linear", "start": "first",
            "nodes": {
                "first": {"type": "task", "next": "second"},
                "second": {"type": "task", "next": "done"},
                "done": {"type": "end"},
            },
        })
        self.assertEqual([(s["step_id"], s["status"]) for s in self.store.get_steps(run["id"])], [("first", "ready")])
        self.engine.complete_step(run["id"], "first", StepResult(output={"ok": True}))
        self.assertEqual(self.store.get_step(run["id"], "second")["status"], "ready")

    def test_decision_rejects_undeclared_route_and_accepts_declared_route(self):
        run = self.start({
            "name": "decision", "start": "choose",
            "nodes": {
                "choose": {"type": "decision", "routes": {"yes": "done", "no": "stop"}},
                "done": {"type": "end"}, "stop": {"type": "end"},
            },
        })
        with self.assertRaisesRegex(EngineError, "undeclared route"):
            self.engine.complete_step(run["id"], "choose", StepResult(route="maybe", output={}))
        self.engine.complete_step(run["id"], "choose", StepResult(route="yes", reason="verified", output={}))
        self.assertEqual(self.store.get_run(run["id"])["status"], "completed")
        self.assertEqual(self.store.get_step(run["id"], "done")["status"], "completed")

    def test_parallel_fans_out_and_converges_after_all_direct_branches(self):
        run = self.start({
            "name": "parallel", "start": "fanout",
            "nodes": {
                "fanout": {"type": "parallel", "branches": ["left", "right"], "next": "merge"},
                "left": {"type": "task"}, "right": {"type": "task"},
                "merge": {"type": "task", "next": "done"}, "done": {"type": "end"},
            },
        })
        self.assertEqual({s["step_id"] for s in self.store.get_steps(run["id"]) if s["status"] == "ready"}, {"left", "right"})
        self.engine.complete_step(run["id"], "left", StepResult(output={}))
        self.assertIsNone(self.store.get_step(run["id"], "merge"))
        self.engine.complete_step(run["id"], "right", StepResult(output={}))
        self.assertEqual(self.store.get_step(run["id"], "merge")["status"], "ready")

    def test_human_node_waits_for_approval_then_resumes(self):
        run = self.start({
            "name": "human", "start": "approve",
            "nodes": {
                "approve": {"type": "human", "prompt": "Publish report?", "payload": {"action": "publish"}, "next": "done"},
                "done": {"type": "end"},
            },
        })
        approval = self.store.list_approvals(run["id"])[0]
        self.assertEqual(self.store.get_run(run["id"])["status"], "waiting_for_human")
        self.engine.resolve_approval(approval["id"], "approved")
        self.assertEqual(self.store.get_run(run["id"])["status"], "completed")

    def test_output_schema_failure_does_not_complete_step(self):
        run = self.start({
            "name": "schema", "start": "work",
            "nodes": {
                "work": {"type": "task", "output_schema": {"type": "object", "required": ["report"], "properties": {"report": {"type": "string"}}}, "next": "done"},
                "done": {"type": "end"},
            },
        })
        with self.assertRaisesRegex(EngineError, "required property"):
            self.engine.complete_step(run["id"], "work", StepResult(output={}))
        self.assertNotEqual(self.store.get_step(run["id"], "work")["status"], "completed")

    def test_retry_exhaustion_fails_run(self):
        run = self.start({
            "name": "retry", "start": "work", "defaults": {"max_attempts": 2},
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        self.engine.fail_step(run["id"], "work", "temporary")
        self.assertEqual(self.store.get_step(run["id"], "work")["status"], "ready")
        self.engine.fail_step(run["id"], "work", "permanent")
        self.assertEqual(self.store.get_run(run["id"])["status"], "failed")

    def test_claim_lease_prevents_duplicates_and_expired_lease_is_reclaimed(self):
        run = self.start({
            "name": "lease", "start": "work",
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        first = self.engine.claim_next(run["id"], "worker-a", lease_seconds=60)
        self.assertEqual(first.step_id, "work")
        self.assertIsNone(self.engine.claim_next(run["id"], "worker-b", lease_seconds=60))
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        self.store.upsert_step(run["id"], "work", "leased", lease_expires_at=expired)
        reclaimed = self.engine.claim_next(run["id"], "worker-b", lease_seconds=60)
        self.assertEqual(reclaimed.lease_owner, "worker-b")

    def test_budget_stops_run_before_activating_too_many_steps(self):
        workflow = {
            "name": "step-budget",
            "start": "draft",
            "budget": {"max_steps": 1},
            "nodes": {"draft": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        }
        run = self.start(workflow)
        self.engine.complete_step(run["id"], "draft", StepResult(output={"report": "ok"}))
        self.assertEqual(self.store.get_run(run["id"])["status"], "failed")
        self.assertIn("max_steps", self.store.get_run(run["id"])["error"])

    def test_duration_budget_stops_claim_after_deadline(self):
        workflow = {
            "name": "duration-budget",
            "start": "draft",
            "budget": {"max_duration_seconds": 0},
            "nodes": {"draft": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        }
        run = self.start(workflow)
        self.assertIsNone(self.engine.claim_next(run["id"], "worker"))
        self.assertEqual(self.store.get_run(run["id"])["status"], "failed")

    def test_pause_resume_and_cancel_enforce_transitions(self):
        run = self.start({
            "name": "control", "start": "work",
            "nodes": {"work": {"type": "task", "next": "done"}, "done": {"type": "end"}},
        })
        self.engine.pause(run["id"])
        self.assertEqual(self.store.get_run(run["id"])["status"], "paused")
        self.engine.resume(run["id"])
        self.assertEqual(self.store.get_run(run["id"])["status"], "running")
        self.engine.cancel(run["id"])
        self.assertEqual(self.store.get_run(run["id"])["status"], "cancelled")
        with self.assertRaisesRegex(EngineError, "terminal"):
            self.engine.resume(run["id"])


if __name__ == "__main__":
    unittest.main()
