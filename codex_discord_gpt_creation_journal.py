"""Crash-recoverable public API for genuinely new GPT Discord threads."""

from __future__ import annotations

# Pyright flags mandatory assert_never defaults after proving every enum case.
# pyright: reportUnnecessaryComparison=false

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
import re
import secrets
from sqlite3 import Connection
import time
from typing import Final, assert_never

import codex_discord_gpt_creation_journal_store as store
from codex_discord_gpt_creation_journal_store import (
    GptCreationAmbiguityError as GptCreationAmbiguityError,
    GptCreationConflict as GptCreationConflict,
    GptCreationMarker as GptCreationMarker,
    GptCreationMutation as GptCreationMutation,
    GptCreationMutationError as GptCreationMutationError,
    GptCreationNonce as GptCreationNonce,
    GptCreationOperation as GptCreationOperation,
    GptCreationStatus as GptCreationStatus,
    JournalRow as JournalRow,
    MirrorRow as MirrorRow,
)
from codex_discord_gpt_lifecycle import GPT_CHAT_CAPACITY, GptCapacityExceededError
from codex_discord_gpt_migration import GPT_PROJECT_KEY
from codex_discord_gpt_ownership import (
    CodexThreadId,
    DiscordChannelId,
    DiscordThreadId,
    MirrorThreadLifecycleState,
)


_DISCORD_NAME_LIMIT: Final = 100
_NONCE_PATTERN: Final = re.compile(r"^[0-9a-f]{32}$")
_MARKER_PATTERN: Final = re.compile(r"^\[gpt-sync:([0-9a-f]{32})\](?: .*)?$")


@dataclass(frozen=True, slots=True)
class GptCreationIntent:
    codex_thread_id: CodexThreadId
    thread_title: str
    discord_parent_channel_id: DiscordChannelId


@dataclass(frozen=True, slots=True)
class GptCreationProtections:
    unfinished: tuple[GptCreationOperation, ...]
    marker_tokens: frozenset[GptCreationMarker]
    nullable_discord_thread_ids: tuple[DiscordThreadId | None, ...]
    discord_thread_ids: frozenset[DiscordThreadId]


@dataclass(frozen=True, slots=True)
class GptCreationRecoveryRequest:
    db_path: Path
    operation: GptCreationOperation
    final_name: str
    finalize_cursor: Callable[[GptCreationOperation], None]


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
    return GptCreationOperation(
        CodexThreadId(owner),
        row[1],
        row[2],
        DiscordChannelId(row[3]),
        GptCreationNonce(row[4]),
        status,
        discord_thread_id,
        row[7],
        row[8],
    )


def _load_operation(
    conn: Connection, owner: CodexThreadId
) -> GptCreationOperation | None:
    row = store.load_operation_row(conn, owner)
    return None if row is None else _to_operation(row)


def prepare_gpt_creation(
    db_path: Path, intent: GptCreationIntent
) -> GptCreationOperation:
    """Atomically reserve capacity and journal one genuinely new owner."""
    current = time.time()
    operation = GptCreationOperation(
        intent.codex_thread_id,
        GPT_PROJECT_KEY,
        intent.thread_title,
        intent.discord_parent_channel_id,
        GptCreationNonce(secrets.token_hex(16)),
        GptCreationStatus.PREPARED,
        None,
        current,
        current,
    )
    with store.transaction(db_path) as conn:
        if store.owner_mapping_exists(conn, intent.codex_thread_id):
            raise _ambiguous(intent.codex_thread_id, "existing_mapping")
        if store.owner_operation_exists(conn, intent.codex_thread_id):
            raise _ambiguous(intent.codex_thread_id, "existing_operation")
        if store.nonce_exists(conn, operation.nonce):
            raise _ambiguous(intent.codex_thread_id, "nonce_collision")
        used_slots = store.used_slots(conn)
        if used_slots >= GPT_CHAT_CAPACITY:
            raise GptCapacityExceededError(used_slots, 1)
        store.insert_prepared(conn, operation)
    return operation


def format_gpt_creation_thread_name(operation: GptCreationOperation) -> str:
    suffix = " ".join(operation.thread_title.split())
    available = _DISCORD_NAME_LIMIT - len(operation.marker_token) - 1
    suffix = suffix[:available].rstrip()
    return (
        str(operation.marker_token)
        if not suffix
        else f"{operation.marker_token} {suffix}"
    )


def parse_gpt_creation_thread_name(value: str) -> GptCreationNonce | None:
    match = _MARKER_PATTERN.fullmatch(value)
    return None if match is None else GptCreationNonce(match.group(1))


