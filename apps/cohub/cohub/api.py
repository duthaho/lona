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

from .db import CohubStore
from .engine import EngineError, WorkflowEngine
from .schemas import WorkflowValidationError
from .service import WorkerService


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


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
                })
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
            if method == "GET" and path == "/api/approvals":
                self._json(200, {"approvals": store.list_approvals(status="pending")})
                return
            run_match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)", path)
            if method == "GET" and run_match:
                self._json(200, engine.status(run_match.group(1)))
                return

            if method == "POST" and path == "/api/workflows":
                workflow = store.publish_workflow(self._read_json())
                self._json(201, workflow)
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
                run = engine.start_run(
                    workflow_name,
                    input_data,
                    title=body.get("title"),
                    version=body.get("version"),
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

        def _safe_dispatch(self, method: str) -> None:
            try:
                self._dispatch(method)
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except (EngineError, WorkflowValidationError, KeyError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(500, {"error": "internal server error"})

    return ThreadingHTTPServer((host, port), Handler)
