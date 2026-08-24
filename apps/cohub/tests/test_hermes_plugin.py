import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cohub import hermes_plugin


class FakeContext:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset, schema, handler):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}


class HermesPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"COHUB_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        hermes_plugin.reset_runtime()

    def test_registers_expected_tools(self):
        context = FakeContext()
        hermes_plugin.register(context)
        self.assertEqual(set(context.tools), {
            "cohub_publish_workflow", "cohub_start_run", "cohub_run_status",
            "cohub_tick_run", "cohub_resolve_approval",
        })
        for tool in context.tools.values():
            self.assertEqual(tool["toolset"], "cohub")
            self.assertIn("description", tool["schema"])

    def test_handlers_never_raise_and_return_json_strings(self):
        context = FakeContext()
        hermes_plugin.register(context)
        invalid = context.tools["cohub_publish_workflow"]["handler"]({}, unexpected=True)
        self.assertIsInstance(invalid, str)
        self.assertIn("error", json.loads(invalid))

    def test_publish_start_tick_and_status_tools(self):
        context = FakeContext()
        hermes_plugin.register(context)
        workflow = {
            "name": "plugin-demo", "start": "work",
            "nodes": {
                "work": {"type": "task", "local_result": {"output": {"ok": True}}, "next": "done"},
                "done": {"type": "end"},
            },
        }
        published = json.loads(context.tools["cohub_publish_workflow"]["handler"]({"workflow": workflow}))
        self.assertTrue(published["success"])
        started = json.loads(context.tools["cohub_start_run"]["handler"]({"workflow": "plugin-demo", "input": {}}))
        run_id = started["run"]["id"]
        ticked = json.loads(context.tools["cohub_tick_run"]["handler"]({"run_id": run_id}))
        self.assertTrue(ticked["worked"])
        status = json.loads(context.tools["cohub_run_status"]["handler"]({"run_id": run_id}))
        self.assertEqual(status["run"]["status"], "completed")
        self.assertTrue((Path(self.temp.name) / "cohub.db").exists())


if __name__ == "__main__":
    unittest.main()
