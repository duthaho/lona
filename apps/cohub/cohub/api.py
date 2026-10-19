"""Standard-library HTTP API and static dashboard server."""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .db import CohubStore, DraftConflictError
from .engine import EngineError, WorkflowEngine
from .generation import WorkflowGenerationService
from .schemas import WorkflowValidationError, validate_run_input, validate_workflow
from .service import WorkerService
from .templates import get_template, list_templates


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _draft_diagnostic(message: str) -> dict[str, str]:
    node = re.search(r"(?:node|branch) ([A-Za-z0-9._-]+)", message)
    if node:
        path = f"nodes.{node.group(1)}"
    elif message.startswith("start node"):
        path = "start"
    elif message.startswith("name "):
        path = "name"
    elif message.startswith("nodes "):
        path = "nodes"
    else:
        path = "workflow"
    return {"path": path, "message": message}


def _public_generation(generation: dict[str, Any]) -> dict[str, Any]:
    value = dict(generation)
    if value.get("status") != "candidate_ready":
        value.pop("candidate", None)
    return value


def _simulate_workflow(definition: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    validated_input = validate_run_input(definition, input_data)
    visited: set[str] = set()
    queue = [definition["start"]]
    steps: list[dict[str, Any]] = []
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        node = definition["nodes"][node_id]
        steps.append({
            "node_id": node_id,
            "type": node["type"],
            "approval_required": node["type"] == "human" or bool(node.get("side_effect")),
            "side_effect_suppressed": bool(node.get("side_effect")),
        })
        targets: list[str] = []
        if isinstance(node.get("next"), str):
            targets.append(node["next"])
        targets.extend(node.get("routes", {}).values())
        targets.extend(node.get("branches", []))
        queue.extend(targets)
    return {"status": "simulated", "input": validated_input, "steps": steps, "side_effect_adapters_invoked": False}


def create_server(
    host: str,
    port: int,
    store: CohubStore,
    engine: WorkflowEngine,
    worker: WorkerService,
    *,
    static_dir: str | Path,
    api_token: str = "",
    model_catalog: Callable[[bool], dict[str, Any]] | None = None,
    workflow_generation: WorkflowGenerationService | None = None,
) -> ThreadingHTTPServer:
    root = Path(static_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Cohub/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _authorized(self) -> bool:
            if not api_token:
                return True
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {api_token}"
            return hmac.compare_digest(supplied, expected)

        def _json(self, status: int, body: Any) -> None:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ApiError(400, "invalid Content-Length") from exc
            if length <= 0 or length > 1_000_000:
                raise ApiError(400, "request body must be between 1 byte and 1 MB")
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiError(400, "request body must be valid JSON") from exc
            if not isinstance(value, dict):
                raise ApiError(400, "request body must be a JSON object")
            return value

        def _dispatch(self, method: str) -> None:
            path = urlparse(self.path).path
            if path.startswith("/api/") and not self._authorized():
                raise ApiError(401, "invalid or missing API token")

            if method == "GET" and path == "/api/health":
                self._json(200, {"status": "ok", "service": "cohub", "version": "0.1.0"})
                return
            if method == "GET" and path == "/api/overview":
                runs = store.list_runs()
                counts = {status: sum(1 for run in runs if run["status"] == status) for status in (
                    "queued", "running", "waiting_for_human", "paused", "completed", "failed", "cancelled"
                )}
                self._json(200, {
                    "counts": counts,
                    "runs": runs[:10],
                    "approvals": store.list_approvals(status="pending"),
                    "workflows": store.list_workflows(),
                    "tasks": store.list_tasks()[:10],
                    "capabilities": {"workflow_generation": workflow_generation is not None},
                })
                return
            if method == "GET" and path == "/api/analytics":
                self._json(200, {"models": store.workflow_analytics()})
                return
            if method == "GET" and path == "/api/hermes/models":
                if model_catalog is None:
                    raise ApiError(503, "Hermes model catalog is unavailable")
                self._json(200, model_catalog(False))
                return
            if method == "GET" and path == "/api/tasks":
                self._json(200, {"tasks": store.list_tasks()})
                return
            if method == "GET" and path == "/api/runs":
                self._json(200, {"runs": store.list_runs()})
                return
            if method == "GET" and path == "/api/workflows":
                self._json(200, {"workflows": store.list_workflows()})
                return
            if method == "GET" and path == "/api/workflow-templates":
                self._json(200, {"templates": list_templates()})
                return
            if method == "GET" and path == "/api/workflow-drafts":
                self._json(200, {"drafts": store.list_workflow_drafts()})
                return
            if method == "GET" and path == "/api/approvals":
                self._json(200, {"approvals": store.list_approvals(status="pending")})
                return
            run_match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)", path)
            if method == "GET" and run_match:
                self._json(200, engine.status(run_match.group(1)))
                return
            draft_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)", path)
            if method == "GET" and draft_match:
                draft = store.get_workflow_draft(draft_match.group(1))
                if draft is None:
                    raise ApiError(404, "workflow draft not found")
                self._json(200, draft)
                return
            generation_match = re.fullmatch(r"/api/workflow-generations/([A-Za-z0-9_-]+)", path)
            if method == "GET" and generation_match:
                if workflow_generation is None:
                    raise ApiError(503, "workflow generation is not configured")
                generation = workflow_generation.reconcile(generation_match.group(1))
                self._json(200, _public_generation(generation))
                return
            draft_generations_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)/generations", path)
            if method == "GET" and draft_generations_match:
                if store.get_workflow_draft(draft_generations_match.group(1)) is None:
                    raise ApiError(404, "workflow draft not found")
                generations = store.list_workflow_generations(draft_generations_match.group(1))
                self._json(200, {"generations": [_public_generation(item) for item in generations]})
                return
            draft_revisions_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)/revisions", path)
            if method == "GET" and draft_revisions_match:
                if store.get_workflow_draft(draft_revisions_match.group(1)) is None:
                    raise ApiError(404, "workflow draft not found")
                self._json(200, {"revisions": store.list_workflow_draft_revisions(draft_revisions_match.group(1))})
                return
            draft_restore_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)/restore", path)
            if method == "POST" and draft_restore_match:
                body = self._read_json()
                target = body.get("target_revision")
                expected = body.get("revision")
                if any(isinstance(value, bool) or not isinstance(value, int) for value in (target, expected)):
                    raise ApiError(400, "revision and target_revision must be integers")
                assert isinstance(target, int) and not isinstance(target, bool)
                assert isinstance(expected, int) and not isinstance(expected, bool)
                restored = store.restore_workflow_draft_revision(
                    draft_restore_match.group(1), target, expected_revision=expected,
                )
                self._json(200, restored)
                return
            draft_test_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)/test-run", path)
            if method == "POST" and draft_test_match:
                body = self._read_json()
                draft = store.get_workflow_draft(draft_test_match.group(1))
                if draft is None:
                    raise ApiError(404, "workflow draft not found")
                if body.get("revision") != draft["revision"]:
                    raise DraftConflictError("workflow draft revision is stale")
                input_data = body.get("input", {})
                if not isinstance(input_data, dict):
                    raise ApiError(400, "input must be an object")
                definition = validate_workflow(draft["definition"])
                self._json(200, _simulate_workflow(definition, input_data))
                return
            generation_accept_match = re.fullmatch(r"/api/workflow-generations/([A-Za-z0-9_-]+)/accept", path)
            if method == "POST" and generation_accept_match:
                if workflow_generation is None:
                    raise ApiError(503, "workflow generation is not configured")
                body = self._read_json()
                revision = body.get("revision")
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ApiError(400, "revision must be an integer")
                generation = workflow_generation.accept(generation_accept_match.group(1), expected_revision=revision)
                draft = store.get_workflow_draft(generation["draft_id"])
                self._json(200, {"generation": _public_generation(generation), "draft": draft})
                return
            if method == "PUT" and draft_match:
                body = self._read_json()
                revision = body.get("revision")
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ApiError(400, "revision must be an integer")
                definition = body.get("definition")
                layout = body.get("layout")
                if definition is not None and not isinstance(definition, dict):
                    raise ApiError(400, "definition must be an object")
                if layout is not None and not isinstance(layout, dict):
                    raise ApiError(400, "layout must be an object")
                draft = store.update_workflow_draft(
                    draft_match.group(1),
                    expected_revision=revision,
                    definition=definition,
                    layout=layout,
                )
                self._json(200, draft)
                return

            if method == "POST" and path == "/api/workflows":
                workflow = store.publish_workflow(self._read_json())
                self._json(201, workflow)
                return
            if method == "POST" and path == "/api/workflow-drafts":
                body = self._read_json()
                template_id = body.get("template_id")
                workflow_name = body.get("workflow_name")
                if template_id is not None:
                    if not isinstance(template_id, str):
                        raise ApiError(400, "template_id must be a string")
                    definition = get_template(template_id)
                    if definition is None:
                        raise ApiError(404, "workflow template not found")
                    draft = store.create_workflow_draft(definition)
                elif workflow_name is not None:
                    if not isinstance(workflow_name, str) or not workflow_name:
                        raise ApiError(400, "workflow_name must be a non-empty string")
                    version = body.get("version")
                    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
                        raise ApiError(400, "version must be an integer")
                    draft = store.create_workflow_draft_from_version(workflow_name, version)
                else:
                    definition = body.get("definition")
                    layout = body.get("layout", {})
                    if not isinstance(definition, dict):
                        raise ApiError(400, "definition must be an object")
                    if not isinstance(layout, dict):
                        raise ApiError(400, "layout must be an object")
                    draft = store.create_workflow_draft(definition, layout=layout)
                self._json(201, draft)
                return
            draft_generation_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)/(generate|revise)", path)
            if method == "POST" and draft_generation_match:
                if workflow_generation is None:
                    raise ApiError(503, "workflow generation is not configured")
                body = self._read_json()
                revision = body.get("revision")
                prompt = body.get("prompt")
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ApiError(400, "revision must be an integer")
                if not isinstance(prompt, str):
                    raise ApiError(400, "prompt must be a string")
                provider = body.get("provider")
                model = body.get("model")
                if (provider is None) != (model is None):
                    raise ApiError(400, "provider and model must be selected together")
                if provider is not None:
                    if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
                        raise ApiError(400, "provider and model must be non-empty strings")
                    if model_catalog is None:
                        raise ApiError(400, "explicit model selection is unavailable")
                    catalog = model_catalog(False)
                    allowed = {
                        (item.get("provider"), option.get("id"))
                        for item in catalog.get("providers", [])
                        for option in item.get("models", [])
                    }
                    if (provider, model) not in allowed:
                        raise ApiError(400, "selected provider/model is not authenticated in Hermes")
                generation = workflow_generation.start(
                    draft_generation_match.group(1),
                    prompt=prompt,
                    expected_revision=revision,
                    kind=draft_generation_match.group(2),
                    provider=provider,
                    model=model,
                )
                self._json(202, _public_generation(generation))
                return
            draft_validate_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)/validate", path)
            if method == "POST" and draft_validate_match:
                self._read_json()
                draft = store.get_workflow_draft(draft_validate_match.group(1))
                if draft is None:
                    raise ApiError(404, "workflow draft not found")
                try:
                    validate_workflow(draft["definition"])
                    diagnostics: list[dict[str, str]] = []
                except WorkflowValidationError as exc:
                    diagnostics = [_draft_diagnostic(str(exc))]
                self._json(200, {"valid": not diagnostics, "revision": draft["revision"], "diagnostics": diagnostics})
                return
            draft_publish_match = re.fullmatch(r"/api/workflow-drafts/([A-Za-z0-9_-]+)/publish", path)
            if method == "POST" and draft_publish_match:
                body = self._read_json()
                revision = body.get("revision")
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ApiError(400, "revision must be an integer")
                result = store.publish_workflow_draft(draft_publish_match.group(1), expected_revision=revision)
                self._json(201, result)
                return
            if method == "POST" and path == "/api/runs":
                body = self._read_json()
                workflow_name = body.get("workflow")
                if not isinstance(workflow_name, str) or not workflow_name:
                    raise ApiError(400, "workflow is required")
                input_data = body.get("input", {})
                if not isinstance(input_data, dict):
                    raise ApiError(400, "input must be an object")
                provider = body.get("provider")
                model = body.get("model")
                if (provider is None) != (model is None):
                    raise ApiError(400, "provider and model must be selected together")
                if provider is not None:
                    if not isinstance(provider, str) or not provider.strip():
                        raise ApiError(400, "provider must be a non-empty string")
                    if not isinstance(model, str) or not model.strip():
                        raise ApiError(400, "model must be a non-empty string")
                    if model_catalog is None:
                        raise ApiError(400, "explicit model selection is unavailable")
                    catalog = model_catalog(False)
                    allowed = {
                        (item.get("provider"), option.get("id"))
                        for item in catalog.get("providers", [])
                        for option in item.get("models", [])
                    }
                    if (provider, model) not in allowed:
                        raise ApiError(400, "selected provider/model is not authenticated in Hermes")
                workflow_version = body.get("version")
                if workflow_version is not None and (isinstance(workflow_version, bool) or not isinstance(workflow_version, int)):
                    raise ApiError(400, "version must be an integer")
                published = store.get_workflow(workflow_name, workflow_version)
                if published is None:
                    raise ApiError(404, "workflow not found")
                input_data = validate_run_input(published["definition"], input_data)
                run = engine.start_run(
                    workflow_name,
                    input_data,
                    title=body.get("title"),
                    version=workflow_version,
                    requested_provider=provider,
                    requested_model=model,
                )
                self._json(201, run)
                return
            tick_match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)/tick", path)
            if method == "POST" and tick_match:
                self._read_json()
                worked = worker.tick(tick_match.group(1))
                self._json(200, {"worked": worked, "run": engine.status(tick_match.group(1))})
                return
            action_match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)/(pause|resume|cancel)", path)
            if method == "POST" and action_match:
                self._read_json()
                getattr(engine, action_match.group(2))(action_match.group(1))
                self._json(200, engine.status(action_match.group(1)))
                return
            approval_match = re.fullmatch(r"/api/approvals/([A-Za-z0-9_-]+)/(approve|reject)", path)
            if method == "POST" and approval_match:
                body = self._read_json()
                decision = "approved" if approval_match.group(2) == "approve" else "rejected"
                approval = engine.resolve_approval(
                    approval_match.group(1),
                    decision,
                    body.get("note"),
                    expected_payload_hash=body.get("payload_hash"),
                )
                self._json(200, approval)
                return
            if path.startswith("/api/"):
                raise ApiError(404, "API route not found")
            if method != "GET":
                raise ApiError(405, "method not allowed")
            self._static(path)

        def _static(self, path: str) -> None:
            relative = "index.html" if path == "/" else path.lstrip("/")
            candidate = (root / relative).resolve()
            if candidate != root and root not in candidate.parents:
                raise ApiError(404, "not found")
            if not candidate.is_file():
                candidate = root / "index.html"
            if not candidate.is_file():
                raise ApiError(404, "dashboard not installed")
            payload = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._safe_dispatch("GET")

        def do_POST(self) -> None:
            self._safe_dispatch("POST")

        def do_PUT(self) -> None:
            self._safe_dispatch("PUT")

        def _safe_dispatch(self, method: str) -> None:
            try:
                self._dispatch(method)
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except DraftConflictError as exc:
                self._json(409, {"error": str(exc)})
            except (EngineError, WorkflowValidationError, KeyError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(500, {"error": "internal server error"})

    return ThreadingHTTPServer((host, port), Handler)
