import tempfile
import unittest
from pathlib import Path

from cohub.db import CohubStore
from cohub.engine import WorkflowEngine
from cohub.executors import LocalExecutor
from cohub.service import WorkerService


class RestartResumeE2ETests(unittest.TestCase):
    def test_parallel_retry_artifact_approval_and_restart_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path, artifact_root = root / "cohub.db", root / "artifacts"
            workflow = {
                "name": "personal-daily-briefing",
                "description": "Collect sources in parallel, retry transient failure, draft, and approve delivery.",
                "start": "collect",
                "defaults": {"max_attempts": 2},
                "nodes": {
                    "collect": {"type": "parallel", "branches": ["calendar", "mail"], "next": "draft"},
                    "calendar": {"type": "task", "local_result": {"output": {"events": 3}}},
                    "mail": {"type": "task", "fail_until_attempt": 2, "local_result": {"output": {"important": 2}}},
                    "draft": {
                        "type": "task",
                        "local_result": {"output": {"report": "Daily briefing is ready"}},
                        "output_schema": {"type": "object", "required": ["report"]},
                        "artifact": {"path": "daily/briefing.md", "content": "# Daily briefing\n\nVerified.\n"},
                        "next": "deliver",
                    },
                    "deliver": {
                        "type": "task", "side_effect": True,
                        "approval_payload": {"action": "send_message", "channel": "telegram", "thread": "origin"},
                        "local_result": {"output": {"delivered": True}},
                        "next": "done",
                    },
                    "done": {"type": "end"},
                },
            }
            store = CohubStore(db_path)
            store.publish_workflow(workflow)
            engine = WorkflowEngine(store, artifact_root)
            worker = WorkerService(engine, LocalExecutor(artifact_root), worker_id="e2e-worker")
            run = engine.start_run(workflow["name"], {"date": "2026-08-24"}, title="Daily briefing")
            worker.drain(run["id"])
            status = engine.status(run["id"])
            self.assertEqual(status["status"], "waiting_for_human")
            self.assertEqual(status["steps"][2]["attempt"], 2)
            self.assertEqual(len(status["artifacts"]), 1)
            approval = status["approvals"][0]

            # Simulate a process restart by rebuilding every runtime object from disk.
            restarted_store = CohubStore(db_path)
            restarted_engine = WorkflowEngine(restarted_store, artifact_root)
            restarted_worker = WorkerService(restarted_engine, LocalExecutor(artifact_root), worker_id="restarted-worker")
            restarted_engine.resolve_approval(
                approval["id"], "approved", expected_payload_hash=approval["payload_hash"]
            )
            restarted_worker.drain(run["id"])
            final = restarted_engine.status(run["id"])
            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["step_count"], 6)
            self.assertEqual(restarted_store.get_task(final["task_id"])["status"], "completed")
            mail_step = next(step for step in final["steps"] if step["step_id"] == "mail")
            self.assertIsNone(mail_step["error"])
            self.assertEqual((artifact_root / "daily/briefing.md").read_text(), "# Daily briefing\n\nVerified.\n")
            self.assertEqual([event["seq"] for event in final["events"]], list(range(1, len(final["events"]) + 1)))
            self.assertEqual(final["approvals"][0]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
