import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cohub.generation import GenerationCandidateError, parse_generation_candidate
from cohub.executors import HermesWorkflowGenerator


VALID_WORKFLOW = {
    "name": "generated",
    "start": "draft",
    "nodes": {
        "draft": {"type": "task", "prompt": "Create a concise report", "next": "done"},
        "done": {"type": "end"},
    },
}


class DesignerHermesHandler(BaseHTTPRequestHandler):
    calls = []
    enabled_tools = []

    def log_message(self, format, *args):
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.__class__.calls.append(("GET", self.path, None))
        if self.path == "/v1/toolsets":
            self._json(200, {"data": [{"name": "default", "enabled": bool(self.enabled_tools), "tools": self.enabled_tools}]})
        elif self.path == "/v1/runs/designer-run-1":
            self._json(200, {
                "run_id": "designer-run-1",
                "status": "completed",
                "output": json.dumps({"workflow": VALID_WORKFLOW, "assumptions": ["Inputs are supplied at run time"]}),
            })
        else:
            self._json(404, {"error": "missing"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.calls.append(("POST", self.path, body))
        if self.path == "/v1/runs":
            self._json(202, {"run_id": "designer-run-1", "status": "queued"})
        elif self.path.endswith("/stop"):
            self._json(200, {"status": "stopping"})
        else:
            self._json(404, {"error": "missing"})


class WorkflowGenerationTests(unittest.TestCase):
    def test_strict_candidate_parser_accepts_supported_workflow(self):
        result = parse_generation_candidate(json.dumps({"workflow": VALID_WORKFLOW, "assumptions": ["Review before publishing"]}))
        self.assertEqual(result.workflow["name"], "generated")
        self.assertEqual(result.assumptions, ["Review before publishing"])
        self.assertTrue(result.candidate_hash.startswith("sha256:"))

    def test_candidate_parser_rejects_unknown_keys_tools_and_secrets(self):
        with self.assertRaises(GenerationCandidateError) as unknown:
            parse_generation_candidate(json.dumps({"workflow": {**VALID_WORKFLOW, "auto_run": True}, "assumptions": []}))
        self.assertIn("unsupported field", unknown.exception.diagnostics[0]["message"])

        tool_node = json.loads(json.dumps(VALID_WORKFLOW))
        tool_node["nodes"]["draft"]["command"] = "send-email"
        with self.assertRaises(GenerationCandidateError) as tool:
            parse_generation_candidate(json.dumps({"workflow": tool_node, "assumptions": []}))
        self.assertIn("unsupported field", tool.exception.diagnostics[0]["message"])

        secret_node = json.loads(json.dumps(VALID_WORKFLOW))
        secret_node["nodes"]["draft"]["prompt"] = "Use token ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        with self.assertRaises(GenerationCandidateError) as secret:
            parse_generation_candidate(json.dumps({"workflow": secret_node, "assumptions": []}))
        self.assertIn("credential", secret.exception.diagnostics[0]["message"])

    def test_candidate_parser_rejects_markdown_and_malformed_output(self):
        with self.assertRaises(GenerationCandidateError):
            parse_generation_candidate("```json\n{}\n```")
        with self.assertRaises(GenerationCandidateError):
            parse_generation_candidate(json.dumps({"workflow": VALID_WORKFLOW, "assumptions": "not-a-list"}))

    def test_designer_executor_verifies_empty_tool_surface_before_submission(self):
        DesignerHermesHandler.calls = []
        DesignerHermesHandler.enabled_tools = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), DesignerHermesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        generator = HermesWorkflowGenerator(f"http://127.0.0.1:{server.server_port}", "secret")

        run_id = generator.submit(
            generation_id="generation-1",
            prompt="Create a daily briefing with approval",
            current_workflow=VALID_WORKFLOW,
            provider="openai-codex",
            model="gpt-5.6",
        )
        self.assertEqual(run_id, "designer-run-1")
        self.assertEqual(DesignerHermesHandler.calls[0][0:2], ("GET", "/v1/toolsets"))
        submitted = DesignerHermesHandler.calls[1][2]
        self.assertEqual(submitted["session_id"], "cohub-generation:generation-1")
        self.assertEqual(submitted["provider"], "openai-codex")
        self.assertEqual(submitted["model"], "gpt-5.6")
        self.assertIn("Never call tools", submitted["instructions"])
        self.assertNotIn("tools", submitted)
        self.assertEqual(generator.get_status(run_id)["status"], "completed")

    def test_designer_executor_fails_closed_when_profile_has_tools(self):
        DesignerHermesHandler.calls = []
        DesignerHermesHandler.enabled_tools = ["terminal"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), DesignerHermesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        generator = HermesWorkflowGenerator(f"http://127.0.0.1:{server.server_port}", "secret")

        with self.assertRaisesRegex(RuntimeError, "tools disabled"):
            generator.submit(
                generation_id="generation-unsafe",
                prompt="Create a workflow",
                current_workflow=VALID_WORKFLOW,
            )
        self.assertFalse(any(call[0] == "POST" and call[1] == "/v1/runs" for call in DesignerHermesHandler.calls))


if __name__ == "__main__":
    unittest.main()
