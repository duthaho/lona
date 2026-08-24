import sqlite3
import tempfile
import unittest
from pathlib import Path

from cohub.db import CohubStore


WORKFLOW = {
    "name": "simple",
    "start": "work",
    "nodes": {
        "work": {"type": "task", "next": "done"},
        "done": {"type": "end"},
    },
}


class CohubStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CohubStore(Path(self.temp.name) / "cohub.db")

    def test_connection_context_closes_connection(self):
        with self.store.connection() as connection:
            connection.execute("SELECT 1").fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_publish_is_immutable_and_deduplicates_identical_definition(self):
        first = self.store.publish_workflow(WORKFLOW)
        duplicate = self.store.publish_workflow(WORKFLOW)
        changed = {**WORKFLOW, "description": "changed"}
        second = self.store.publish_workflow(changed)

        self.assertEqual(first["version"], 1)
        self.assertEqual(duplicate["id"], first["id"])
        self.assertEqual(second["version"], 2)
        self.assertNotEqual(second["fingerprint"], first["fingerprint"])
        self.assertEqual(len(self.store.list_workflows()), 2)

    def test_creates_task_and_run_pinned_to_workflow_version(self):
        published = self.store.publish_workflow(WORKFLOW)
        task = self.store.create_task("Build report", {"topic": "weekly"})
        run = self.store.create_run(task["id"], published, {"topic": "weekly"})

        loaded = self.store.get_run(run["id"])
        self.assertEqual(loaded["workflow_version"], 1)
        self.assertEqual(loaded["workflow_fingerprint"], published["fingerprint"])
        self.assertEqual(loaded["input"], {"topic": "weekly"})

    def test_state_change_and_event_are_atomic_and_sequence_is_monotonic(self):
        published = self.store.publish_workflow(WORKFLOW)
        task = self.store.create_task("Atomic run")
        run = self.store.create_run(task["id"], published, {})
        self.store.transition_run(run["id"], "running", "run.started", {"source": "test"})
        self.store.transition_run(run["id"], "paused", "run.paused", {})
        events = self.store.list_events(run["id"])
        self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[-1]["type"], "run.paused")

        with self.assertRaises(RuntimeError):
            self.store.transition_run(run["id"], "failed", "run.failed", {}, fail_after_state=True)
        self.assertEqual(self.store.get_run(run["id"])["status"], "paused")
        self.assertEqual(self.store.list_events(run["id"])[-1]["type"], "run.paused")

    def test_persists_external_execution_for_restart_without_duplicate_submission(self):
        published = self.store.publish_workflow(WORKFLOW)
        task = self.store.create_task("External execution")
        run = self.store.create_run(task["id"], published, {})
        record = self.store.create_external_execution(run["id"], "collect", 1, "hermes", "hermes-run-1")
        self.assertEqual(record["status"], "running")

        restarted = CohubStore(Path(self.temp.name) / "cohub.db")
        recovered = restarted.get_external_execution(run["id"], "collect", 1)
        self.assertEqual(recovered["external_run_id"], "hermes-run-1")
        same = restarted.create_external_execution(run["id"], "collect", 1, "hermes", "hermes-run-2")
        self.assertEqual(same["external_run_id"], "hermes-run-1")

        updated = restarted.update_external_execution(run["id"], "collect", 1, "waiting_for_approval")
        self.assertEqual(updated["status"], "waiting_for_approval")
        self.assertEqual(
            [item["external_run_id"] for item in restarted.list_active_external_executions(run["id"])],
            ["hermes-run-1"],
        )

    def test_persists_run_model_selection_and_idempotent_usage(self):
        published = self.store.publish_workflow(WORKFLOW)
        task = self.store.create_task("Model routed run")
        run = self.store.create_run(
            task["id"],
            published,
            {},
            requested_provider="openai-codex",
            requested_model="gpt-5.6",
        )
        self.assertEqual(run["requested_provider"], "openai-codex")
        self.assertEqual(run["requested_model"], "gpt-5.6")

        self.store.create_external_execution(
            run["id"],
            "work",
            1,
            "hermes",
            "hermes-model-1",
            requested_provider="openai-codex",
            requested_model="gpt-5.6",
        )
        usage = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
        first = self.store.record_external_result(
            run["id"], "work", 1, reported_provider="openai-codex", reported_model="gpt-5.6", usage=usage
        )
        second = self.store.record_external_result(
            run["id"], "work", 1, reported_provider="openai-codex", reported_model="gpt-5.6", usage=usage
        )
        self.assertEqual(first["reported_provider"], "openai-codex")
        self.assertEqual(first["usage"], usage)
        self.assertEqual(second["usage"], usage)

        restarted = CohubStore(Path(self.temp.name) / "cohub.db")
        loaded = restarted.get_run(run["id"])
        self.assertEqual(loaded["requested_model"], "gpt-5.6")
        self.assertEqual(loaded["usage"], usage)

    def test_additive_model_columns_migrate_an_existing_database(self):
        with self.store.connection() as connection:
            for column in ("usage_json", "requested_model", "requested_provider"):
                connection.execute(f"ALTER TABLE runs DROP COLUMN {column}")
            for column in ("usage_json", "reported_model", "reported_provider", "requested_model", "requested_provider"):
                connection.execute(f"ALTER TABLE external_executions DROP COLUMN {column}")

        CohubStore(Path(self.temp.name) / "cohub.db")

        with self.store.connection() as connection:
            run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            external_columns = {row["name"] for row in connection.execute("PRAGMA table_info(external_executions)")}
        self.assertTrue({"requested_provider", "requested_model", "usage_json"} <= run_columns)
        self.assertTrue({"requested_provider", "requested_model", "reported_provider", "reported_model", "usage_json"} <= external_columns)

    def test_persists_step_approval_and_artifact_records(self):
        published = self.store.publish_workflow(WORKFLOW)
        task = self.store.create_task("Records")
        run = self.store.create_run(task["id"], published, {})
        self.store.upsert_step(run["id"], "work", "ready")
        approval = self.store.create_approval(run["id"], "work", {"action": "publish"})
        artifact = self.store.create_artifact(run["id"], "work", "report.md", "abc123", 42)

        self.assertEqual(self.store.get_steps(run["id"])[0]["status"], "ready")
        self.assertEqual(self.store.list_approvals(run["id"])[0]["id"], approval["id"])
        self.assertEqual(self.store.list_artifacts(run["id"])[0]["id"], artifact["id"])


if __name__ == "__main__":
    unittest.main()
