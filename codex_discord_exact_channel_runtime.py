from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
import sqlite3
from typing import Final, TypeAlias, assert_never

import codex_discord_gpt_creation_journal as gpt_creation_journal
import codex_discord_gpt_ownership as gpt_ownership


_EXACT_READ_TIMEOUT_SECONDS: Final = 1.0
ExactOwnershipRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]


@unique
class ExactChannelBlockReason(StrEnum):
    OWNERSHIP_CONFLICT = "gpt_ownership_conflict"
    DEACTIVATING = "gpt_deactivating"
    INACTIVE = "gpt_inactive"
    REACTIVATING = "gpt_reactivating"
    CREATION_JOURNAL_ID = "gpt_creation_journal_id"
    CREATION_JOURNAL_MARKER = "gpt_creation_journal_marker"


@dataclass(frozen=True, slots=True)
class ExactChannelActive:
    codex_thread_id: str


@dataclass(frozen=True, slots=True)
class ExactChannelBlocked:
    reason: str


@dataclass(frozen=True, slots=True)
class ExactChannelUnknown:
    pass


ExactChannelDecision: TypeAlias = (
    ExactChannelActive | ExactChannelBlocked | ExactChannelUnknown
)
ExactChannelSafetyResult: TypeAlias = ExactChannelDecision


def _load_exact_owner(
    conn: sqlite3.Connection,
    discord_channel_id: int | None,
) -> gpt_ownership.MirrorThreadOwnership | None:
    if not discord_channel_id:
        return None
    normalized_id = gpt_ownership.DiscordThreadId(int(discord_channel_id))
    rows: list[ExactOwnershipRow] = conn.execute(
        "SELECT codex_thread_id, project_key, thread_title, discord_channel_id, "
        + "discord_thread_id, updated_at, managed_by, lifecycle_state "
        + "FROM mirror_threads WHERE discord_thread_id = ? ORDER BY codex_thread_id",
        (normalized_id,),
    ).fetchall()
    if len(rows) > 1:
        raise gpt_ownership.DiscordOwnershipConflictError(normalized_id, len(rows))
    if not rows:
        return None
    row = rows[0]
    return gpt_ownership.MirrorThreadOwnership(
        gpt_ownership.CodexThreadId(row[0]),
        row[1],
        row[2],
        gpt_ownership.DiscordChannelId(row[3]),
        gpt_ownership.DiscordThreadId(row[4]),
        row[5],
        gpt_ownership.MirrorThreadManagedBy(row[6]),
        gpt_ownership.MirrorThreadLifecycleState(row[7]),
    )


def _read_exact_state(
    db_path: Path,
    discord_channel_id: int | None,
) -> tuple[
    gpt_ownership.MirrorThreadOwnership | None,
    gpt_creation_journal.GptCreationProtections,
]:
    read_only_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(
        sqlite3.connect(
            read_only_uri,
            uri=True,
            timeout=_EXACT_READ_TIMEOUT_SECONDS,
        )
    ) as conn:
        _ = conn.execute("PRAGMA query_only=ON")
        _ = conn.execute("BEGIN")
        try:
            owner = _load_exact_owner(conn, discord_channel_id)
            protections = (
                gpt_creation_journal.load_gpt_creation_protections_from_connection(
                    conn
                )
            )
            return owner, protections
        finally:
            conn.rollback()


def resolve_exact_channel_safety(
    db_path: Path,
    discord_channel_id: int | None,
    channel_name: str | None,
) -> ExactChannelSafetyResult:
    """Resolve exact GPT ownership without consulting any fallback target."""
    try:
        owner, protections = _read_exact_state(db_path, discord_channel_id)
    except gpt_ownership.DiscordOwnershipConflictError:
        return ExactChannelBlocked(ExactChannelBlockReason.OWNERSHIP_CONFLICT.value)
    if discord_channel_id in protections.discord_thread_ids:
        return ExactChannelBlocked(ExactChannelBlockReason.CREATION_JOURNAL_ID.value)
    marker_nonce = (
        None
        if channel_name is None
        else gpt_creation_journal.parse_gpt_creation_thread_name(channel_name)
    )
    marker = (
        None
        if marker_nonce is None
        else gpt_creation_journal.GptCreationMarker(f"[gpt-sync:{marker_nonce}]")
    )
    if marker is not None and marker in protections.marker_tokens:
        return ExactChannelBlocked(
            ExactChannelBlockReason.CREATION_JOURNAL_MARKER.value
        )
    if owner is None or owner.is_ordinary:
        return ExactChannelUnknown()

    state = owner.lifecycle_state
    match state:
        case gpt_ownership.MirrorThreadLifecycleState.ACTIVE:
            return ExactChannelActive(str(owner.codex_thread_id))
        case gpt_ownership.MirrorThreadLifecycleState.DEACTIVATING:
            reason = ExactChannelBlockReason.DEACTIVATING
        case gpt_ownership.MirrorThreadLifecycleState.INACTIVE:
            reason = ExactChannelBlockReason.INACTIVE
        case gpt_ownership.MirrorThreadLifecycleState.REACTIVATING:
            reason = ExactChannelBlockReason.REACTIVATING
        case _:
            assert_never(state)
    return ExactChannelBlocked(reason.value)


def resolve_exact_channel_decision(
    db_path: Path,
    discord_channel_id: int | None,
    channel_name: str | None,
) -> ExactChannelDecision:
    return resolve_exact_channel_safety(db_path, discord_channel_id, channel_name)
