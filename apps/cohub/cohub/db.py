"""SQLite persistence for Cohub's durable workflow state."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .schemas import canonical_json, fingerprint_workflow, validate_workflow


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _decode(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for field in json_fields:
        if field in value and value[field] is not None:
            decoded = json.loads(value.pop(field))
            public_name = field.removesuffix("_json")
            value[public_name] = decoded
    return value


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS workflow_versions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(name, version)
);
CREATE INDEX IF NOT EXISTS workflow_name_version ON workflow_versions(name, version DESC);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    workflow_id TEXT NOT NULL REFERENCES workflow_versions(id),
    workflow_name TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    workflow_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    step_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    output_json TEXT,
    route TEXT,
    reason TEXT,
    error TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, step_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    decision_note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status, created_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    step_id TEXT,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, seq)
);
"""


class CohubStore:
    """Transaction-safe SQLite repository for Cohub."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def _next_seq(connection: sqlite3.Connection, run_id: str) -> int:
        row = connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM events WHERE run_id=?", (run_id,)).fetchone()
        return int(row["seq"])

    @classmethod
    def _event(
        cls,
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        data: dict[str, Any],
        step_id: str | None = None,
    ) -> int:
        seq = cls._next_seq(connection, run_id)
        connection.execute(
            "INSERT INTO events(run_id,seq,type,step_id,data_json,created_at) VALUES(?,?,?,?,?,?)",
            (run_id, seq, event_type, step_id, canonical_json(data), utc_now()),
        )
        return seq

    def publish_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_workflow(workflow)
        fingerprint = fingerprint_workflow(normalized)
        with self.connection() as connection:
            existing = connection.execute("SELECT * FROM workflow_versions WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                return _decode(existing, ("definition_json",))  # type: ignore[return-value]
            row = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS version FROM workflow_versions WHERE name=?",
                (normalized["name"],),
            ).fetchone()
            workflow_id = _id("wf")
            connection.execute(
                "INSERT INTO workflow_versions(id,name,version,fingerprint,definition_json,created_at) VALUES(?,?,?,?,?,?)",
                (workflow_id, normalized["name"], int(row["version"]), fingerprint, canonical_json(normalized), utc_now()),
            )
            created = connection.execute("SELECT * FROM workflow_versions WHERE id=?", (workflow_id,)).fetchone()
            return _decode(created, ("definition_json",))  # type: ignore[return-value]

    def list_workflows(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM workflow_versions ORDER BY name,version DESC").fetchall()
            return [_decode(row, ("definition_json",)) for row in rows]  # type: ignore[misc]

    def get_workflow(self, name: str, version: int | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM workflow_versions WHERE name=?"
        params: list[Any] = [name]
        if version is None:
            query += " ORDER BY version DESC LIMIT 1"
        else:
            query += " AND version=?"
            params.append(version)
        with self.connection() as connection:
            return _decode(connection.execute(query, params).fetchone(), ("definition_json",))

    def create_task(self, title: str, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        task_id, now = _id("task"), utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO tasks(id,title,status,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (task_id, title, "queued", canonical_json(input_data or {}), now, now),
            )
            return _decode(connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(), ("input_json",))  # type: ignore[return-value]

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
            return [_decode(row, ("input_json",)) for row in rows]  # type: ignore[misc]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return _decode(connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(), ("input_json",))

    def create_run(self, task_id: str, workflow: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        run_id, now = _id("run"), utc_now()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO runs(id,task_id,workflow_id,workflow_name,workflow_version,workflow_fingerprint,status,input_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, task_id, workflow["id"], workflow["name"], workflow["version"], workflow["fingerprint"], "queued", canonical_json(input_data), now, now),
            )
            self._event(connection, run_id, "run.created", {"workflow": workflow["name"], "version": workflow["version"]})
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return _decode(row, ("input_json",))  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return _decode(connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone(), ("input_json",))

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [_decode(row, ("input_json",)) for row in rows]  # type: ignore[misc]

    def get_run_workflow(self, run_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT w.* FROM workflow_versions w JOIN runs r ON r.workflow_id=w.id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"run not found: {run_id}")
            return _decode(row, ("definition_json",))  # type: ignore[return-value]

    def transition_run(
        self,
        run_id: str,
        status: str,
        event_type: str,
        data: dict[str, Any],
        *,
        error: str | None = None,
        fail_after_state: bool = False,
    ) -> None:
        now = utc_now()
        completed = now if status in {"completed", "failed", "cancelled"} else None
        with self.connection() as connection:
            result = connection.execute(
                "UPDATE runs SET status=?,error=?,updated_at=?,completed_at=COALESCE(?,completed_at) WHERE id=?",
                (status, error, now, completed, run_id),
            )
            if result.rowcount != 1:
                raise KeyError(f"run not found: {run_id}")
            task_status = status if status in {"completed", "failed", "cancelled", "paused", "waiting_for_human"} else "running"
            connection.execute(
                "UPDATE tasks SET status=?,updated_at=? WHERE id=(SELECT task_id FROM runs WHERE id=?)",
                (task_status, now, run_id),
            )
            self._event(connection, run_id, event_type, data)
            if fail_after_state:
                raise RuntimeError("injected transaction failure")

    def upsert_step(self, run_id: str, step_id: str, status: str, **fields: Any) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO steps(run_id,step_id,status,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(run_id,step_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at""",
                (run_id, step_id, status, now),
            )
            allowed = {
                "attempt", "output_json", "route", "reason", "error", "lease_owner", "lease_expires_at", "started_at", "completed_at"
            }
            updates, values = [], []
            for key, value in fields.items():
                if key not in allowed:
                    raise ValueError(f"unsupported step field: {key}")
                if key == "output_json" and value is not None and not isinstance(value, str):
                    value = canonical_json(value)
                updates.append(f"{key}=?")
                values.append(value)
            if updates:
                values.extend([now, run_id, step_id])
                connection.execute(f"UPDATE steps SET {','.join(updates)},updated_at=? WHERE run_id=? AND step_id=?", values)
            if status == "completed":
                connection.execute("UPDATE steps SET error=NULL WHERE run_id=? AND step_id=?", (run_id, step_id))
            connection.execute(
                "UPDATE runs SET step_count=(SELECT COUNT(*) FROM steps WHERE run_id=?) WHERE id=?",
                (run_id, run_id),
            )
            row = connection.execute("SELECT * FROM steps WHERE run_id=? AND step_id=?", (run_id, step_id)).fetchone()
            return _decode(row, ("output_json",))  # type: ignore[return-value]

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM steps WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
            return [_decode(row, ("output_json",)) for row in rows]  # type: ignore[misc]

    def get_step(self, run_id: str, step_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return _decode(connection.execute("SELECT * FROM steps WHERE run_id=? AND step_id=?", (run_id, step_id)).fetchone(), ("output_json",))

    def create_approval(self, run_id: str, step_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        import hashlib

        approval_id, now = _id("approval"), utc_now()
        payload_json = canonical_json(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM approvals WHERE run_id=? AND step_id=? AND status='pending' AND payload_hash=?",
                (run_id, step_id, payload_hash),
            ).fetchone()
            if existing:
                return _decode(existing, ("payload_json",))  # type: ignore[return-value]
            connection.execute(
                "INSERT INTO approvals(id,run_id,step_id,status,payload_json,payload_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                (approval_id, run_id, step_id, "pending", payload_json, payload_hash, now),
            )
            self._event(connection, run_id, "approval.requested", {"approval_id": approval_id, "payload_hash": payload_hash}, step_id)
            row = connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            return _decode(row, ("payload_json",))  # type: ignore[return-value]

    def resolve_approval(self, approval_id: str, decision: str, note: str | None = None) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not row:
                raise KeyError(f"approval not found: {approval_id}")
            if row["status"] != "pending":
                raise ValueError("approval is already resolved")
            connection.execute(
                "UPDATE approvals SET status=?,decision_note=?,resolved_at=? WHERE id=?",
                (decision, note, utc_now(), approval_id),
            )
            self._event(connection, row["run_id"], f"approval.{decision}", {"approval_id": approval_id, "note": note}, row["step_id"])
            return _decode(connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone(), ("payload_json",))  # type: ignore[return-value]

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            return _decode(row, ("payload_json",))

    def list_approvals(self, run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        query = "SELECT * FROM approvals" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at DESC"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
            return [_decode(row, ("payload_json",)) for row in rows]  # type: ignore[misc]

    def create_artifact(self, run_id: str, step_id: str, path: str, sha256: str, size: int) -> dict[str, Any]:
        artifact_id = _id("artifact")
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO artifacts(id,run_id,step_id,path,sha256,size,created_at) VALUES(?,?,?,?,?,?,?)",
                (artifact_id, run_id, step_id, path, sha256, int(size), utc_now()),
            )
            self._event(connection, run_id, "artifact.created", {"artifact_id": artifact_id, "path": path, "sha256": sha256}, step_id)
            return _decode(connection.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone())  # type: ignore[return-value]

    def list_artifacts(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM artifacts", []
        if run_id:
            query += " WHERE run_id=?"
            params.append(run_id)
        query += " ORDER BY created_at DESC"
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def list_events(self, run_id: str | None = None, after_seq: int = 0) -> list[dict[str, Any]]:
        if run_id:
            query, params = "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", [run_id, after_seq]
        else:
            query, params = "SELECT * FROM events ORDER BY created_at,run_id,seq", []
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
            return [_decode(row, ("data_json",)) for row in rows]  # type: ignore[misc]
