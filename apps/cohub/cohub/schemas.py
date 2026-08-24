"""Workflow document validation and canonical fingerprinting."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import deque
from typing import Any


class WorkflowValidationError(ValueError):
    """Raised when a workflow definition violates a deterministic invariant."""


NODE_TYPES = frozenset({"task", "decision", "parallel", "human", "end"})
SCHEMA_KEYS = frozenset({"type", "required", "properties"})
PRIMITIVE_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def canonical_json(value: Any) -> str:
    """Serialize JSON data with stable ordering and no insignificant whitespace."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint_workflow(workflow: dict[str, Any]) -> str:
    """Return the SHA-256 fingerprint of a validated workflow's canonical JSON."""

    normalized = validate_workflow(workflow)
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def _edges(node: dict[str, Any]) -> list[str]:
    node_type = node["type"]
    if node_type == "decision":
        return list(node.get("routes", {}).values())
    if node_type == "parallel":
        values = list(node.get("branches", []))
        if node.get("next"):
            values.append(node["next"])
        return values
    return [node["next"]] if node.get("next") else []


def _validate_output_schema(schema: Any, location: str) -> None:
    if not isinstance(schema, dict):
        raise WorkflowValidationError(f"{location} output_schema must be an object")
    unknown = set(schema) - SCHEMA_KEYS
    if unknown:
        raise WorkflowValidationError(f"{location} uses unsupported schema keyword: {sorted(unknown)[0]}")
    schema_type = schema.get("type", "object")
    if schema_type not in PRIMITIVE_TYPES:
        raise WorkflowValidationError(f"{location} has unsupported schema type: {schema_type}")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise WorkflowValidationError(f"{location} schema required must be a string list")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise WorkflowValidationError(f"{location} schema properties must be an object")
    for key, child in properties.items():
        _validate_output_schema(child, f"{location}.{key}")


def validate_workflow(workflow: Any) -> dict[str, Any]:
    """Validate and return a deep normalized copy of a workflow definition."""

    if not isinstance(workflow, dict):
        raise WorkflowValidationError("workflow must be an object")
    for field in ("name", "start", "nodes"):
        if field not in workflow:
            raise WorkflowValidationError(f"workflow is missing required field: {field}")
    normalized = copy.deepcopy(workflow)
    name = normalized["name"]
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise WorkflowValidationError("name must be a safe non-empty identifier")
    nodes = normalized["nodes"]
    if not isinstance(nodes, dict) or not nodes:
        raise WorkflowValidationError("nodes must be a non-empty object")
    start = normalized["start"]
    if start not in nodes:
        raise WorkflowValidationError(f"start node does not exist: {start}")

    for node_id, node in nodes.items():
        if not isinstance(node_id, str) or not NAME_RE.fullmatch(node_id):
            raise WorkflowValidationError(f"invalid node identifier: {node_id!r}")
        if not isinstance(node, dict):
            raise WorkflowValidationError(f"node {node_id} must be an object")
        node_type = node.get("type")
        if node_type not in NODE_TYPES:
            raise WorkflowValidationError(f"node {node_id} has unsupported node type: {node_type}")
        if node_type == "decision" and (not isinstance(node.get("routes"), dict) or not node["routes"]):
            raise WorkflowValidationError(f"decision node {node_id} requires routes")
        if node_type == "parallel" and (not isinstance(node.get("branches"), list) or not node["branches"]):
            raise WorkflowValidationError(f"parallel node {node_id} requires branches")
        if node_type == "end" and _edges(node):
            raise WorkflowValidationError(f"end node {node_id} cannot have outgoing edges")
        if "output_schema" in node:
            _validate_output_schema(node["output_schema"], f"node {node_id}")
        for target in _edges(node):
            if target not in nodes:
                raise WorkflowValidationError(f"node {node_id} references unknown node: {target}")
        if node_type == "parallel":
            for branch in node["branches"]:
                branch_node = nodes.get(branch)
                if isinstance(branch_node, dict) and (branch_node.get("type") != "task" or _edges(branch_node)):
                    raise WorkflowValidationError(
                        f"direct parallel branch {branch} must be a one-step task without outgoing edges"
                    )

    reachable: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        node_id = queue.popleft()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        queue.extend(_edges(nodes[node_id]))
    if not any(nodes[node_id]["type"] == "end" for node_id in reachable):
        raise WorkflowValidationError("workflow must contain a reachable end node")
    normalized.setdefault("defaults", {})
    normalized.setdefault("budget", {})
    return normalized


def validate_output(output: Any, schema: dict[str, Any] | None) -> None:
    """Validate an executor output against Cohub's intentionally small schema subset."""

    if schema is None:
        if not isinstance(output, dict):
            raise WorkflowValidationError("step output must be an object")
        return
    expected = schema.get("type", "object")
    checks = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "null": lambda value: value is None,
    }
    if not checks[expected](output):
        raise WorkflowValidationError(f"output must be {expected}")
    if expected == "object":
        missing = [key for key in schema.get("required", []) if key not in output]
        if missing:
            raise WorkflowValidationError(f"output is missing required property: {missing[0]}")
        for key, child in schema.get("properties", {}).items():
            if key in output:
                validate_output(output[key], child)
