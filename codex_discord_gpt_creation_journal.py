"""Crash-recoverable persistence for genuinely new GPT Discord threads."""

from __future__ import annotations

# Pyright flags mandatory assert_never defaults after proving every enum case.
# pyright: reportUnnecessaryComparison=false

from contextlib import closing
from dataclasses import dataclass, replace
from enum import StrEnum, unique
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Final, Literal, NewType, TypeAlias, assert_never, override

from codex_discord_gpt_lifecycle import GPT_CHAT_CAPACITY, GptCapacityExceededError
from codex_discord_gpt_migration import GPT_PROJECT_KEY
from codex_discord_gpt_ownership import CodexThreadId, DiscordChannelId, DiscordThreadId, MirrorThreadLifecycleState
from codex_discord_store_schema import init_store_schema


GptCreationNonce = NewType("GptCreationNonce", str)
GptCreationMarker = NewType("GptCreationMarker", str)
JournalRow: TypeAlias = tuple[str, str, str, int, str, str, int | None, float, float]
MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]
CountRow: TypeAlias = tuple[int]
GptCreationConflict: TypeAlias = Literal["existing_operation", "existing_mapping", "nonce_collision", "journal_mismatch", "malformed_journal", "status_order", "mapping_identity", "discord_ownership"]
GptCreationMutation: TypeAlias = Literal["mark_started", "complete", "cancel"]

_SQLITE_TIMEOUT_SECONDS: Final = 10.0
_DISCORD_NAME_LIMIT: Final = 100
_NONCE_PATTERN: Final = re.compile(r"^[0-9a-f]{32}$")
_MARKER_PATTERN: Final = re.compile(r"^\[gpt-sync:([0-9a-f]{32})\](?: .*)?$")


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
class GptCreationIntent:
    codex_thread_id: CodexThreadId
    thread_title: str
    discord_parent_channel_id: DiscordChannelId


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


@dataclass(frozen=True, slots=True)
class GptCreationProtections:
    unfinished: tuple[GptCreationOperation, ...]
    marker_tokens: frozenset[GptCreationMarker]
    nullable_discord_thread_ids: tuple[DiscordThreadId | None, ...]
    discord_thread_ids: frozenset[DiscordThreadId]


