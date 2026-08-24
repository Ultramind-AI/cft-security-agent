from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from schemas.api import (
    ApiEvidence,
    ApiFinding,
    ApiProject,
    ApiRun,
    ChatMessage,
    ChatSession,
)
from schemas.errors import ErrorDetail
from schemas.evidence import Evidence
from schemas.report import FinalReport
from schemas.target import TargetProfile


class ApiStore:
    """SQLite metadata store. Large executor artifacts remain on disk."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # All operations are short metadata reads/writes coming from both the
        # API worker pool and the background run thread; serialize them so no
        # two threads are ever inside sqlite3 on this file at once.
        self._lock = threading.Lock()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=30.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS projects (
                    target_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    services_json TEXT NOT NULL,
                    profile_path TEXT NOT NULL,
                    repository_path TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_mode TEXT,
                    max_iterations INTEGER NOT NULL,
                    analysis_request TEXT,
                    artifact_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    gate_decision TEXT,
                    error_json TEXT,
                    FOREIGN KEY(target_id) REFERENCES projects(target_id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_target_created
                    ON runs(target_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS findings (
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    severity TEXT,
                    service TEXT,
                    file TEXT NOT NULL,
                    line_start INTEGER,
                    report_path TEXT NOT NULL,
                    PRIMARY KEY(run_id, finding_id),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS evidence (
                    run_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, evidence_id),
                    FOREIGN KEY(run_id, finding_id)
                        REFERENCES findings(run_id, finding_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_evidence_run_finding
                    ON evidence(run_id, finding_id);

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    active_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(target_id) REFERENCES projects(target_id),
                    FOREIGN KEY(active_run_id) REFERENCES runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated
                    ON chat_sessions(updated_at DESC);

                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
                    ON chat_messages(session_id, created_at);

                CREATE TABLE IF NOT EXISTS chat_session_runs (
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, run_id),
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chat_session_runs_order
                    ON chat_session_runs(session_id, linked_at, run_id);
                """
            )
            # Existing T23 databases predate the chat prompt column.
            _ensure_column(connection, "runs", "analysis_request", "TEXT")
            connection.execute(
                """
                INSERT OR IGNORE INTO chat_session_runs (session_id, run_id, linked_at)
                SELECT session_id, run_id, MIN(created_at)
                FROM chat_messages
                WHERE run_id IS NOT NULL
                GROUP BY session_id, run_id
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chat_session_runs (session_id, run_id, linked_at)
                SELECT session_id, active_run_id, updated_at
                FROM chat_sessions
                WHERE active_run_id IS NOT NULL
                """
            )

    def upsert_project(self, *, profile_path: Path, profile: TargetProfile) -> None:
        repository = str(profile.repository_path) if profile.repository_path is not None else None
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    target_id, name, environment, services_json,
                    profile_path, repository_path, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    name = excluded.name,
                    environment = excluded.environment,
                    services_json = excluded.services_json,
                    profile_path = excluded.profile_path,
                    repository_path = excluded.repository_path,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.id,
                    profile.name,
                    profile.environment,
                    json.dumps(sorted(profile.services), ensure_ascii=False),
                    str(profile_path),
                    repository,
                    _now_iso(),
                ),
            )

    def get_project(self, target_id: str) -> ApiProject:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE target_id = ?",
                (target_id,),
            ).fetchone()
        if row is None:
            raise KeyError(target_id)
        return _project_from_row(row)

    def list_projects(self) -> list[ApiProject]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY target_id"
            ).fetchall()
        return [_project_from_row(row) for row in rows]

    def create_run(
        self,
        *,
        run_id: str,
        target_id: str,
        agent_mode: str | None,
        max_iterations: int,
        analysis_request: str | None,
        artifact_dir: Path,
    ) -> ApiRun:
        created_at = _now_iso()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, target_id, status, agent_mode, max_iterations,
                    analysis_request, artifact_dir, created_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    target_id,
                    agent_mode,
                    max_iterations,
                    analysis_request,
                    str(artifact_dir.resolve()),
                    created_at,
                ),
            )
        return self.get_run(run_id)

    def mark_running(self, run_id: str) -> None:
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE runs
                SET status = 'running', started_at = ?, error_json = NULL
                WHERE run_id = ?
                """,
                (_now_iso(), run_id),
            )
            if updated.rowcount != 1:
                raise KeyError(run_id)

    def mark_finished(
        self,
        run_id: str,
        *,
        exit_code: int,
        gate_decision: str | None,
        error: ErrorDetail | None = None,
    ) -> None:
        status = "technical_failure" if exit_code == 2 else "completed"
        error_json = error.model_dump_json() if error is not None else None
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE runs
                SET status = ?, finished_at = ?, exit_code = ?,
                    gate_decision = ?, error_json = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    _now_iso(),
                    exit_code,
                    gate_decision,
                    error_json,
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise KeyError(run_id)

    def mark_failed(self, run_id: str, error: ErrorDetail) -> None:
        self.mark_finished(
            run_id,
            exit_code=2,
            gate_decision="fail",
            error=error,
        )

    def get_run(self, run_id: str) -> ApiRun:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run_from_row(row)

    def list_runs(self, *, limit: int = 100) -> list[ApiRun]:
        bounded = max(1, min(limit, 500))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def artifact_dir(self, run_id: str) -> Path:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT artifact_dir FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Path(row["artifact_dir"]).resolve()

    def replace_results(
        self,
        run_id: str,
        *,
        reports: list[tuple[FinalReport, Path]],
    ) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM evidence WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM findings WHERE run_id = ?", (run_id,))
            for report, report_path in reports:
                finding = report.finding
                connection.execute(
                    """
                    INSERT INTO findings (
                        run_id, finding_id, status, source, rule_id, title,
                        severity, service, file, line_start, report_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        report.finding_id,
                        report.status,
                        finding.source,
                        finding.rule_id,
                        finding.title,
                        finding.severity,
                        finding.service,
                        finding.file,
                        finding.line_start,
                        str(report_path.resolve()),
                    ),
                )
                for item in report.evidence:
                    connection.execute(
                        """
                        INSERT INTO evidence (
                            run_id, evidence_id, finding_id, payload_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            item.id,
                            report.finding_id,
                            item.model_dump_json(),
                        ),
                    )

    def list_findings(self, run_id: str) -> list[ApiFinding]:
        self.get_run(run_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM findings
                WHERE run_id = ?
                ORDER BY finding_id
                """,
                (run_id,),
            ).fetchall()
        return [_finding_from_row(row) for row in rows]

    def get_finding(self, run_id: str, finding_id: str) -> ApiFinding:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM findings
                WHERE run_id = ? AND finding_id = ?
                """,
                (run_id, finding_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"{run_id}:{finding_id}")
        return _finding_from_row(row)

    def report_path(self, run_id: str, finding_id: str) -> Path:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT report_path FROM findings
                WHERE run_id = ? AND finding_id = ?
                """,
                (run_id, finding_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"{run_id}:{finding_id}")
        path = Path(row["report_path"]).resolve()
        _ensure_inside(path, self.artifact_dir(run_id))
        return path

    def list_evidence(self, run_id: str, *, finding_id: str | None = None) -> list[ApiEvidence]:
        self.get_run(run_id)
        query = (
            "SELECT finding_id, payload_json FROM evidence WHERE run_id = ? "
            "ORDER BY finding_id, evidence_id"
        )
        parameters: tuple[object, ...] = (run_id,)
        if finding_id is not None:
            query = (
                "SELECT finding_id, payload_json FROM evidence "
                "WHERE run_id = ? AND finding_id = ? ORDER BY evidence_id"
            )
            parameters = (run_id, finding_id)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            ApiEvidence(
                run_id=run_id,
                finding_id=row["finding_id"],
                evidence=Evidence.model_validate_json(row["payload_json"]),
            )
            for row in rows
        ]

    def create_chat_session(self, *, target_id: str, title: str) -> ChatSession:
        self.get_project(target_id)
        session_id = f"chat-{uuid4().hex}"
        now = _now_iso()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, target_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, target_id, title, now, now),
            )
        return self.get_chat_session(session_id)

    def get_chat_session(self, session_id: str) -> ChatSession:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return _chat_session_from_row(row)

    def list_chat_sessions(self, *, limit: int = 100) -> list[ChatSession]:
        bounded = max(1, min(limit, 500))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [_chat_session_from_row(row) for row in rows]

    def set_chat_run(self, session_id: str, run_id: str) -> None:
        self.get_run(run_id)
        with self._connection() as connection:
            now = _now_iso()
            updated = connection.execute(
                """
                UPDATE chat_sessions
                SET active_run_id = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (run_id, now, session_id),
            )
            if updated.rowcount != 1:
                raise KeyError(session_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO chat_session_runs (session_id, run_id, linked_at)
                VALUES (?, ?, ?)
                """,
                (session_id, run_id, now),
            )

    def list_chat_run_ids(self, session_id: str) -> list[str]:
        self.get_chat_session(session_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT run_id
                FROM chat_session_runs
                WHERE session_id = ?
                ORDER BY linked_at, run_id
                """,
                (session_id,),
            ).fetchall()
        return [str(row["run_id"]) for row in rows]

    def append_chat_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        kind: str = "text",
        run_id: str | None = None,
    ) -> ChatMessage:
        self.get_chat_session(session_id)
        if run_id is not None:
            self.get_run(run_id)
        message_id = f"msg-{uuid4().hex}"
        now = _now_iso()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO chat_messages (
                    message_id, session_id, role, kind, content, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, kind, content, run_id, now),
            )
            connection.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
        return self.get_chat_message(message_id)

    def get_chat_message(self, message_id: str) -> ChatMessage:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM chat_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        if row is None:
            raise KeyError(message_id)
        return _chat_message_from_row(row)

    def list_chat_messages(self, session_id: str) -> list[ChatMessage]:
        self.get_chat_session(session_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at, message_id
                """,
                (session_id,),
            ).fetchall()
        return [_chat_message_from_row(row) for row in rows]


