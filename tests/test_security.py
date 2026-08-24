import tempfile
import unittest
from pathlib import Path

from cohub.db import CohubStore
from cohub.engine import EngineError, WorkflowEngine


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = CohubStore(root / "cohub.db")
        self.engine = WorkflowEngine(self.store, root / "artifacts")

    def test_approval_hash_is_order_independent(self):
        self.assertEqual(
            self.engine.approval_hash({"action": "publish", "target": "report"}),
            self.engine.approval_hash({"target": "report", "action": "publish"}),
        )

    def test_resolve_rejects_changed_payload_hash(self):
        workflow = {
            "name": "approval-hash", "start": "approve",
            "nodes": {
                "approve": {"type": "human", "payload": {"action": "publish", "target": "report-a"}, "next": "done"},
                "done": {"type": "end"},
            },
        }
        self.store.publish_workflow(workflow)
        run = self.engine.start_run(workflow["name"], {})
        approval = self.store.list_approvals(run["id"])[0]
        changed_hash = self.engine.approval_hash({"action": "publish", "target": "report-b"})
        with self.assertRaisesRegex(EngineError, "payload hash"):
            self.engine.resolve_approval(approval["id"], "approved", expected_payload_hash=changed_hash)
        self.assertEqual(self.store.list_approvals(run["id"])[0]["status"], "pending")

    def test_side_effect_step_requires_exact_approval_before_claim(self):
        workflow = {
            "name": "side-effect", "start": "publish",
            "nodes": {
                "publish": {
                    "type": "task", "side_effect": True,
                    "approval_payload": {"action": "publish", "target": "report"},
                    "next": "done",
                },
                "done": {"type": "end"},
            },
        }
        self.store.publish_workflow(workflow)
        run = self.engine.start_run(workflow["name"], {})
        self.assertEqual(self.store.get_run(run["id"])["status"], "waiting_for_human")
        self.assertIsNone(self.engine.claim_next(run["id"], "worker"))
        approval = self.store.list_approvals(run["id"])[0]
        self.engine.resolve_approval(approval["id"], "approved", expected_payload_hash=approval["payload_hash"])
        claim = self.engine.claim_next(run["id"], "worker")
        self.assertEqual(claim.step_id, "publish")

    def test_artifact_path_cannot_escape_root(self):
        safe = self.engine.artifact_path("run/report.md")
        self.assertTrue(str(safe).endswith("run/report.md"))
        with self.assertRaisesRegex(EngineError, "escapes"):
            self.engine.artifact_path("../secret.txt")


if __name__ == "__main__":
    unittest.main()