def _init_db(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        init_store_schema(conn)


def _ambiguous(owner: str, conflict: GptCreationConflict) -> GptCreationAmbiguityError:
    return GptCreationAmbiguityError(CodexThreadId(owner), conflict)


def _to_operation(row: JournalRow) -> GptCreationOperation:
    owner = row[0]
    if row[1] != GPT_PROJECT_KEY or _NONCE_PATTERN.fullmatch(row[4]) is None:
        raise _ambiguous(owner, "malformed_journal")
    try:
        status = GptCreationStatus(row[5])
    except ValueError:
        raise _ambiguous(owner, "malformed_journal") from None
    match status:
        case GptCreationStatus.PREPARED | GptCreationStatus.CREATE_STARTED:
            if row[6] is not None:
                raise _ambiguous(owner, "malformed_journal")
            discord_thread_id = None
        case GptCreationStatus.DISCORD_IDENTIFIED:
            if row[6] is None:
                raise _ambiguous(owner, "malformed_journal")
            discord_thread_id = DiscordThreadId(row[6])
        case _ as unreachable:
            assert_never(unreachable)
    return GptCreationOperation(CodexThreadId(owner), row[1], row[2], DiscordChannelId(row[3]), GptCreationNonce(row[4]), status, discord_thread_id, row[7], row[8])


def _load_operation(conn: sqlite3.Connection, owner: CodexThreadId) -> GptCreationOperation | None:
    rows: list[JournalRow] = conn.execute("SELECT * FROM gpt_chat_creation_ops WHERE codex_thread_id = ?", (owner,)).fetchall()
    return None if not rows else _to_operation(rows[0])


def _mapping_state(conn: sqlite3.Connection, operation: GptCreationOperation, discord_thread_id: DiscordThreadId) -> MirrorThreadLifecycleState | None:
    by_owner: list[MirrorRow] = conn.execute("SELECT * FROM mirror_threads WHERE codex_thread_id = ?", (operation.codex_thread_id,)).fetchall()
    by_discord: list[MirrorRow] = conn.execute("SELECT * FROM mirror_threads WHERE discord_thread_id = ? ORDER BY codex_thread_id", (discord_thread_id,)).fetchall()
    if len(by_discord) > 1:
        raise _ambiguous(operation.codex_thread_id, "discord_ownership")
    if not by_owner:
        if by_discord:
            raise _ambiguous(operation.codex_thread_id, "discord_ownership")
        return None
    row = by_owner[0]
    identity = (row[0], row[1], row[2], row[3], row[4], row[6])
    expected = (operation.codex_thread_id, GPT_PROJECT_KEY, operation.thread_title, operation.discord_parent_channel_id, discord_thread_id, "gpt_chat")
    if identity != expected or not by_discord or by_discord[0][0] != operation.codex_thread_id:
        raise _ambiguous(operation.codex_thread_id, "mapping_identity")
    try:
        state = MirrorThreadLifecycleState(row[7])
    except ValueError:
        raise _ambiguous(operation.codex_thread_id, "mapping_identity") from None
    match state:
        case MirrorThreadLifecycleState.ACTIVE | MirrorThreadLifecycleState.REACTIVATING:
            return state
        case MirrorThreadLifecycleState.DEACTIVATING | MirrorThreadLifecycleState.INACTIVE:
            raise _ambiguous(operation.codex_thread_id, "mapping_identity")
        case _ as unreachable:
            assert_never(unreachable)


def prepare_gpt_creation(db_path: Path, intent: GptCreationIntent) -> GptCreationOperation:
    """Atomically reserve capacity and journal one genuinely new owner."""
    nonce = GptCreationNonce(secrets.token_hex(16))
    current = time.time()
    _init_db(db_path)
    with closing(sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS)) as conn, conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM mirror_threads WHERE codex_thread_id = ?", (intent.codex_thread_id,)).fetchone():
            raise _ambiguous(intent.codex_thread_id, "existing_mapping")
        if conn.execute("SELECT 1 FROM gpt_chat_creation_ops WHERE codex_thread_id = ?", (intent.codex_thread_id,)).fetchone():
            raise _ambiguous(intent.codex_thread_id, "existing_operation")
        if conn.execute("SELECT 1 FROM gpt_chat_creation_ops WHERE nonce = ?", (nonce,)).fetchone():
            raise _ambiguous(intent.codex_thread_id, "nonce_collision")
        count_rows: list[CountRow] = conn.execute("SELECT COUNT(*) FROM (SELECT codex_thread_id FROM mirror_threads WHERE managed_by = 'gpt_chat' AND lifecycle_state <> 'inactive' UNION SELECT codex_thread_id FROM gpt_chat_creation_ops)").fetchall()
        if count_rows[0][0] >= GPT_CHAT_CAPACITY:
            raise GptCapacityExceededError(count_rows[0][0], 1)
        _ = conn.execute("INSERT INTO gpt_chat_creation_ops VALUES (?, ?, ?, ?, ?, 'prepared', NULL, ?, ?)", (intent.codex_thread_id, GPT_PROJECT_KEY, intent.thread_title, intent.discord_parent_channel_id, nonce, current, current))
    return GptCreationOperation(intent.codex_thread_id, GPT_PROJECT_KEY, intent.thread_title, intent.discord_parent_channel_id, nonce, GptCreationStatus.PREPARED, None, current, current)


def format_gpt_creation_thread_name(operation: GptCreationOperation) -> str:
    suffix = " ".join(operation.thread_title.split())
    available = _DISCORD_NAME_LIMIT - len(operation.marker_token) - 1
    suffix = suffix[:available].rstrip()
    return str(operation.marker_token) if not suffix else f"{operation.marker_token} {suffix}"


def parse_gpt_creation_thread_name(value: str) -> GptCreationNonce | None:
    match = _MARKER_PATTERN.fullmatch(value)
    return None if match is None else GptCreationNonce(match.group(1))


def mark_gpt_creation_started(db_path: Path, operation: GptCreationOperation) -> GptCreationOperation:
    _init_db(db_path)
    with closing(sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS)) as conn, conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        current = _load_operation(conn, operation.codex_thread_id)
        if current is None or current != operation:
            raise GptCreationMutationError(operation.codex_thread_id, "mark_started")
        match current.status:
            case GptCreationStatus.PREPARED:
                updated = replace(current, status=GptCreationStatus.CREATE_STARTED, updated_at=time.time())
                cursor = conn.execute("UPDATE gpt_chat_creation_ops SET status = 'create_started', updated_at = ? WHERE codex_thread_id = ? AND nonce = ? AND status = 'prepared'", (updated.updated_at, current.codex_thread_id, current.nonce))
                if cursor.rowcount != 1:
                    raise GptCreationMutationError(operation.codex_thread_id, "mark_started")
                return updated
            case GptCreationStatus.CREATE_STARTED:
                return current
            case GptCreationStatus.DISCORD_IDENTIFIED:
                raise GptCreationMutationError(operation.codex_thread_id, "mark_started")
            case _ as unreachable:
                assert_never(unreachable)


