from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
import json
from pathlib import Path
import sqlite3
import time
from collections.abc import Callable
from typing import TypeAlias, cast

from codex_discord_store_schema import init_store_schema


SQLiteCell: TypeAlias = str | int | float | bytes | None
SQLiteRow: TypeAlias = tuple[SQLiteCell, ...]
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
DecodeJsonValue: TypeAlias = Callable[[str], JsonValue]
_decode_json_value: DecodeJsonValue = json.loads


@unique
class QueueJobState(StrEnum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class StoredQueueJob:
    job_id: str
    target_thread_id: str
    channel_id: int
    owner_user_id: int | None
    discord_message_id: int | None
    prompt: str
    queued: bool
    ack_sent: bool
    state: QueueJobState
    attempt_count: int
    turn_id: str | None
    baseline_turn_ids: tuple[str, ...]
    last_error: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class QueueEnqueueResult:
    job: StoredQueueJob
    created: bool


class QueueJobNotFoundError(LookupError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Durable queue job not found: {job_id}")
        self.job_id: str = job_id


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    init_store_schema(conn)
    return conn


def _record(row: SQLiteRow) -> StoredQueueJob:
    baseline = _decode_baseline(str(row[11] or "[]"))
    return StoredQueueJob(
        job_id=str(row[0]),
        target_thread_id=str(row[1]),
        channel_id=int(cast(int, row[2])),
        owner_user_id=int(cast(int, row[3])) if row[3] is not None else None,
        discord_message_id=int(cast(int, row[4])) if row[4] is not None else None,
        prompt=str(row[5]),
        queued=bool(row[6]),
        ack_sent=bool(row[7]),
        state=QueueJobState(str(row[8])),
        attempt_count=int(cast(int, row[9])),
        turn_id=str(row[10]) if row[10] is not None else None,
        baseline_turn_ids=baseline,
        last_error=str(row[12] or ""),
        created_at=float(cast(float, row[13])),
        updated_at=float(cast(float, row[14])),
    )


def _decode_baseline(raw: str) -> tuple[str, ...]:
    decoded = _decode_json_value(raw)
    if not isinstance(decoded, list):
        return ()
    return tuple(str(value) for value in decoded)


def _select_job(conn: sqlite3.Connection, job_id: str) -> StoredQueueJob:
    row = cast(SQLiteRow | None, conn.execute("SELECT * FROM codex_turn_queue WHERE job_id = ?", (job_id,)).fetchone())
    if row is None:
        raise QueueJobNotFoundError(job_id)
    return _record(row)


def enqueue_queue_job(
    db_path: Path,
    *,
    job_id: str,
    target_thread_id: str,
    channel_id: int,
    owner_user_id: int | None,
    discord_message_id: int | None,
    prompt: str,
    queued: bool,
    ack_sent: bool,
    created_at: float | None = None,
) -> QueueEnqueueResult:
    now = time.time() if created_at is None else created_at
    with _connect(db_path) as conn:
        result = conn.execute(
            "INSERT OR IGNORE INTO codex_turn_queue "
            + "(job_id, target_thread_id, channel_id, owner_user_id, discord_message_id, prompt, "
            + "queued, ack_sent, state, attempt_count, baseline_turn_ids, created_at, updated_at) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '[]', ?, ?)",
            (
                job_id,
                target_thread_id,
                channel_id,
                owner_user_id,
                discord_message_id,
                prompt,
                int(queued),
                int(ack_sent),
                QueueJobState.PENDING.value,
                now,
                now,
            ),
        )
        created = result.rowcount == 1
        if created:
            job = _select_job(conn, job_id)
        elif discord_message_id is not None:
            row = cast(
                SQLiteRow | None,
                conn.execute(
                    "SELECT * FROM codex_turn_queue WHERE discord_message_id = ?",
                    (discord_message_id,),
                ).fetchone(),
            )
            if row is None:
                raise QueueJobNotFoundError(job_id)
            job = _record(row)
        else:
            job = _select_job(conn, job_id)
    return QueueEnqueueResult(job, created)


def list_queue_jobs(db_path: Path, target_thread_id: str | None = None) -> list[StoredQueueJob]:
    with _connect(db_path) as conn:
        if target_thread_id is None:
            rows = cast(
                list[SQLiteRow],
                conn.execute("SELECT * FROM codex_turn_queue ORDER BY created_at, job_id").fetchall(),
            )
        else:
            rows = cast(
                list[SQLiteRow],
                conn.execute(
                    "SELECT * FROM codex_turn_queue WHERE target_thread_id = ? ORDER BY created_at, job_id",
                    (target_thread_id,),
                ).fetchall(),
            )
    return [_record(row) for row in rows]


def begin_queue_job_attempt(
    db_path: Path,
    job_id: str,
    *,
    baseline_turn_ids: tuple[str, ...],
) -> StoredQueueJob:
    with _connect(db_path) as conn:
        now = time.time()
        result = conn.execute(
            "UPDATE codex_turn_queue SET state = ?, attempt_count = attempt_count + 1, "
            + "turn_id = NULL, baseline_turn_ids = ?, last_error = '', updated_at = ? WHERE job_id = ?",
            (QueueJobState.STARTING.value, json.dumps(baseline_turn_ids), now, job_id),
        )
        if result.rowcount != 1:
            raise QueueJobNotFoundError(job_id)
        return _select_job(conn, job_id)


def mark_queue_job_running(db_path: Path, job_id: str, turn_id: str) -> StoredQueueJob:
    with _connect(db_path) as conn:
        result = conn.execute(
            "UPDATE codex_turn_queue SET state = ?, turn_id = ?, updated_at = ? WHERE job_id = ?",
            (QueueJobState.RUNNING.value, turn_id, time.time(), job_id),
        )
        if result.rowcount != 1:
            raise QueueJobNotFoundError(job_id)
        return _select_job(conn, job_id)


def complete_queue_job(db_path: Path, job_id: str) -> bool:
    with _connect(db_path) as conn:
        result = conn.execute("DELETE FROM codex_turn_queue WHERE job_id = ?", (job_id,))
        return result.rowcount == 1


def flush_queue_jobs(db_path: Path, target_thread_id: str) -> list[StoredQueueJob]:
    with _connect(db_path) as conn:
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                "SELECT * FROM codex_turn_queue WHERE target_thread_id = ? ORDER BY created_at, job_id",
                (target_thread_id,),
            ).fetchall(),
        )
        _ = conn.execute("DELETE FROM codex_turn_queue WHERE target_thread_id = ?", (target_thread_id,))
    return [_record(row) for row in rows]


def retract_queue_job(
    db_path: Path,
    target_thread_id: str,
    *,
    channel_id: int | None,
    owner_user_id: int | None,
) -> StoredQueueJob | None:
    clauses = ["target_thread_id = ?", "state = ?"]
    params: list[SQLiteCell] = [target_thread_id, QueueJobState.PENDING.value]
    if channel_id is not None:
        clauses.append("channel_id = ?")
        params.append(channel_id)
    if owner_user_id is not None:
        clauses.append("owner_user_id = ?")
        params.append(owner_user_id)
    where = " AND ".join(clauses)
    with _connect(db_path) as conn:
        row = cast(
            SQLiteRow | None,
            conn.execute(
                f"SELECT * FROM codex_turn_queue WHERE {where} ORDER BY created_at DESC, job_id DESC LIMIT 1",
                params,
            ).fetchone(),
        )
        if row is None:
            return None
        job = _record(row)
        _ = conn.execute("DELETE FROM codex_turn_queue WHERE job_id = ?", (job.job_id,))
        return job
