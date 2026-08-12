"""Persistent background execution for long ProSuite verifications."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from . import verification
from .schemas import ConditionRequest, DatasetRef

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
_PROGRESS_WRITE_INTERVAL_SECONDS = 0.5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state_dir() -> Path:
    """Return a stable per-user directory, independent of an MCP client's cwd."""
    configured = os.environ.get("PROSUITE_MCP_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "ProSuite" / "prosuite-mcp"
    if os.environ.get("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"]) / "prosuite-mcp"
    return Path.home() / ".local" / "state" / "prosuite-mcp"


class RunStore:
    """Small SQLite registry plus atomic JSON result files."""

    def __init__(self, state_dir: Path | None = None):
        self.state_dir = (state_dir or default_state_dir()).resolve()
        self.results_dir = self.state_dir / "results"
        self.outputs_dir = self.state_dir / "outputs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(exist_ok=True)
        self.db_path = self.state_dir / "runs.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS verification_runs (
                    run_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    progress_json TEXT,
                    message TEXT,
                    output_dir TEXT,
                    result_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_created_at "
                "ON verification_runs(created_at DESC)"
            )

    def create(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        run_id = str(uuid4())
        now = _utc_now()
        result_path = self.results_dir / f"{run_id}.json"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO verification_runs (
                    run_id, kind, status, request_json, message, output_dir,
                    result_path, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    kind,
                    json.dumps(request, ensure_ascii=False),
                    "Waiting for the verification worker",
                    request.get("output_dir"),
                    str(result_path),
                    now,
                    now,
                ),
            )
        return self.get(run_id)  # type: ignore[return-value]

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM verification_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_running(self, run_id: str) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE verification_runs
                SET status = 'running', message = ?, started_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                ("Connecting to ProSuite", now, now, run_id),
            )

    def update_progress(self, run_id: str, event: verification.ProgressEvent) -> None:
        message = (
            event.processing_step_message or event.message or "Verification is running"
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE verification_runs
                SET progress_json = ?, message = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (json.dumps(event.as_dict()), message, _utc_now(), run_id),
            )

    def finish(self, run_id: str, result: dict[str, Any]) -> None:
        row = self.get(run_id)
        if row is None:
            return
        result_path = Path(row["result_path"])
        temporary_path = result_path.with_suffix(f".{uuid4().hex}.tmp")
        temporary_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(result_path)

        status = "succeeded" if result.get("status") == "success" else "failed"
        message = (
            "Verification completed"
            if status == "succeeded"
            else str(result.get("error") or "Verification failed")
        )
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE verification_runs
                SET status = ?, message = ?, output_dir = COALESCE(?, output_dir), updated_at = ?,
                    finished_at = ?
                WHERE run_id = ?
                """,
                (status, message, result.get("output_dir"), now, now, run_id),
            )

    def list(self, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        with self._connect() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM verification_runs WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM verification_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]


class AsyncVerificationManager:
    """Own a persistent run registry and a bounded in-process worker queue."""

    def __init__(self, state_dir: Path | None = None, max_workers: int = 1):
        self.store = RunStore(state_dir)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="prosuite-verification"
        )

    def start_adhoc(
        self,
        model_catalog_path: str,
        model_name: str,
        datasets: list[DatasetRef],
        conditions: list[ConditionRequest],
        output_dir: str | None,
        envelope: dict[str, float] | None,
    ) -> dict[str, Any]:
        request = {
            "model_catalog_path": model_catalog_path,
            "model_name": model_name,
            "datasets": [dataset.model_dump(mode="json") for dataset in datasets],
            "conditions": [
                condition.model_dump(mode="json") for condition in conditions
            ],
            "output_dir": output_dir,
            "envelope": envelope,
        }
        return self._submit("adhoc", request)

    def start_xml(
        self,
        spec_path: str,
        specification_name: str,
        replacements: list[list[str]],
        output_dir: str | None,
        envelope: dict[str, float] | None,
    ) -> dict[str, Any]:
        request = {
            "spec_path": spec_path,
            "specification_name": specification_name,
            "replacements": replacements,
            "output_dir": output_dir,
            "envelope": envelope,
        }
        return self._submit("xml", request)

    def _submit(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        requested_output_dir = request.get("output_dir")
        if requested_output_dir is None:
            request["output_dir"] = (
                str(self.store.outputs_dir) if verification._service_is_local() else ""
            )

        row = self.store.create(kind, request)
        self._executor.submit(self._execute, row["run_id"], kind, request)
        run_id = row["run_id"]
        output_dir = row["output_dir"] or None
        return {
            "run_id": run_id,
            "status": "queued",
            "message": (
                f"Verification queued as run {run_id}. No automatic completion "
                "notification will be sent; call get_verification_status with this "
                "run_id to inspect progress, then get_verification_result after it "
                "finishes."
            ),
            "automatic_notification": False,
            "status_tool": {
                "name": "get_verification_status",
                "arguments": {"run_id": run_id},
            },
            "result_tool": {
                "name": "get_verification_result",
                "arguments": {"run_id": run_id},
            },
            "output_dir": output_dir,
            "output_directory_is_final": False,
            "output_note": (
                "ProSuite output is disabled because the service is remote and no "
                "server-side output_dir was supplied."
                if not output_dir
                else "This is the output base directory; the final run subdirectory "
                "will appear in status and result after completion."
            ),
            "result_path": row["result_path"],
            "requested_output_dir": requested_output_dir,
        }

    def _execute(self, run_id: str, kind: str, request: dict[str, Any]) -> None:
        self.store.mark_running(run_id)
        last_write = 0.0

        def on_progress(event: verification.ProgressEvent) -> None:
            nonlocal last_write
            now = monotonic()
            if now - last_write < _PROGRESS_WRITE_INTERVAL_SECONDS:
                return
            last_write = now
            self.store.update_progress(run_id, event)

        try:
            output_dir = request["output_dir"]

            if kind == "adhoc":
                result = verification.run_verification_impl(
                    request["model_catalog_path"],
                    request["model_name"],
                    [DatasetRef.model_validate(value) for value in request["datasets"]],
                    [
                        ConditionRequest.model_validate(value)
                        for value in request["conditions"]
                    ],
                    output_dir,
                    request["envelope"],
                    "adhoc",
                    on_progress,
                )
            else:
                result = verification.run_xml_verification_impl(
                    request["spec_path"],
                    request["specification_name"],
                    request["replacements"],
                    output_dir,
                    request["envelope"],
                    on_progress,
                )
        except Exception as exc:
            result = {
                "status": "error",
                "engine_confirmed": False,
                "error": f"Background verification failed: {type(exc).__name__}: {exc}",
            }
        self.store.finish(run_id, result)

    def status(self, run_id: str) -> dict[str, Any]:
        row = self.store.get(run_id)
        if row is None:
            return {"status": "error", "error": f"Unknown run_id: {run_id}"}
        return _status_response(row)

    def result(self, run_id: str) -> dict[str, Any]:
        row = self.store.get(run_id)
        if row is None:
            return {"status": "error", "error": f"Unknown run_id: {run_id}"}
        if row["status"] not in _TERMINAL_STATUSES:
            return _status_response(row)
        result_path = Path(row["result_path"])
        if not result_path.is_file():
            return {
                "status": "error",
                "run_id": run_id,
                "run_status": row["status"],
                "error": "The run is terminal but its result file is missing.",
            }
        result = json.loads(result_path.read_text(encoding="utf-8"))
        return {"run_id": run_id, "run_status": row["status"], **result}

    def list(self, status: str | None = None, limit: int = 20) -> dict[str, Any]:
        rows = self.store.list(status=status, limit=limit)
        return {"status": "ok", "runs": [_status_response(row) for row in rows]}

    def close(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


def _status_response(row: dict[str, Any]) -> dict[str, Any]:
    progress = json.loads(row["progress_json"]) if row["progress_json"] else None
    current = total = None
    if progress:
        event = verification.ProgressEvent(**progress)
        current, total = event.counter()

    elapsed_seconds = None
    estimated_remaining_seconds = None
    if row["started_at"]:
        end = row["finished_at"] or _utc_now()
        elapsed_seconds = max(
            0,
            round(
                (
                    datetime.fromisoformat(end)
                    - datetime.fromisoformat(row["started_at"])
                ).total_seconds(),
                1,
            ),
        )
        if row["status"] == "running" and current and total and current <= total:
            estimated_remaining_seconds = round(
                elapsed_seconds * (total - current) / current, 1
            )

    return {
        "run_id": row["run_id"],
        "kind": row["kind"],
        "status": row["status"],
        "message": row["message"],
        "progress": progress,
        "progress_percent": (
            round(current * 100 / total, 1) if current is not None and total else None
        ),
        "elapsed_seconds": elapsed_seconds,
        "estimated_remaining_seconds": estimated_remaining_seconds,
        "output_dir": row["output_dir"],
        "output_directory_is_final": row["status"] == "succeeded",
        "result_path": row["result_path"],
        "result_available": Path(row["result_path"]).is_file(),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }


_manager: AsyncVerificationManager | None = None
_manager_lock = threading.Lock()


def get_run_manager() -> AsyncVerificationManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            workers = max(
                1, int(os.environ.get("PROSUITE_MCP_MAX_CONCURRENT_RUNS", "1"))
            )
            _manager = AsyncVerificationManager(max_workers=workers)
        return _manager
