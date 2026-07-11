"""Typed, exact ownership queries for migrated mirror-thread rows."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Final, NewType, TypeAlias, override

from codex_discord_store_schema import init_store_schema


CodexThreadId = NewType("CodexThreadId", str)
DiscordChannelId = NewType("DiscordChannelId", int)
DiscordThreadId = NewType("DiscordThreadId", int)


@unique
class MirrorThreadManagedBy(StrEnum):
    ORDINARY = "ordinary"
    GPT_CHAT = "gpt_chat"


@unique
class MirrorThreadLifecycleState(StrEnum):
    ACTIVE = "active"
    DEACTIVATING = "deactivating"
    INACTIVE = "inactive"
    REACTIVATING = "reactivating"


@dataclass(frozen=True, slots=True)
class MirrorThreadOwnership:
    codex_thread_id: CodexThreadId
    project_key: str
    thread_title: str
    discord_channel_id: DiscordChannelId
    discord_thread_id: DiscordThreadId
    updated_at: float
    managed_by: MirrorThreadManagedBy
    lifecycle_state: MirrorThreadLifecycleState

    @property
    def is_active_gpt(self) -> bool:
        return (
            self.managed_by is MirrorThreadManagedBy.GPT_CHAT
            and self.lifecycle_state is MirrorThreadLifecycleState.ACTIVE
        )

    @property
    def is_ordinary(self) -> bool:
        return self.managed_by is MirrorThreadManagedBy.ORDINARY


@dataclass(frozen=True, slots=True)
class DiscordOwnershipConflictError(RuntimeError):
    discord_thread_id: DiscordThreadId
    owner_count: int

    @override
    def __str__(self) -> str:
        return (
            f"Discord thread {self.discord_thread_id} has "
            f"{self.owner_count} mirror owners."
        )


@dataclass(frozen=True, slots=True)
class GptOwnershipOverwriteError(RuntimeError):
    codex_thread_id: CodexThreadId
    discord_thread_id: DiscordThreadId

    @override
    def __str__(self) -> str:
        return (
            "Ordinary mirror persistence cannot overwrite GPT ownership for "
            f"Codex thread {self.codex_thread_id} or Discord thread {self.discord_thread_id}."
        )


OwnershipRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]

_OWNERSHIP_COLUMNS: Final = (
    "codex_thread_id, project_key, thread_title, discord_channel_id, "
    "discord_thread_id, updated_at, managed_by, lifecycle_state"
)


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        init_store_schema(conn)


def _to_ownership(row: OwnershipRow) -> MirrorThreadOwnership:
    return MirrorThreadOwnership(
        codex_thread_id=CodexThreadId(row[0]),
        project_key=row[1],
        thread_title=row[2],
        discord_channel_id=DiscordChannelId(row[3]),
        discord_thread_id=DiscordThreadId(row[4]),
        updated_at=row[5],
        managed_by=MirrorThreadManagedBy(row[6]),
        lifecycle_state=MirrorThreadLifecycleState(row[7]),
    )


def get_mirror_thread_owner_by_discord_thread_id(
    db_path: Path,
    discord_thread_id: int | None,
) -> MirrorThreadOwnership | None:
    """Return one exact Discord owner in any state, rejecting ambiguity."""
    if not discord_thread_id:
        return None
    normalized_id = DiscordThreadId(int(discord_thread_id))
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows: list[OwnershipRow] = conn.execute(
            "SELECT " + _OWNERSHIP_COLUMNS + " FROM mirror_threads "
            + "WHERE discord_thread_id = ? ORDER BY codex_thread_id",
            (normalized_id,),
        ).fetchall()
    if len(rows) > 1:
        raise DiscordOwnershipConflictError(
            discord_thread_id=normalized_id,
            owner_count=len(rows),
        )
    if not rows:
        return None
    return _to_ownership(rows[0])


def get_mirror_thread_owner_by_codex_thread_id(
    db_path: Path,
    codex_thread_id: str,
) -> MirrorThreadOwnership | None:
    """Return an exact Codex owner without hiding inactive or transitional state."""
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows: list[OwnershipRow] = conn.execute(
            "SELECT " + _OWNERSHIP_COLUMNS + " FROM mirror_threads "
            + "WHERE codex_thread_id = ?",
            (str(codex_thread_id),),
        ).fetchall()
    if not rows:
        return None
    return _to_ownership(rows[0])


def get_active_gpt_mirror_thread_by_discord_thread_id(
    db_path: Path,
    discord_thread_id: int | None,
) -> MirrorThreadOwnership | None:
    """Return only an exact active GPT owner; all other exact states are unroutable."""
    owner = get_mirror_thread_owner_by_discord_thread_id(db_path, discord_thread_id)
    if owner is None or not owner.is_active_gpt:
        return None
    return owner


def list_ordinary_mirror_threads(db_path: Path) -> tuple[MirrorThreadOwnership, ...]:
    """List ordinary mappings only, newest first, for legacy status surfaces."""
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows: list[OwnershipRow] = conn.execute(
            "SELECT " + _OWNERSHIP_COLUMNS + " FROM mirror_threads "
            + "WHERE managed_by = 'ordinary' "
            + "ORDER BY updated_at DESC, codex_thread_id"
        ).fetchall()
    return tuple(_to_ownership(row) for row in rows)
