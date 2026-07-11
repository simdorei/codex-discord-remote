from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
import sqlite3
from typing import TypeAlias, assert_never

import codex_discord_gpt_creation_journal as gpt_creation_journal
import codex_discord_gpt_ownership as gpt_ownership
import codex_discord_project_types as discord_project_types
import codex_discord_projects as discord_projects
import codex_discord_store as discord_store
from codex_discord_store_schema import init_store_schema
from codex_thread_models import ThreadInfo

GetDbPathFunc = Callable[[], Path]
GetProjectBridgeModuleFunc = Callable[[], discord_project_types.BridgeProjectModule]
InitMirrorDbFunc = Callable[[], None]


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
ExactOwnershipRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]


def _load_exact_owner(
    db_path: Path,
    discord_channel_id: int | None,
) -> gpt_ownership.MirrorThreadOwnership | None:
    if not discord_channel_id:
        return None
    normalized_id = gpt_ownership.DiscordThreadId(int(discord_channel_id))
    with closing(sqlite3.connect(db_path)) as conn, conn:
        init_store_schema(conn)
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


def resolve_exact_channel_safety(
    db_path: Path,
    discord_channel_id: int | None,
    channel_name: str | None,
) -> ExactChannelSafetyResult:
    """Resolve exact GPT ownership without consulting any fallback target."""
    try:
        owner = _load_exact_owner(db_path, discord_channel_id)
    except gpt_ownership.DiscordOwnershipConflictError:
        return ExactChannelBlocked(ExactChannelBlockReason.OWNERSHIP_CONFLICT.value)

    protections = gpt_creation_journal.load_gpt_creation_protections(db_path)
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


@dataclass(frozen=True, slots=True)
class ProjectRuntime:
    get_db_path: GetDbPathFunc
    get_project_bridge_module: GetProjectBridgeModuleFunc
    projectless_chat_key: str
    init_mirror_db_func: InitMirrorDbFunc
    get_mirrored_codex_thread_id_func: discord_project_types.GetMirroredCodexThreadId
    get_thread_cwd_func: discord_project_types.GetThreadCwd
    get_mirror_project_for_channel_func: (
        discord_project_types.GetMirrorProjectForChannel
    )
    project_keys_match_func: discord_project_types.ProjectKeysMatch

    def get_project_key(self, thread: ThreadInfo) -> str:
        return discord_projects.get_project_key(
            thread,
            bridge_module=self.get_project_bridge_module(),
            projectless_chat_key=self.projectless_chat_key,
        )

    def normalize_project_key(self, project_key: str | None) -> str:
        return discord_projects.normalize_project_key(
            project_key,
            bridge_module=self.get_project_bridge_module(),
            projectless_chat_key=self.projectless_chat_key,
        )

    def get_project_name(self, thread: ThreadInfo) -> str:
        return discord_projects.get_project_name(
            thread, bridge_module=self.get_project_bridge_module()
        )

    def filter_mirrorable_threads(self, threads: list[ThreadInfo]) -> list[ThreadInfo]:
        return discord_projects.filter_mirrorable_threads(
            threads,
            bridge_module=self.get_project_bridge_module(),
            projectless_chat_key=self.projectless_chat_key,
        )

    def get_mirrored_codex_thread_id(
        self, discord_channel_id: int | None
    ) -> str | None:
        return discord_store.get_mirrored_codex_thread_id(
            self.get_db_path(), discord_channel_id
        )

    def resolve_exact_channel_safety(
        self,
        discord_channel_id: int | None,
        channel_name: str | None,
    ) -> ExactChannelSafetyResult:
        return resolve_exact_channel_safety(
            self.get_db_path(),
            discord_channel_id,
            channel_name,
        )

    def resolve_exact_channel_decision(
        self,
        discord_channel_id: int | None,
        channel_name: str | None,
    ) -> ExactChannelDecision:
        return resolve_exact_channel_decision(
            self.get_db_path(),
            discord_channel_id,
            channel_name,
        )

    def persist_inbound_mirror_thread_channel(
        self, target_thread_id: str, discord_thread_id: int
    ) -> None:
        _ = discord_store.update_mirror_thread_discord_thread_id(
            self.get_db_path(),
            target_thread_id,
            int(discord_thread_id),
        )

    def describe_mirrored_project_channel(self, discord_channel_id: int | None) -> str:
        return discord_store.describe_mirrored_project_channel(
            self.get_db_path(), discord_channel_id
        )

    def get_mirror_project_for_channel(
        self, discord_channel_id: int | None
    ) -> tuple[str, str] | None:
        return discord_store.get_mirror_project_for_channel(
            self.get_db_path(), discord_channel_id
        )

    def get_thread_cwd(self, thread_id: str | None) -> str | None:
        return discord_projects.get_thread_cwd(
            thread_id, bridge_module=self.get_project_bridge_module()
        )

    def resolve_discord_new_thread_cwd(
        self, discord_channel_id: int | None
    ) -> str | None:
        return discord_projects.resolve_discord_new_thread_cwd(
            discord_channel_id,
            bridge_module=self.get_project_bridge_module(),
            projectless_chat_key=self.projectless_chat_key,
            get_mirrored_codex_thread_id_func=self.get_mirrored_codex_thread_id_func,
            get_thread_cwd_func=self.get_thread_cwd_func,
            get_mirror_project_for_channel_func=self.get_mirror_project_for_channel_func,
            find_projectless_new_chat_cwd_func=discord_projects.find_projectless_new_chat_cwd,
        )

    def project_keys_match(self, left: str | None, right: str | None) -> bool:
        return discord_projects.project_keys_match(
            left,
            right,
            bridge_module=self.get_project_bridge_module(),
            projectless_chat_key=self.projectless_chat_key,
        )

    def resolve_discord_new_thread_project_channel_id(
        self,
        discord_channel_id: int | None,
        project_key: str | None,
    ) -> int | None:
        return discord_projects.resolve_discord_new_thread_project_channel_id(
            discord_channel_id,
            project_key,
            db_path=self.get_db_path(),
            init_mirror_db_func=self.init_mirror_db_func,
            project_keys_match_func=self.project_keys_match_func,
        )

    def is_mirrored_channel_id(self, discord_channel_id: int | None) -> bool:
        return discord_store.is_mirrored_channel_id(
            self.get_db_path(), discord_channel_id
        )
