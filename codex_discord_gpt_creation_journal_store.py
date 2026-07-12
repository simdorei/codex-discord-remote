"""SQLite storage boundary and creation-journal value types."""

from __future__ import annotations

# Pyright flags mandatory assert_never defaults after proving every enum case.
# pyright: reportUnnecessaryComparison=false

from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
import sqlite3
from typing import Final, Literal, NewType, TypeAlias, assert_never, override

from codex_discord_gpt_lifecycle import GptCapacityExceededError
from codex_discord_gpt_migration import GPT_PROJECT_KEY
from codex_discord_gpt_ownership import (
    CodexThreadId,
    DiscordChannelId,
    DiscordThreadId,
    MirrorThreadLifecycleState,
)
from codex_discord_store_schema import init_store_schema


GptCreationNonce = NewType("GptCreationNonce", str)
GptCreationMarker = NewType("GptCreationMarker", str)
JournalRow: TypeAlias = tuple[str, str, str, int, str, str, int | None, float, float]
MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]
ExistsRow: TypeAlias = tuple[int]
GptCreationConflict: TypeAlias = Literal[
    "existing_operation",
    "existing_mapping",
    "nonce_collision",
    "journal_mismatch",
    "malformed_journal",
    "status_order",
    "mapping_identity",
    "discord_ownership",
]
GptCreationMutation: TypeAlias = Literal["mark_started", "complete", "cancel"]
_SQLITE_TIMEOUT_SECONDS: Final = 10.0


@unique
class GptCreationStatus(StrEnum):
    PREPARED = "prepared"
    CREATE_STARTED = "create_started"
    DISCORD_IDENTIFIED = "discord_identified"


@dataclass(frozen=True, slots=True)
class GptCreationAmbiguityError(RuntimeError):
    codex_thread_id: CodexThreadId
    conflict: GptCreationConflict

    @override
    def __str__(self) -> str:
        return f"GPT creation {self.codex_thread_id} is ambiguous: {self.conflict}."


@dataclass(frozen=True, slots=True)
class GptCreationMutationError(RuntimeError):
    codex_thread_id: CodexThreadId
    mutation: GptCreationMutation

    @override
    def __str__(self) -> str:
        return f"GPT creation {self.codex_thread_id} cannot {self.mutation}."


@dataclass(frozen=True, slots=True)
class GptCreationOperation:
    codex_thread_id: CodexThreadId
    project_key: str
    thread_title: str
    discord_parent_channel_id: DiscordChannelId
    nonce: GptCreationNonce
    status: GptCreationStatus
    discord_thread_id: DiscordThreadId | None
    created_at: float
    updated_at: float

    @property
    def marker_token(self) -> GptCreationMarker:
        return GptCreationMarker(f"[gpt-sync:{self.nonce}]")


def _init_db(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        init_store_schema(conn)


@contextmanager
def transaction(db_path: Path) -> Generator[sqlite3.Connection]:
    _init_db(db_path)
    try:
        with (
            closing(sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS)) as conn,
            conn,
        ):
            _ = conn.execute("BEGIN IMMEDIATE")
            yield conn
    except GptCreationAmbiguityError as exc:
        raise GptCreationAmbiguityError(exc.codex_thread_id, exc.conflict) from None
    except GptCreationMutationError as exc:
        raise GptCreationMutationError(exc.codex_thread_id, exc.mutation) from None
    except GptCapacityExceededError as exc:
        raise GptCapacityExceededError(
            exc.used_slots, exc.requested_increase, exc.limit
        ) from None


def load_operation_row(
    conn: sqlite3.Connection, owner: CodexThreadId
) -> JournalRow | None:
    rows: list[JournalRow] = conn.execute(
        "SELECT * FROM gpt_chat_creation_ops WHERE codex_thread_id = ?", (owner,)
    ).fetchall()
    return None if not rows else rows[0]


def mapping_state(
    conn: sqlite3.Connection,
    operation: GptCreationOperation,
    discord_thread_id: DiscordThreadId,
) -> MirrorThreadLifecycleState | None:
    by_owner: list[MirrorRow] = conn.execute(
        "SELECT * FROM mirror_threads WHERE codex_thread_id = ?",
        (operation.codex_thread_id,),
    ).fetchall()
    by_discord: list[MirrorRow] = conn.execute(
        "SELECT * FROM mirror_threads WHERE discord_thread_id = ? ORDER BY codex_thread_id",
        (discord_thread_id,),
    ).fetchall()
    if len(by_discord) > 1:
        raise GptCreationAmbiguityError(operation.codex_thread_id, "discord_ownership")
    if not by_owner:
        if by_discord:
            raise GptCreationAmbiguityError(
                operation.codex_thread_id, "discord_ownership"
            )
        return None
    row = by_owner[0]
    identity = row[0], row[1], row[2], row[3], row[4], row[6]
    expected = (
        operation.codex_thread_id,
        GPT_PROJECT_KEY,
        operation.thread_title,
        operation.discord_parent_channel_id,
        discord_thread_id,
        "gpt_chat",
    )
    if (
        identity != expected
        or not by_discord
        or by_discord[0][0] != operation.codex_thread_id
    ):
        raise GptCreationAmbiguityError(operation.codex_thread_id, "mapping_identity")
    try:
        state = MirrorThreadLifecycleState(row[7])
    except ValueError:
        raise GptCreationAmbiguityError(
            operation.codex_thread_id, "mapping_identity"
        ) from None
    match state:
        case (
            MirrorThreadLifecycleState.ACTIVE | MirrorThreadLifecycleState.REACTIVATING
        ):
            return state
        case (
            MirrorThreadLifecycleState.DEACTIVATING
            | MirrorThreadLifecycleState.INACTIVE
        ):
            raise GptCreationAmbiguityError(
                operation.codex_thread_id, "mapping_identity"
            )
        case _ as unreachable:
            assert_never(unreachable)