def _project_from_row(row: sqlite3.Row) -> ApiProject:
    repository = row["repository_path"]
    return ApiProject(
        id=row["target_id"],
        name=row["name"],
        environment=row["environment"],
        services=json.loads(row["services_json"]),
        repository_available=bool(repository) and Path(repository).expanduser().is_dir(),
    )


def _run_from_row(row: sqlite3.Row) -> ApiRun:
    error = ErrorDetail.model_validate_json(row["error_json"]) if row["error_json"] else None
    return ApiRun(
        id=row["run_id"],
        target_id=row["target_id"],
        status=row["status"],
        agent_mode=row["agent_mode"],
        max_iterations=row["max_iterations"],
        analysis_request=row["analysis_request"],
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        exit_code=row["exit_code"],
        gate_decision=row["gate_decision"],
        error=error,
    )


def _finding_from_row(row: sqlite3.Row) -> ApiFinding:
    return ApiFinding(
        run_id=row["run_id"],
        finding_id=row["finding_id"],
        status=row["status"],
        source=row["source"],
        rule_id=row["rule_id"],
        title=row["title"],
        severity=row["severity"],
        service=row["service"],
        file=row["file"],
        line_start=row["line_start"],
        report_available=Path(row["report_path"]).is_file(),
    )


def _chat_session_from_row(row: sqlite3.Row) -> ChatSession:
    return ChatSession(
        id=row["session_id"],
        target_id=row["target_id"],
        title=row["title"],
        active_run_id=row["active_run_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _chat_message_from_row(row: sqlite3.Row) -> ChatMessage:
    return ChatMessage(
        id=row["message_id"],
        session_id=row["session_id"],
        role=row["role"],
        kind=row["kind"],
        content=row["content"],
        run_id=row["run_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Artifact path escaped the registered run directory") from exc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