def handoff_gpt_creation(db_path: Path, operation: GptCreationOperation, discord_thread_id: DiscordThreadId) -> GptCreationOperation:
    """Atomically establish one exact mapping and identify its journal row."""
    _init_db(db_path)
    with closing(sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS)) as conn, conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        current = _load_operation(conn, operation.codex_thread_id)
        if current is None or current != operation:
            raise _ambiguous(operation.codex_thread_id, "journal_mismatch")
        match current.status:
            case GptCreationStatus.PREPARED:
                raise _ambiguous(operation.codex_thread_id, "status_order")
            case GptCreationStatus.DISCORD_IDENTIFIED:
                if current.discord_thread_id != discord_thread_id:
                    raise _ambiguous(operation.codex_thread_id, "mapping_identity")
                _ = _mapping_state(conn, current, discord_thread_id)
                return current
            case GptCreationStatus.CREATE_STARTED:
                mapping_state = _mapping_state(conn, current, discord_thread_id)
            case _ as unreachable:
                assert_never(unreachable)
        updated_at = time.time()
        if mapping_state is None:
            _ = conn.execute("INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, 'gpt_chat', 'reactivating')", (current.codex_thread_id, GPT_PROJECT_KEY, current.thread_title, current.discord_parent_channel_id, discord_thread_id, updated_at))
        else:
            cursor = conn.execute("UPDATE mirror_threads SET updated_at = ? WHERE codex_thread_id = ?", (updated_at, current.codex_thread_id))
            if cursor.rowcount != 1:
                raise _ambiguous(operation.codex_thread_id, "mapping_identity")
        cursor = conn.execute("UPDATE gpt_chat_creation_ops SET status = 'discord_identified', discord_thread_id = ?, updated_at = ? WHERE codex_thread_id = ? AND nonce = ? AND status = 'create_started'", (discord_thread_id, updated_at, current.codex_thread_id, current.nonce))
        if cursor.rowcount != 1:
            raise _ambiguous(operation.codex_thread_id, "journal_mismatch")
    return replace(current, status=GptCreationStatus.DISCORD_IDENTIFIED, discord_thread_id=discord_thread_id, updated_at=updated_at)


def load_gpt_creation_protections(db_path: Path) -> GptCreationProtections:
    _init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows: list[JournalRow] = conn.execute("SELECT * FROM gpt_chat_creation_ops ORDER BY codex_thread_id").fetchall()
    unfinished = tuple(_to_operation(row) for row in rows)
    return GptCreationProtections(unfinished, frozenset(operation.marker_token for operation in unfinished), tuple(operation.discord_thread_id for operation in unfinished), frozenset(operation.discord_thread_id for operation in unfinished if operation.discord_thread_id is not None))


def complete_gpt_creation(db_path: Path, operation: GptCreationOperation) -> None:
    """Remove a journal only after the caller completed Discord final state/name."""
    _init_db(db_path)
    with closing(sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS)) as conn, conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        current = _load_operation(conn, operation.codex_thread_id)
        if current is None or current != operation:
            raise GptCreationMutationError(operation.codex_thread_id, "complete")
        match current.status:
            case GptCreationStatus.DISCORD_IDENTIFIED:
                discord_thread_id = current.discord_thread_id
            case GptCreationStatus.PREPARED | GptCreationStatus.CREATE_STARTED:
                raise GptCreationMutationError(operation.codex_thread_id, "complete")
            case _ as unreachable:
                assert_never(unreachable)
        if discord_thread_id is None:
            raise GptCreationMutationError(operation.codex_thread_id, "complete")
        try:
            state = _mapping_state(conn, current, discord_thread_id)
        except GptCreationAmbiguityError:
            raise GptCreationMutationError(operation.codex_thread_id, "complete") from None
        match state:
            case MirrorThreadLifecycleState.ACTIVE:
                pass
            case None | MirrorThreadLifecycleState.DEACTIVATING | MirrorThreadLifecycleState.INACTIVE | MirrorThreadLifecycleState.REACTIVATING:
                raise GptCreationMutationError(operation.codex_thread_id, "complete")
            case _ as unreachable:
                assert_never(unreachable)
        _delete_operation(conn, current, "complete")


def cancel_gpt_creation(db_path: Path, operation: GptCreationOperation) -> None:
    """Explicitly remove an operation after clear proved cancellation is safe."""
    _init_db(db_path)
    with closing(sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS)) as conn, conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        current = _load_operation(conn, operation.codex_thread_id)
        if current is None or current != operation:
            raise GptCreationMutationError(operation.codex_thread_id, "cancel")
        _delete_operation(conn, current, "cancel")


def _delete_operation(conn: sqlite3.Connection, operation: GptCreationOperation, mutation: GptCreationMutation) -> None:
    cursor = conn.execute("DELETE FROM gpt_chat_creation_ops WHERE codex_thread_id = ? AND nonce = ?", (operation.codex_thread_id, operation.nonce))
    if cursor.rowcount != 1:
        raise GptCreationMutationError(operation.codex_thread_id, mutation)
