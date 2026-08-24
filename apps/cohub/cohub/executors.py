"""Step executor adapters for local development and Hermes Runs API."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .models import ClaimedStep, StepResult
from .schemas import canonical_json


class HermesApiError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Hermes API returned HTTP {status_code}: {body}")


class StepExecutor(Protocol):
    def execute(self, claimed: ClaimedStep) -> StepResult: ...


class DurableStepExecutor(Protocol):
    provider: str
    timeout: float
    poll_interval: float

    def require_approval_bridge(self) -> None: ...
    def submit(self, claimed: ClaimedStep) -> str: ...
    def get_status(self, external_run_id: str) -> dict[str, Any]: ...
    def get_approval_event(self, external_run_id: str) -> dict[str, Any]: ...
    def resolve_approval(self, external_run_id: str, choice: str) -> dict[str, Any]: ...
    def stop(self, external_run_id: str) -> dict[str, Any]: ...
    def parse_completed(self, status: dict[str, Any]) -> StepResult: ...


class LocalExecutor:
    """Deterministic fixture executor used for tests, demos, and offline development."""

    def __init__(self, artifact_root: str | Path):
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def execute(self, claimed: ClaimedStep) -> StepResult:
        node = claimed.node
        if claimed.attempt < int(node.get("fail_until_attempt", 0)):
            raise RuntimeError(f"planned failure before attempt {node['fail_until_attempt']}")
        configured = node.get("local_result", {})
        artifact_records: list[dict[str, Any]] = []
        artifact = node.get("artifact")
        if artifact:
            path = (self.artifact_root / artifact["path"]).resolve()
            if path != self.artifact_root and self.artifact_root not in path.parents:
                raise RuntimeError("artifact path escapes configured root")
            path.parent.mkdir(parents=True, exist_ok=True)
            content = str(artifact.get("content", ""))
            path.write_text(content, encoding="utf-8")
            raw = path.read_bytes()
            artifact_records.append({
                "path": str(path.relative_to(self.artifact_root)),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            })
        return StepResult(
            status=str(configured.get("status", "completed")),
            output=dict(configured.get("output", {})),
            route=configured.get("route"),
            reason=configured.get("reason"),
            artifacts=tuple(artifact_records),
        )


class HermesRunsExecutor:
    """Execute one workflow step through Hermes' documented Runs API."""

    provider = "hermes"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        poll_interval: float = 0.5,
        timeout: float = 300,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = canonical_json(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 30)) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:1000]
            raise HermesApiError(exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Hermes API request failed: {exc.reason}") from exc

    def execute(self, claimed: ClaimedStep) -> StepResult:
        hermes_run_id = self.submit(claimed)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            status = self.get_status(hermes_run_id)
            state = status.get("status")
            if state == "completed":
                return self._parse_result(status.get("output", ""))
            if state in {"failed", "cancelled"}:
                raise RuntimeError(f"Hermes run {state}: {status.get('error') or status.get('output') or 'unknown error'}")
            if state == "waiting_for_approval":
                raise RuntimeError("Hermes run requires an approval bridge")
            time.sleep(self.poll_interval)
        self.stop(hermes_run_id)
        raise TimeoutError(f"Hermes run did not finish within {self.timeout} seconds")

    def require_approval_bridge(self) -> None:
        capabilities = self._request("GET", "/v1/capabilities")
        features = capabilities.get("features", {})
        if not features.get("run_approval_response") or not features.get("approval_events"):
            raise RuntimeError("Hermes Runs API does not advertise approval bridge support")

    def submit(self, claimed: ClaimedStep) -> str:
        correlation_id = f"{claimed.run_id}:{claimed.step_id}:{claimed.attempt}"
        started = self._request(
            "POST",
            "/v1/runs",
            {
                "input": self._prompt(claimed),
                "session_id": correlation_id,
                "instructions": "Return exactly one JSON object matching Cohub's StepResult contract. Do not wrap it in Markdown.",
            },
        )
        hermes_run_id = started.get("run_id")
        if not hermes_run_id:
            raise RuntimeError("Hermes API did not return run_id")
        return str(hermes_run_id)

    def get_status(self, hermes_run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{hermes_run_id}")

    def get_approval_event(self, hermes_run_id: str) -> dict[str, Any]:
        """Read the queued, redacted approval request from Hermes' SSE stream."""

        request = urllib.request.Request(
            f"{self.base_url}/v1/runs/{hermes_run_id}/events",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "text/event-stream"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 30)) as response:
                event_name = ""
                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        event = json.loads(line[5:].strip())
                        if event_name == "approval.request" or event.get("event") == "approval.request":
                            return event
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Hermes approval event could not be recovered") from exc
        raise RuntimeError("Hermes approval event could not be recovered")

    def resolve_approval(self, hermes_run_id: str, choice: str) -> dict[str, Any]:
        if choice not in {"once", "session", "always", "deny"}:
            raise ValueError(f"unsupported Hermes approval choice: {choice}")
        return self._request("POST", f"/v1/runs/{hermes_run_id}/approval", {"choice": choice})

    def stop(self, hermes_run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/runs/{hermes_run_id}/stop", {})

    def parse_completed(self, status: dict[str, Any]) -> StepResult:
        return self._parse_result(status.get("output", ""))

    @staticmethod
    def _prompt(claimed: ClaimedStep) -> str:
        contract = {
            "status": "completed",
            "output": "JSON object",
            "route": f"one of {sorted(claimed.node.get('routes', {}))}" if claimed.node.get("routes") else None,
            "reason": "brief evidence-based reason",
        }
        return (
            "Execute exactly one deterministic Cohub workflow step. Return only one JSON object matching the response contract.\n\n"
            f"Step: {claimed.step_id}\n"
            f"Instructions: {claimed.node.get('prompt', claimed.step_id)}\n"
            f"Task input: {canonical_json(claimed.task_input)}\n"
            f"Dependency outputs: {canonical_json(claimed.dependency_outputs)}\n"
            f"Response contract: {canonical_json(contract)}"
        )

    @staticmethod
    def _parse_result(raw: Any) -> StepResult:
        if isinstance(raw, str):
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(lines[1:-1])
                if raw.lstrip().startswith("json"):
                    raw = raw.lstrip()[4:].lstrip()
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Hermes step output was not valid JSON") from exc
        elif isinstance(raw, dict):
            value = raw
        else:
            raise RuntimeError("Hermes step output must be a JSON object")
        if not isinstance(value, dict):
            raise RuntimeError("Hermes step result must be a JSON object")
        # Models frequently return the bare output object rather than the
        # documented {status, output, route, reason} envelope. Treat a dict
        # without an "output" key as the output itself instead of dropping it.
        if "output" not in value and value.get("status") in {"completed", "failed", "cancelled"}:
            return StepResult(
                status=str(value["status"]),
                output={},
                route=value.get("route"),
                reason=value.get("reason"),
                artifacts=tuple(value.get("artifacts", [])),
            )
        if "output" not in value:
            return StepResult(output=value)
        if not isinstance(value["output"], dict):
            raise RuntimeError("Hermes step result 'output' must be an object")
        return StepResult(
            status=str(value.get("status", "completed")),
            output=value["output"],
            route=value.get("route"),
            reason=value.get("reason"),
            artifacts=tuple(value.get("artifacts", [])),
        )