def mark_gpt_creation_started(
    db_path: Path, operation: GptCreationOperation
) -> GptCreationOperation:
    with store.transaction(db_path) as conn:
        current = _load_operation(conn, operation.codex_thread_id)
        if current is None or current != operation:
            raise GptCreationMutationError(operation.codex_thread_id, "mark_started")
        match current.status:
            case GptCreationStatus.PREPARED:
                updated = replace(
                    current,
                    status=GptCreationStatus.CREATE_STARTED,
                    updated_at=time.time(),
                )
                if store.update_started(conn, current, updated) != 1:
                    raise GptCreationMutationError(
                        operation.codex_thread_id, "mark_started"
                    )
                return updated
            case GptCreationStatus.CREATE_STARTED:
                return current
            case GptCreationStatus.DISCORD_IDENTIFIED:
                raise GptCreationMutationError(
                    operation.codex_thread_id, "mark_started"
                )
            case _ as unreachable:
                assert_never(unreachable)


def handoff_gpt_creation(
    db_path: Path, operation: GptCreationOperation, discord_thread_id: DiscordThreadId
) -> GptCreationOperation:
    """Atomically establish one exact mapping and identify its journal row."""
    with store.transaction(db_path) as conn:
        current = _load_operation(conn, operation.codex_thread_id)
        if current is None or current != operation:
            raise _ambiguous(operation.codex_thread_id, "journal_mismatch")
        match current.status:
            case GptCreationStatus.PREPARED:
                raise _ambiguous(operation.codex_thread_id, "status_order")
            case GptCreationStatus.DISCORD_IDENTIFIED:
                if current.discord_thread_id != discord_thread_id:
                    raise _ambiguous(operation.codex_thread_id, "mapping_identity")
                _ = store.mapping_state(conn, current, discord_thread_id)
                return current
            case GptCreationStatus.CREATE_STARTED:
                mapping_state = store.mapping_state(conn, current, discord_thread_id)
            case _ as unreachable:
                assert_never(unreachable)
        identified = replace(
            current,
            status=GptCreationStatus.DISCORD_IDENTIFIED,
            discord_thread_id=discord_thread_id,
            updated_at=time.time(),
        )
        if mapping_state is None:
            store.insert_mapping(conn, identified)
        elif store.touch_mapping(conn, identified) != 1:
            raise _ambiguous(operation.codex_thread_id, "mapping_identity")
        if store.update_identified(conn, current, identified) != 1:
            raise _ambiguous(operation.codex_thread_id, "journal_mismatch")
    return identified


def load_gpt_creation_protections(db_path: Path) -> GptCreationProtections:
    unfinished = tuple(_to_operation(row) for row in store.load_journal_rows(db_path))
    return GptCreationProtections(
        unfinished,
        frozenset(operation.marker_token for operation in unfinished),
        tuple(operation.discord_thread_id for operation in unfinished),
        frozenset(
            operation.discord_thread_id
            for operation in unfinished
            if operation.discord_thread_id is not None
        ),
    )


def complete_gpt_creation(db_path: Path, operation: GptCreationOperation) -> None:
    """Remove a journal only after the caller completed Discord final state/name."""
    with store.transaction(db_path) as conn:
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
            state = store.mapping_state(conn, current, discord_thread_id)
        except GptCreationAmbiguityError:
            raise GptCreationMutationError(
                operation.codex_thread_id, "complete"
            ) from None
        match state:
            case MirrorThreadLifecycleState.ACTIVE:
                pass
            case (
                None
                | MirrorThreadLifecycleState.DEACTIVATING
                | MirrorThreadLifecycleState.INACTIVE
                | MirrorThreadLifecycleState.REACTIVATING
            ):
                raise GptCreationMutationError(operation.codex_thread_id, "complete")
            case _ as unreachable:
                assert_never(unreachable)
        _delete_operation(conn, current, "complete")


def cancel_gpt_creation(db_path: Path, operation: GptCreationOperation) -> None:
    """Explicitly remove an operation after clear proved cancellation is safe."""
    with store.transaction(db_path) as conn:
        current = _load_operation(conn, operation.codex_thread_id)
        if current is None or current != operation:
            raise GptCreationMutationError(operation.codex_thread_id, "cancel")
        _delete_operation(conn, current, "cancel")


def _delete_operation(
    conn: Connection,
    operation: GptCreationOperation,
    mutation: GptCreationMutation,
) -> None:
    if store.delete_operation(conn, operation) != 1:
        raise GptCreationMutationError(operation.codex_thread_id, mutation)
