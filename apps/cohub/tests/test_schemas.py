import copy
import unittest

from cohub.schemas import WorkflowValidationError, canonical_json, fingerprint_workflow, validate_workflow


BASE_WORKFLOW = {
    "name": "approval-report",
    "start": "draft",
    "nodes": {
        "draft": {"type": "task", "next": "review"},
        "review": {
            "type": "decision",
            "routes": {"approved": "publish", "changes": "draft"},
        },
        "publish": {"type": "end"},
    },
}


class WorkflowSchemaTests(unittest.TestCase):
    def test_accepts_valid_workflow_and_returns_normalized_copy(self):
        workflow = copy.deepcopy(BASE_WORKFLOW)
        normalized = validate_workflow(workflow)
        self.assertEqual(normalized["name"], "approval-report")
        self.assertEqual(normalized["nodes"]["draft"]["type"], "task")
        self.assertIsNot(normalized, workflow)

    def test_rejects_missing_required_fields(self):
        for field in ("name", "start", "nodes"):
            workflow = copy.deepcopy(BASE_WORKFLOW)
            del workflow[field]
            with self.subTest(field=field), self.assertRaisesRegex(WorkflowValidationError, field):
                validate_workflow(workflow)

    def test_rejects_unsupported_node_type(self):
        workflow = copy.deepcopy(BASE_WORKFLOW)
        workflow["nodes"]["draft"]["type"] = "magic"
        with self.assertRaisesRegex(WorkflowValidationError, "unsupported node type"):
            validate_workflow(workflow)

    def test_rejects_missing_start_and_dangling_edges(self):
        missing_start = copy.deepcopy(BASE_WORKFLOW)
        missing_start["start"] = "missing"
        with self.assertRaisesRegex(WorkflowValidationError, "start node"):
            validate_workflow(missing_start)

        dangling = copy.deepcopy(BASE_WORKFLOW)
        dangling["nodes"]["draft"]["next"] = "missing"
        with self.assertRaisesRegex(WorkflowValidationError, "unknown node"):
            validate_workflow(dangling)

    def test_decisions_require_routes_and_parallel_nodes_require_branches(self):
        decision = copy.deepcopy(BASE_WORKFLOW)
        del decision["nodes"]["review"]["routes"]
        with self.assertRaisesRegex(WorkflowValidationError, "routes"):
            validate_workflow(decision)

        parallel = copy.deepcopy(BASE_WORKFLOW)
        parallel["nodes"]["draft"] = {"type": "parallel", "next": "review"}
        with self.assertRaisesRegex(WorkflowValidationError, "branches"):
            validate_workflow(parallel)

    def test_parallel_direct_branches_cannot_advance_independently(self):
        workflow = {
            "name": "parallel",
            "start": "fanout",
            "nodes": {
                "fanout": {"type": "parallel", "branches": ["left", "right"], "next": "merge"},
                "left": {"type": "task", "next": "merge"},
                "right": {"type": "task"},
                "merge": {"type": "task", "next": "done"},
                "done": {"type": "end"},
            },
        }
        with self.assertRaisesRegex(WorkflowValidationError, "direct parallel branch"):
            validate_workflow(workflow)

    def test_requires_a_reachable_end_node(self):
        workflow = {
            "name": "loop",
            "start": "again",
            "nodes": {"again": {"type": "task", "next": "again"}},
        }
        with self.assertRaisesRegex(WorkflowValidationError, "reachable end"):
            validate_workflow(workflow)

    def test_fingerprint_is_stable_across_dictionary_order(self):
        left = copy.deepcopy(BASE_WORKFLOW)
        right = {"nodes": left["nodes"], "start": left["start"], "name": left["name"]}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(fingerprint_workflow(left), fingerprint_workflow(right))
        self.assertEqual(len(fingerprint_workflow(left)), 64)

    def test_rejects_unknown_output_schema_keywords(self):
        workflow = copy.deepcopy(BASE_WORKFLOW)
        workflow["nodes"]["draft"]["output_schema"] = {
            "type": "object",
            "additionalProperties": False,
        }
        with self.assertRaisesRegex(WorkflowValidationError, "unsupported schema keyword"):
            validate_workflow(workflow)


if __name__ == "__main__":
    unittest.main()
