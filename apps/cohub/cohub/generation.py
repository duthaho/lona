"""Safe, durable Hermes-assisted workflow generation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .db import CohubStore, DraftConflictError
from .schemas import WorkflowValidationError, canonical_json, validate_workflow


WORKFLOW_KEYS = frozenset({"name", "description", "start", "nodes", "defaults", "budget", "input_schema"})
NODE_KEYS = {
    "task": frozenset({"type", "prompt", "next", "max_attempts", "side_effect", "approval_payload", "output_schema"}),
    "decision": frozenset({"type", "prompt", "routes", "max_attempts", "output_schema"}),
    "parallel": frozenset({"type", "branches", "next"}),
    "human": frozenset({"type", "prompt", "payload", "next"}),
    "end": frozenset({"type"}),
}
SECRET_KEY_RE = re.compile(r"(?:api[_-]?key|access[_-]?token|password|passwd|private[_-]?key|client[_-]?secret|credential)", re.I)
SECRET_VALUE_RES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", re.I),
)


@dataclass(frozen=True)
class GenerationCandidate:
    workflow: dict[str, Any]
    assumptions: list[str]
    candidate_hash: str


class GenerationCandidateError(ValueError):
    def __init__(self, message: str, *, path: str = "candidate", candidate_hash: str | None = None):
        super().__init__(message)
        self.diagnostics = [{"path": path, "message": message}]
        self.candidate_hash = candidate_hash


class WorkflowGenerator(Protocol):
    def submit(
        self,
        *,
        generation_id: str,
        prompt: str,
        current_workflow: dict[str, Any],
        provider: str | None = None,
        model: str | None = None,
    ) -> str: ...

    def get_status(self, external_run_id: str) -> dict[str, Any]: ...
    def stop(self, external_run_id: str) -> dict[str, Any]: ...


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _has_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_VALUE_RES)


def contains_likely_credential(value: Any, path: str = "candidate") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if SECRET_KEY_RE.search(str(key)):
                return child_path
            found = contains_likely_credential(child, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = contains_likely_credential(child, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str) and _has_secret_value(value):
        return path
    return None


def _strict_fields(value: dict[str, Any], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise GenerationCandidateError(f"unsupported field: {unknown[0]}", path=f"{path}.{unknown[0]}")


def parse_generation_candidate(raw: str) -> GenerationCandidate:
    """Parse a strict untrusted generation response without retaining rejected content."""

    candidate_hash = _hash(raw)
    try:
        envelope = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GenerationCandidateError("Hermes response must be one strict JSON object", candidate_hash=candidate_hash) from exc
    if not isinstance(envelope, dict):
        raise GenerationCandidateError("generation response must be an object", candidate_hash=candidate_hash)
    try:
        _strict_fields(envelope, frozenset({"workflow", "assumptions"}), "candidate")
    except GenerationCandidateError as exc:
        exc.candidate_hash = candidate_hash
        raise
    workflow = envelope.get("workflow")
    assumptions = envelope.get("assumptions", [])
    if not isinstance(workflow, dict):
        raise GenerationCandidateError("workflow must be an object", path="candidate.workflow", candidate_hash=candidate_hash)
    if not isinstance(assumptions, list) or not all(isinstance(item, str) and item.strip() for item in assumptions):
        raise GenerationCandidateError("assumptions must be a string list", path="candidate.assumptions", candidate_hash=candidate_hash)
    credential_path = contains_likely_credential(envelope)
    if credential_path:
        raise GenerationCandidateError("candidate contains a likely credential and was not stored", path=credential_path, candidate_hash=candidate_hash)
    try:
        _strict_fields(workflow, WORKFLOW_KEYS, "candidate.workflow")
        nodes = workflow.get("nodes")
        if not isinstance(nodes, dict):
            raise GenerationCandidateError("nodes must be an object", path="candidate.workflow.nodes")
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                raise GenerationCandidateError("node must be an object", path=f"candidate.workflow.nodes.{node_id}")
            node_type = node.get("type")
            allowed = NODE_KEYS.get(str(node_type))
            if allowed is None:
                raise GenerationCandidateError(f"unsupported node type: {node_type}", path=f"candidate.workflow.nodes.{node_id}.type")
            _strict_fields(node, allowed, f"candidate.workflow.nodes.{node_id}")
        defaults = workflow.get("defaults", {})
        budget = workflow.get("budget", {})
        if not isinstance(defaults, dict) or set(defaults) - {"max_attempts"}:
            raise GenerationCandidateError("defaults contains unsupported fields", path="candidate.workflow.defaults")
        if not isinstance(budget, dict) or set(budget) - {"max_steps", "max_duration_seconds"}:
            raise GenerationCandidateError("budget contains unsupported fields", path="candidate.workflow.budget")
        normalized = validate_workflow(workflow)
    except WorkflowValidationError as exc:
        raise GenerationCandidateError(str(exc), path="candidate.workflow", candidate_hash=candidate_hash) from exc
    except GenerationCandidateError as exc:
        exc.candidate_hash = candidate_hash
        raise
    return GenerationCandidate(normalized, [item.strip() for item in assumptions], candidate_hash)


class WorkflowGenerationService:
    """Submit and reconcile durable generation records without duplicate resubmission."""

    def __init__(self, store: CohubStore, generator: WorkflowGenerator):
        self.store = store
        self.generator = generator

    def start(
        self,
        draft_id: str,
        *,
        prompt: str,
        expected_revision: int,
        kind: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt or len(prompt) > 8000:
            raise ValueError("generation prompt must contain 1 to 8000 characters")
        if _has_secret_value(prompt):
            raise ValueError("generation prompt contains a likely credential")
        draft = self.store.get_workflow_draft(draft_id)
        if draft is None:
            raise KeyError(f"workflow draft not found: {draft_id}")
        generation = self.store.create_workflow_generation(
            draft_id,
            prompt=prompt,
            base_revision=expected_revision,
            provider=provider,
            model=model,
            kind=kind,
        )
        try:
            external_run_id = self.generator.submit(
                generation_id=generation["id"],
                prompt=prompt,
                current_workflow=draft["definition"],
                provider=provider,
                model=model,
            )
            return self.store.attach_workflow_generation_run(generation["id"], external_run_id)
        except Exception:
            self.store.fail_workflow_generation(generation["id"], error="Hermes designer submission failed")
            raise

    def accept(self, generation_id: str, *, expected_revision: int) -> dict[str, Any]:
        return self.store.accept_workflow_generation_candidate(
            generation_id,
            expected_revision=expected_revision,
        )

    def reconcile(self, generation_id: str) -> dict[str, Any]:
        generation = self.store.get_workflow_generation(generation_id)
        if generation is None:
            raise KeyError(f"workflow generation not found: {generation_id}")
        if generation["status"] not in {"queued", "running"}:
            return generation
        external_run_id = generation.get("hermes_run_id")
        if not external_run_id:
            return self.store.fail_workflow_generation(generation_id, error="Hermes submission outcome is uncertain; Cohub will not resubmit automatically")
        status = self.generator.get_status(external_run_id)
        state = str(status.get("status", "")).lower()
        if state in {"queued", "running", "pending", "started"}:
            return generation
        if state == "waiting_for_approval":
            try:
                self.generator.stop(external_run_id)
            finally:
                return self.store.fail_workflow_generation(generation_id, error="Hermes designer attempted an operation requiring approval")
        if state != "completed":
            return self.store.fail_workflow_generation(generation_id, error="Hermes designer run failed")
        output = status.get("output")
        if isinstance(output, dict):
            raw = canonical_json(output)
        elif isinstance(output, str):
            raw = output
        else:
            return self.store.fail_workflow_generation(generation_id, error="Hermes designer returned no JSON candidate")
        try:
            candidate = parse_generation_candidate(raw)
            return self.store.stage_workflow_generation_candidate(
                generation_id,
                candidate=candidate.workflow,
                candidate_hash=candidate.candidate_hash,
                diagnostics=[],
                assumptions=candidate.assumptions,
            )
        except GenerationCandidateError as exc:
            return self.store.fail_workflow_generation(
                generation_id,
                error="Hermes candidate was rejected",
                diagnostics=exc.diagnostics,
                candidate_hash=exc.candidate_hash,
            )
        except (DraftConflictError, WorkflowValidationError):
            return self.store.fail_workflow_generation(
                generation_id,
                error="Draft changed while Hermes was generating; review the current revision before retrying",
                diagnostics=[{"path": "draft.revision", "message": "generation base revision is stale"}],
            )
