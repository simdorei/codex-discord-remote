"""Atomic capacity accounting and GPT mapping lifecycle transitions."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
import sqlite3
import time
from typing import Final, TypeAlias, override

from codex_discord_gpt_migration import GPT_PROJECT_KEY
from codex_discord_gpt_ownership import (
    CodexThreadId,
    MirrorThreadLifecycleState,
    MirrorThreadManagedBy,
)
from codex_discord_store_schema import init_store_schema


GPT_CHAT_CAPACITY: Final = 5
_SQLITE_TIMEOUT_SECONDS: Final = 10.0
_IdentityRow: TypeAlias = tuple[str, str, str]
_CountRow: TypeAlias = tuple[int]


@unique
class GptLifecycleOperation(StrEnum):
    BEGIN_DEACTIVATION = "begin_deactivation"
    FINALIZE_DEACTIVATION = "finalize_deactivation"
    BEGIN_REACTIVATION = "begin_reactivation"
    FINALIZE_REACTIVATION = "finalize_reactivation"
    BEGIN_CLEAR_DEACTIVATION = "begin_clear_deactivation"


class GptLifecycleError(RuntimeError):
    """Base error for expected, non-mutating lifecycle refusal."""


@dataclass(frozen=True, slots=True)
class GptMappingNotFoundError(GptLifecycleError):
    codex_thread_id: CodexThreadId

    @override
    def __str__(self) -> str:
        return f"GPT mapping {self.codex_thread_id} was not found."


@dataclass(frozen=True, slots=True)
class GptLifecycleOwnerError(GptLifecycleError):
    codex_thread_id: CodexThreadId
    managed_by: str

    @override
    def __str__(self) -> str:
        return f"Mapping {self.codex_thread_id} is not owned by GPT sync."


@dataclass(frozen=True, slots=True)
class GptLifecycleProjectError(GptLifecycleError):
    codex_thread_id: CodexThreadId
    project_key: str

    @override
    def __str__(self) -> str:
        return f"GPT mapping {self.codex_thread_id} has the wrong project key."


@dataclass(frozen=True, slots=True)
class GptLifecycleStateError(GptLifecycleError):
    codex_thread_id: CodexThreadId
    state: str

    @override
    def __str__(self) -> str:
        return f"GPT mapping {self.codex_thread_id} has an invalid lifecycle state."


@dataclass(frozen=True, slots=True)
class GptLifecycleTransitionError(GptLifecycleError):
    codex_thread_id: CodexThreadId
    state: MirrorThreadLifecycleState
    operation: GptLifecycleOperation

    @override
    def __str__(self) -> str:
        return f"Lifecycle operation {self.operation.value} is forbidden from {self.state.value}."


@dataclass(frozen=True, slots=True)
class GptCapacityRequestError(GptLifecycleError):
    requested_increase: int

    @override
    def __str__(self) -> str:
        return "GPT capacity increase must be zero or greater."


@dataclass(frozen=True, slots=True)
class GptCapacityExceededError(GptLifecycleError):
    used_slots: int
    requested_increase: int
    limit: int = GPT_CHAT_CAPACITY

    @override
    def __str__(self) -> str:
        return (
            f"GPT sync capacity is {self.used_slots}/{self.limit}; "
            f"an increase of {self.requested_increase} is not allowed."
        )


@dataclass(frozen=True, slots=True)
class GptCapacityAudit:
    used_slots: int
    requested_increase: int
    limit: int = GPT_CHAT_CAPACITY

    @property
    def projected_slots(self) -> int:
        return self.used_slots + self.requested_increase


@dataclass(frozen=True, slots=True)
class GptLifecycleTransition:
    codex_thread_id: CodexThreadId
    previous_state: MirrorThreadLifecycleState
    state: MirrorThreadLifecycleState
    changed: bool


_ALLOWED_TRANSITIONS: Final = {
    (GptLifecycleOperation.BEGIN_DEACTIVATION, MirrorThreadLifecycleState.ACTIVE): MirrorThreadLifecycleState.DEACTIVATING,
    (GptLifecycleOperation.BEGIN_DEACTIVATION, MirrorThreadLifecycleState.DEACTIVATING): MirrorThreadLifecycleState.DEACTIVATING,
    (GptLifecycleOperation.FINALIZE_DEACTIVATION, MirrorThreadLifecycleState.DEACTIVATING): MirrorThreadLifecycleState.INACTIVE,
    (GptLifecycleOperation.BEGIN_REACTIVATION, MirrorThreadLifecycleState.INACTIVE): MirrorThreadLifecycleState.REACTIVATING,
    (GptLifecycleOperation.BEGIN_REACTIVATION, MirrorThreadLifecycleState.REACTIVATING): MirrorThreadLifecycleState.REACTIVATING,
    (GptLifecycleOperation.FINALIZE_REACTIVATION, MirrorThreadLifecycleState.REACTIVATING): MirrorThreadLifecycleState.ACTIVE,
    (GptLifecycleOperation.BEGIN_CLEAR_DEACTIVATION, MirrorThreadLifecycleState.ACTIVE): MirrorThreadLifecycleState.DEACTIVATING,
    (GptLifecycleOperation.BEGIN_CLEAR_DEACTIVATION, MirrorThreadLifecycleState.DEACTIVATING): MirrorThreadLifecycleState.DEACTIVATING,
    (GptLifecycleOperation.BEGIN_CLEAR_DEACTIVATION, MirrorThreadLifecycleState.REACTIVATING): MirrorThreadLifecycleState.DEACTIVATING,
}


def _init_db(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        init_store_schema(conn)


def _count_used_slots(conn: sqlite3.Connection) -> int:
    rows: list[_CountRow] = conn.execute(
        "SELECT COUNT(*) FROM ("
        + "SELECT codex_thread_id FROM mirror_threads "
        + "WHERE managed_by = 'gpt_chat' AND lifecycle_state <> 'inactive' "
        + "UNION SELECT codex_thread_id FROM gpt_chat_creation_ops)"
    ).fetchall()
    return rows[0][0]


def _ensure_capacity(used_slots: int, requested_increase: int) -> GptCapacityAudit:
    if requested_increase < 0:
        raise GptCapacityRequestError(requested_increase=requested_increase)
    audit = GptCapacityAudit(
        used_slots=used_slots,
        requested_increase=requested_increase,
    )
    if requested_increase > 0 and (
        used_slots > GPT_CHAT_CAPACITY or audit.projected_slots > GPT_CHAT_CAPACITY
    ):
        raise GptCapacityExceededError(
            used_slots=used_slots,
            requested_increase=requested_increase,
        )
    return audit


def audit_gpt_capacity(
    db_path: Path,
    *,
    requested_increase: int = 0,
) -> GptCapacityAudit:
    """Read current unique owner capacity and reject a disallowed increase."""
    _init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        return _ensure_capacity(_count_used_slots(conn), requested_increase)


def _read_state(
    conn: sqlite3.Connection,
    codex_thread_id: CodexThreadId,
) -> MirrorThreadLifecycleState:
    rows: list[_IdentityRow] = conn.execute(
        "SELECT managed_by, project_key, lifecycle_state FROM mirror_threads "
        + "WHERE codex_thread_id = ?",
        (codex_thread_id,),
    ).fetchall()
    if not rows:
        raise GptMappingNotFoundError(codex_thread_id=codex_thread_id)
    managed_by_raw, project_key, state_raw = rows[0]
    if managed_by_raw != MirrorThreadManagedBy.GPT_CHAT.value:
        raise GptLifecycleOwnerError(
            codex_thread_id=codex_thread_id,
            managed_by=managed_by_raw,
        )
    if project_key != GPT_PROJECT_KEY:
        raise GptLifecycleProjectError(
            codex_thread_id=codex_thread_id,
            project_key=project_key,
        )
    try:
        return MirrorThreadLifecycleState(state_raw)
    except ValueError:
        raise GptLifecycleStateError(
            codex_thread_id=codex_thread_id,
            state=state_raw,
        ) from None


def _next_state(
    codex_thread_id: CodexThreadId,
    state: MirrorThreadLifecycleState,
    operation: GptLifecycleOperation,
) -> MirrorThreadLifecycleState:
    target = _ALLOWED_TRANSITIONS.get((operation, state))
    if target is None:
        raise GptLifecycleTransitionError(
            codex_thread_id=codex_thread_id,
            state=state,
            operation=operation,
        )
    return target


def transition_gpt_lifecycle(
    db_path: Path,
    codex_thread_id: str,
    operation: GptLifecycleOperation,
) -> GptLifecycleTransition:
    normalized_id = CodexThreadId(str(codex_thread_id))
    _init_db(db_path)
    with closing(sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS)) as conn:
        with conn:
            _ = conn.execute("BEGIN IMMEDIATE")
            previous_state = _read_state(conn, normalized_id)
            state = _next_state(normalized_id, previous_state, operation)
            if (
                operation is GptLifecycleOperation.BEGIN_REACTIVATION
                and previous_state is MirrorThreadLifecycleState.INACTIVE
            ):
                owner_rows: list[_CountRow] = conn.execute(
                    "SELECT COUNT(*) FROM gpt_chat_creation_ops WHERE codex_thread_id = ?",
                    (normalized_id,),
                ).fetchall()
                requested_increase = 0 if owner_rows[0][0] else 1
                _ = _ensure_capacity(_count_used_slots(conn), requested_increase)
            changed = state is not previous_state
            if changed:
                cursor = conn.execute(
                    "UPDATE mirror_threads SET lifecycle_state = ?, updated_at = ? "
                    + "WHERE codex_thread_id = ? AND managed_by = 'gpt_chat' "
                    + "AND project_key = ? AND lifecycle_state = ?",
                    (
                        state.value,
                        time.time(),
                        normalized_id,
                        GPT_PROJECT_KEY,
                        previous_state.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise GptLifecycleTransitionError(
                        codex_thread_id=normalized_id,
                        state=previous_state,
                        operation=operation,
                    )
    return GptLifecycleTransition(
        codex_thread_id=normalized_id,
        previous_state=previous_state,
        state=state,
        changed=changed,
    )