def owner_mapping_exists(conn: sqlite3.Connection, owner: CodexThreadId) -> bool:
    rows: list[ExistsRow] = conn.execute(
        "SELECT 1 FROM mirror_threads WHERE codex_thread_id = ?", (owner,)
    ).fetchall()
    return bool(rows)


def owner_operation_exists(conn: sqlite3.Connection, owner: CodexThreadId) -> bool:
    rows: list[ExistsRow] = conn.execute(
        "SELECT 1 FROM gpt_chat_creation_ops WHERE codex_thread_id = ?", (owner,)
    ).fetchall()
    return bool(rows)


def nonce_exists(conn: sqlite3.Connection, nonce: GptCreationNonce) -> bool:
    rows: list[ExistsRow] = conn.execute(
        "SELECT 1 FROM gpt_chat_creation_ops WHERE nonce = ?", (nonce,)
    ).fetchall()
    return bool(rows)


def used_slots(conn: sqlite3.Connection) -> int:
    rows: list[tuple[int]] = conn.execute(
        "SELECT COUNT(*) FROM (SELECT codex_thread_id FROM mirror_threads "
        + "WHERE managed_by = 'gpt_chat' AND lifecycle_state <> 'inactive' "
        + "UNION SELECT codex_thread_id FROM gpt_chat_creation_ops)"
    ).fetchall()
    return rows[0][0]


def insert_prepared(conn: sqlite3.Connection, operation: GptCreationOperation) -> None:
    _ = conn.execute(
        "INSERT INTO gpt_chat_creation_ops VALUES (?, ?, ?, ?, ?, 'prepared', NULL, ?, ?)",
        (
            operation.codex_thread_id,
            GPT_PROJECT_KEY,
            operation.thread_title,
            operation.discord_parent_channel_id,
            operation.nonce,
            operation.created_at,
            operation.updated_at,
        ),
    )


def update_started(
    conn: sqlite3.Connection,
    current: GptCreationOperation,
    updated: GptCreationOperation,
) -> int:
    cursor = conn.execute(
        "UPDATE gpt_chat_creation_ops SET status = 'create_started', updated_at = ? "
        + "WHERE codex_thread_id = ? AND nonce = ? AND status = 'prepared'",
        (updated.updated_at, current.codex_thread_id, current.nonce),
    )
    return cursor.rowcount


def insert_mapping(conn: sqlite3.Connection, operation: GptCreationOperation) -> None:
    _ = conn.execute(
        "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, 'gpt_chat', 'reactivating')",
        (
            operation.codex_thread_id,
            GPT_PROJECT_KEY,
            operation.thread_title,
            operation.discord_parent_channel_id,
            operation.discord_thread_id,
            operation.updated_at,
        ),
    )


def touch_mapping(conn: sqlite3.Connection, operation: GptCreationOperation) -> int:
    cursor = conn.execute(
        "UPDATE mirror_threads SET updated_at = ? WHERE codex_thread_id = ?",
        (operation.updated_at, operation.codex_thread_id),
    )
    return cursor.rowcount


def update_identified(
    conn: sqlite3.Connection,
    current: GptCreationOperation,
    identified: GptCreationOperation,
) -> int:
    cursor = conn.execute(
        "UPDATE gpt_chat_creation_ops SET status = 'discord_identified', "
        + "discord_thread_id = ?, updated_at = ? WHERE codex_thread_id = ? "
        + "AND nonce = ? AND status = 'create_started'",
        (
            identified.discord_thread_id,
            identified.updated_at,
            current.codex_thread_id,
            current.nonce,
        ),
    )
    return cursor.rowcount


def load_journal_rows(db_path: Path) -> list[JournalRow]:
    _init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT * FROM gpt_chat_creation_ops ORDER BY codex_thread_id"
        ).fetchall()


def delete_operation(conn: sqlite3.Connection, operation: GptCreationOperation) -> int:
    cursor = conn.execute(
        "DELETE FROM gpt_chat_creation_ops WHERE codex_thread_id = ? AND nonce = ?",
        (operation.codex_thread_id, operation.nonce),
    )
    return cursor.rowcount
