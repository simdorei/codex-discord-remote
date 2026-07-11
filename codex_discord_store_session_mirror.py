from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import time
from typing import Final, TypeAlias

import codex_discord_gpt_delivery as gpt_delivery
from codex_discord_gpt_ownership import (
    CodexThreadId,
    DiscordThreadId,
    MirrorThreadLifecycleState,
    MirrorThreadManagedBy,
)
from codex_discord_store_schema import init_store_schema


_TargetRow: TypeAlias = tuple[str | None, str | None, int, int]
_IdentityRow: TypeAlias = tuple[str, int, int, str, str, str, float, int]
_CursorRow: TypeAlias = tuple[str | None, int | None]
_OffsetRow: TypeAlias = tuple[str | None, int | None, float | None]
_ACTIVE_TARGET_FILTER: Final = (
    "owner.lifecycle_state = 'active' AND ("
    + "owner.managed_by = 'ordinary' OR "
    + "(owner.managed_by = 'gpt_chat' AND owner.project_key = 'codex:chats')) "
    + "AND (SELECT COUNT(*) FROM mirror_threads AS duplicate "
    + "WHERE duplicate.discord_thread_id = owner.discord_thread_id) = 1"
)


def _init_mirror_db(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        init_store_schema(conn)


def get_session_mirror_targets(
    db_path: Path,
    *,
    limit: int = 100,
) -> list[dict[str, str | int]]:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        rows: list[_TargetRow] = conn.execute(
            "SELECT codex_thread_id, thread_title, discord_channel_id, "
            + "discord_thread_id FROM mirror_threads AS owner WHERE "
            + _ACTIVE_TARGET_FILTER
            + " ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [
        {
            "codex_thread_id": str(row[0] or ""),
            "thread_title": str(row[1] or ""),
            "discord_channel_id": int(row[2]),
            "discord_thread_id": int(row[3]),
        }
        for row in rows
        if row[0] and row[3]
    ]


def get_session_mirror_delivery_identity(
    db_path: Path,
    codex_thread_id: str,
) -> gpt_delivery.ActiveDeliveryIdentity | None:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        rows: list[_IdentityRow] = conn.execute(
            "SELECT owner.codex_thread_id, owner.discord_channel_id, "
            + "owner.discord_thread_id, owner.project_key, owner.managed_by, "
            + "owner.lifecycle_state, owner.updated_at, "
            + "(SELECT COUNT(*) FROM mirror_threads AS duplicate "
            + "WHERE duplicate.discord_thread_id = owner.discord_thread_id) "
            + "FROM mirror_threads AS owner WHERE owner.codex_thread_id = ?",
            (str(codex_thread_id),),
        ).fetchall()
    if len(rows) != 1 or rows[0][7] != 1:
        return None
    row = rows[0]
    return gpt_delivery.ActiveDeliveryIdentity(
        codex_thread_id=CodexThreadId(row[0]),
        discord_channel_id=row[1],
        discord_thread_id=DiscordThreadId(row[2]),
        project_key=row[3],
        managed_by=MirrorThreadManagedBy(row[4]),
        lifecycle_state=MirrorThreadLifecycleState(row[5]),
        updated_at=row[6],
    )


def is_exact_active_session_mirror_target(
    db_path: Path,
    codex_thread_id: str,
    discord_thread_id: int,
) -> bool:
    identity = get_session_mirror_delivery_identity(db_path, codex_thread_id)
    return (
        identity is not None
        and identity.is_active_session_target
        and int(identity.discord_thread_id) == int(discord_thread_id)
    )


def claim_session_mirror_event(
    db_path: Path,
    event_digest: str,
    codex_thread_id: str,
    *,
    now: float | None = None,
) -> bool:
    current = time.time() if now is None else now
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        result = conn.execute(
            "INSERT OR IGNORE INTO codex_session_mirror_events ("
            + "event_digest, codex_thread_id, created_at) VALUES (?, ?, ?)",
            (str(event_digest), str(codex_thread_id), current),
        )
        claimed = result.rowcount == 1
    return claimed


def has_session_mirror_event(
    db_path: Path,
    event_digest: str,
    codex_thread_id: str,
) -> bool:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        event_exists = (
            conn.execute(
                "SELECT 1 FROM codex_session_mirror_events "
                + "WHERE event_digest = ? AND codex_thread_id = ?",
                (str(event_digest), str(codex_thread_id)),
            ).fetchone()
            is not None
        )
    return event_exists


def cleanup_session_mirror_events(
    db_path: Path,
    *,
    retention_seconds: float,
    now: float | None = None,
) -> int:
    current = time.time() if now is None else now
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        result = conn.execute(
            "DELETE FROM codex_session_mirror_events WHERE created_at < ?",
            (current - retention_seconds,),
        )
        deleted = result.rowcount
    return deleted


def get_or_init_session_mirror_cursor(
    db_path: Path,
    codex_thread_id: str,
    rollout_path: str,
    initial_cursor: int,
    *,
    now: float | None = None,
) -> int:
    current = time.time() if now is None else now
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        rows: list[_CursorRow] = conn.execute(
            "SELECT rollout_path, cursor FROM codex_session_mirror_offsets "
            + "WHERE codex_thread_id = ?",
            (str(codex_thread_id),),
        ).fetchall()
        stored_cursor = _parse_cursor_row(rows[0] if rows else None, rollout_path)
        if stored_cursor is not None:
            return stored_cursor
        _ = conn.execute(
            "INSERT OR REPLACE INTO codex_session_mirror_offsets "
            + "(codex_thread_id, rollout_path, cursor, updated_at) VALUES (?, ?, ?, ?)",
            (str(codex_thread_id), str(rollout_path), int(initial_cursor), current),
        )
    return int(initial_cursor)


def get_session_mirror_offset(
    db_path: Path,
    codex_thread_id: str,
) -> tuple[str, int, float] | None:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        rows: list[_OffsetRow] = conn.execute(
            "SELECT rollout_path, cursor, updated_at "
            + "FROM codex_session_mirror_offsets WHERE codex_thread_id = ?",
            (str(codex_thread_id),),
        ).fetchall()
    return _parse_offset_row(rows[0] if rows else None)


def _parse_cursor_row(row: _CursorRow | None, rollout_path: str) -> int | None:
    if row is None:
        return None
    stored_path, stored_cursor = row
    if (stored_path or "") != rollout_path:
        return None
    return stored_cursor or 0


def _parse_offset_row(row: _OffsetRow | None) -> tuple[str, int, float] | None:
    if row is None:
        return None
    rollout_path, cursor, updated_at = row
    return rollout_path or "", cursor or 0, updated_at or 0.0


def update_session_mirror_cursor(
    db_path: Path,
    codex_thread_id: str,
    rollout_path: str,
    cursor: int,
    *,
    now: float | None = None,
) -> None:
    current = time.time() if now is None else now
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        _ = conn.execute(
            "INSERT OR REPLACE INTO codex_session_mirror_offsets "
            + "(codex_thread_id, rollout_path, cursor, updated_at) VALUES (?, ?, ?, ?)",
            (str(codex_thread_id), str(rollout_path), int(cursor), current),
        )
