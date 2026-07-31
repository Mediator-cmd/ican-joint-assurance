"""SQLite persistence for M4 runtime-session control state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from .runtime_models import RuntimeFailure, RuntimeStatus, SimulationSpeed
from .runtime_projection import RuntimeProjectionSource


class RuntimeRepositoryError(Exception):
    """Base class for runtime persistence failures."""


class RuntimePersistenceError(RuntimeRepositoryError):
    pass


class RuntimeSessionAlreadyExistsError(RuntimeRepositoryError):
    pass


class RuntimeSessionNotFoundError(RuntimeRepositoryError):
    pass


class RuntimeRevisionConflictError(RuntimeRepositoryError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"expected runtime revision {expected_revision}, current revision {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


@dataclass(frozen=True, slots=True)
class RuntimeSessionRecord:
    session_id: str
    scenario_id: str
    initial_scenario_version: int
    current_scenario_version: int
    initial_plan_id: str
    active_plan_id: str
    candidate_plan_id: str | None
    status: RuntimeStatus
    revision: int
    simulation_time: datetime
    simulation_window_start: datetime
    simulation_window_end: datetime
    speed: SimulationSpeed
    failure: RuntimeFailure | None
    created_at: datetime
    updated_at: datetime
    projection_source: RuntimeProjectionSource | None = None


@dataclass(frozen=True, slots=True)
class RuntimeControlAuditRecord:
    audit_id: int
    session_id: str
    action: str
    revision_before: int | None
    revision_after: int
    status_before: RuntimeStatus | None
    status_after: RuntimeStatus
    simulation_time: datetime
    created_at: datetime
    summary: str


class SQLiteRuntimeSessionRepository:
    """Persist runtime boundaries atomically without storing per-second ticks."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        try:
            self._connection = sqlite3.connect(
                self.database_path,
                timeout=5,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            if self.database_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize_schema()
        except sqlite3.DatabaseError as error:
            raise RuntimePersistenceError("runtime database initialization failed") from error

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_sessions (
                session_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                initial_scenario_version INTEGER NOT NULL CHECK (initial_scenario_version >= 1),
                current_scenario_version INTEGER NOT NULL CHECK (current_scenario_version >= 1),
                initial_plan_id TEXT NOT NULL,
                active_plan_id TEXT NOT NULL,
                candidate_plan_id TEXT,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                simulation_time TEXT NOT NULL,
                simulation_window_start TEXT NOT NULL,
                simulation_window_end TEXT NOT NULL,
                speed INTEGER NOT NULL CHECK (speed IN (1, 5, 15)),
                failure_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                projection_source_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_runtime_sessions_updated
                ON runtime_sessions(updated_at DESC, session_id ASC);
            CREATE INDEX IF NOT EXISTS idx_runtime_sessions_scenario
                ON runtime_sessions(scenario_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_runtime_sessions_status
                ON runtime_sessions(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS runtime_control_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                action TEXT NOT NULL,
                revision_before INTEGER,
                revision_after INTEGER NOT NULL CHECK (revision_after >= 1),
                status_before TEXT,
                status_after TEXT NOT NULL,
                simulation_time TEXT NOT NULL,
                created_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES runtime_sessions(session_id)
            );

            CREATE INDEX IF NOT EXISTS idx_runtime_audit_session
                ON runtime_control_audit(session_id, audit_id ASC);
            """
        )
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(runtime_sessions)").fetchall()
        }
        if "projection_source_json" not in columns:
            self._connection.execute(
                "ALTER TABLE runtime_sessions ADD COLUMN projection_source_json TEXT"
            )

    def create_session(self, record: RuntimeSessionRecord) -> RuntimeSessionRecord:
        if record.revision != 1:
            raise ValueError("a new runtime session must start at revision 1")
        values = self._record_values(record)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """
                    INSERT INTO runtime_sessions (
                        session_id, scenario_id, initial_scenario_version,
                        current_scenario_version, initial_plan_id, active_plan_id,
                        candidate_plan_id, status, revision, simulation_time,
                        simulation_window_start, simulation_window_end, speed,
                        failure_json, created_at, updated_at, projection_source_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                self._insert_audit(
                    record=record,
                    action="session_created",
                    revision_before=None,
                    status_before=None,
                    summary="运行会话已创建",
                )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                self._rollback_if_needed()
                raise RuntimeSessionAlreadyExistsError(
                    f"runtime session {record.session_id} already exists"
                ) from error
            except sqlite3.DatabaseError as error:
                self._rollback_if_needed()
                raise RuntimePersistenceError("runtime session creation failed") from error
        return record

    def get_session(self, session_id: str) -> RuntimeSessionRecord:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT * FROM runtime_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            except sqlite3.DatabaseError as error:
                raise RuntimePersistenceError("runtime session lookup failed") from error
        if row is None:
            raise RuntimeSessionNotFoundError(f"runtime session {session_id} was not found")
        return self._row_to_record(row)

    def list_sessions(
        self,
        *,
        scenario_id: str | None = None,
        status: RuntimeStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[tuple[RuntimeSessionRecord, ...], int]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if scenario_id is not None:
            clauses.append("scenario_id = ?")
            parameters.append(scenario_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            try:
                total = self._connection.execute(
                    f"SELECT COUNT(*) FROM runtime_sessions{where}",
                    parameters,
                ).fetchone()[0]
                rows = self._connection.execute(
                    f"""
                    SELECT * FROM runtime_sessions{where}
                    ORDER BY updated_at DESC, session_id ASC
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, limit, offset],
                ).fetchall()
            except sqlite3.DatabaseError as error:
                raise RuntimePersistenceError("runtime session listing failed") from error
        return tuple(self._row_to_record(row) for row in rows), total

    def update_session(
        self,
        record: RuntimeSessionRecord,
        *,
        expected_revision: int,
        action: str,
        summary: str,
    ) -> RuntimeSessionRecord:
        if record.revision != expected_revision + 1:
            raise ValueError("updated runtime session revision must increase exactly once")
        values = self._record_values(record)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                previous_row = self._connection.execute(
                    "SELECT * FROM runtime_sessions WHERE session_id = ?",
                    (record.session_id,),
                ).fetchone()
                if previous_row is None:
                    raise RuntimeSessionNotFoundError(
                        f"runtime session {record.session_id} was not found"
                    )
                previous = self._row_to_record(previous_row)
                cursor = self._connection.execute(
                    """
                    UPDATE runtime_sessions SET
                        scenario_id = ?, initial_scenario_version = ?,
                        current_scenario_version = ?, initial_plan_id = ?,
                        active_plan_id = ?, candidate_plan_id = ?, status = ?,
                        revision = ?, simulation_time = ?,
                        simulation_window_start = ?, simulation_window_end = ?,
                        speed = ?, failure_json = ?, created_at = ?, updated_at = ?,
                        projection_source_json = ?
                    WHERE session_id = ? AND revision = ?
                    """,
                    (*values[1:], record.session_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    current_revision = self._connection.execute(
                        "SELECT revision FROM runtime_sessions WHERE session_id = ?",
                        (record.session_id,),
                    ).fetchone()[0]
                    raise RuntimeRevisionConflictError(expected_revision, current_revision)
                self._insert_audit(
                    record=record,
                    action=action,
                    revision_before=previous.revision,
                    status_before=previous.status,
                    summary=summary,
                )
                self._connection.execute("COMMIT")
            except (RuntimeSessionNotFoundError, RuntimeRevisionConflictError):
                self._rollback_if_needed()
                raise
            except sqlite3.DatabaseError as error:
                self._rollback_if_needed()
                raise RuntimePersistenceError("runtime session update failed") from error
        return record

    def attach_projection_source(
        self,
        session_id: str,
        source: RuntimeProjectionSource,
    ) -> RuntimeSessionRecord:
        serialized = json.dumps(source.model_dump(mode="json"), ensure_ascii=False)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT projection_source_json FROM runtime_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeSessionNotFoundError(
                        f"runtime session {session_id} was not found"
                    )
                if row["projection_source_json"] is None:
                    self._connection.execute(
                        """
                        UPDATE runtime_sessions SET projection_source_json = ?
                        WHERE session_id = ? AND projection_source_json IS NULL
                        """,
                        (serialized, session_id),
                    )
                else:
                    existing = RuntimeProjectionSource.model_validate(
                        json.loads(row["projection_source_json"])
                    )
                    if existing != source:
                        raise RuntimePersistenceError(
                            "runtime projection source is immutable after creation"
                        )
                self._connection.execute("COMMIT")
            except (RuntimeSessionNotFoundError, RuntimePersistenceError):
                self._rollback_if_needed()
                raise
            except (sqlite3.DatabaseError, ValueError, json.JSONDecodeError) as error:
                self._rollback_if_needed()
                raise RuntimePersistenceError("runtime projection source update failed") from error
        return self.get_session(session_id)

    def recover_interrupted_sessions(self, recovered_at: datetime) -> tuple[str, ...]:
        """Pause interrupted work once and record the recovery as a durable boundary."""

        self._serialize_datetime(recovered_at)
        recovered_ids: list[str] = []
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                rows = self._connection.execute(
                    "SELECT * FROM runtime_sessions WHERE status IN (?, ?)",
                    (RuntimeStatus.RUNNING.value, RuntimeStatus.REPLANNING.value),
                ).fetchall()
                for row in rows:
                    previous = self._row_to_record(row)
                    recovered = replace(
                        previous,
                        status=RuntimeStatus.PAUSED,
                        candidate_plan_id=None,
                        revision=previous.revision + 1,
                        updated_at=max(previous.updated_at, recovered_at),
                    )
                    cursor = self._connection.execute(
                        """
                        UPDATE runtime_sessions SET
                            candidate_plan_id = NULL, status = ?, revision = ?, updated_at = ?
                        WHERE session_id = ? AND revision = ?
                        """,
                        (
                            recovered.status.value,
                            recovered.revision,
                            self._serialize_datetime(recovered.updated_at),
                            recovered.session_id,
                            previous.revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeRevisionConflictError(
                            previous.revision,
                            self._current_revision(recovered.session_id),
                        )
                    self._insert_audit(
                        record=recovered,
                        action="service_restarted",
                        revision_before=previous.revision,
                        status_before=previous.status,
                        summary="服务重启后已安全恢复为暂停状态",
                    )
                    recovered_ids.append(recovered.session_id)
                self._connection.execute("COMMIT")
            except RuntimeRevisionConflictError:
                self._rollback_if_needed()
                raise
            except sqlite3.DatabaseError as error:
                self._rollback_if_needed()
                raise RuntimePersistenceError("runtime session recovery failed") from error
        return tuple(recovered_ids)

    def list_audit_records(self, session_id: str) -> tuple[RuntimeControlAuditRecord, ...]:
        self.get_session(session_id)
        with self._lock:
            try:
                rows = self._connection.execute(
                    """
                    SELECT * FROM runtime_control_audit
                    WHERE session_id = ? ORDER BY audit_id ASC
                    """,
                    (session_id,),
                ).fetchall()
            except sqlite3.DatabaseError as error:
                raise RuntimePersistenceError("runtime audit lookup failed") from error
        return tuple(self._row_to_audit(row) for row in rows)

    @staticmethod
    def replace_record(record: RuntimeSessionRecord, **changes: Any) -> RuntimeSessionRecord:
        return replace(record, **changes)

    def _record_values(self, record: RuntimeSessionRecord) -> tuple[Any, ...]:
        failure_json = (
            json.dumps(record.failure.model_dump(mode="json"), ensure_ascii=False)
            if record.failure is not None
            else None
        )
        projection_source_json = (
            json.dumps(record.projection_source.model_dump(mode="json"), ensure_ascii=False)
            if record.projection_source is not None
            else None
        )
        return (
            record.session_id,
            record.scenario_id,
            record.initial_scenario_version,
            record.current_scenario_version,
            record.initial_plan_id,
            record.active_plan_id,
            record.candidate_plan_id,
            record.status.value,
            record.revision,
            self._serialize_datetime(record.simulation_time),
            self._serialize_datetime(record.simulation_window_start),
            self._serialize_datetime(record.simulation_window_end),
            record.speed.value,
            failure_json,
            self._serialize_datetime(record.created_at),
            self._serialize_datetime(record.updated_at),
            projection_source_json,
        )

    def _insert_audit(
        self,
        *,
        record: RuntimeSessionRecord,
        action: str,
        revision_before: int | None,
        status_before: RuntimeStatus | None,
        summary: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO runtime_control_audit (
                session_id, action, revision_before, revision_after,
                status_before, status_after, simulation_time, created_at, summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.session_id,
                action,
                revision_before,
                record.revision,
                status_before.value if status_before is not None else None,
                record.status.value,
                self._serialize_datetime(record.simulation_time),
                self._serialize_datetime(record.updated_at),
                summary,
            ),
        )

    def _current_revision(self, session_id: str) -> int:
        row = self._connection.execute(
            "SELECT revision FROM runtime_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise RuntimeSessionNotFoundError(f"runtime session {session_id} was not found")
        return int(row[0])

    def _rollback_if_needed(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    @staticmethod
    def _serialize_datetime(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime persistence requires timezone-aware timestamps")
        return value.isoformat()

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RuntimePersistenceError("runtime database contains a naive timestamp")
        return parsed

    def _row_to_record(self, row: sqlite3.Row) -> RuntimeSessionRecord:
        try:
            failure = (
                RuntimeFailure.model_validate(json.loads(row["failure_json"]))
                if row["failure_json"] is not None
                else None
            )
            projection_source = (
                RuntimeProjectionSource.model_validate(
                    json.loads(row["projection_source_json"])
                )
                if row["projection_source_json"] is not None
                else None
            )
            return RuntimeSessionRecord(
                session_id=row["session_id"],
                scenario_id=row["scenario_id"],
                initial_scenario_version=row["initial_scenario_version"],
                current_scenario_version=row["current_scenario_version"],
                initial_plan_id=row["initial_plan_id"],
                active_plan_id=row["active_plan_id"],
                candidate_plan_id=row["candidate_plan_id"],
                status=RuntimeStatus(row["status"]),
                revision=row["revision"],
                simulation_time=self._parse_datetime(row["simulation_time"]),
                simulation_window_start=self._parse_datetime(row["simulation_window_start"]),
                simulation_window_end=self._parse_datetime(row["simulation_window_end"]),
                speed=SimulationSpeed(row["speed"]),
                failure=failure,
                created_at=self._parse_datetime(row["created_at"]),
                updated_at=self._parse_datetime(row["updated_at"]),
                projection_source=projection_source,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimePersistenceError("runtime database contains invalid session data") from error

    def _row_to_audit(self, row: sqlite3.Row) -> RuntimeControlAuditRecord:
        try:
            return RuntimeControlAuditRecord(
                audit_id=row["audit_id"],
                session_id=row["session_id"],
                action=row["action"],
                revision_before=row["revision_before"],
                revision_after=row["revision_after"],
                status_before=(
                    RuntimeStatus(row["status_before"])
                    if row["status_before"] is not None
                    else None
                ),
                status_after=RuntimeStatus(row["status_after"]),
                simulation_time=self._parse_datetime(row["simulation_time"]),
                created_at=self._parse_datetime(row["created_at"]),
                summary=row["summary"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimePersistenceError("runtime database contains invalid audit data") from error
