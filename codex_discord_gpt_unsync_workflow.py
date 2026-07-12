from __future__ import annotations

# Pyright flags mandatory assert_never defaults after proving every enum case.
# pyright: reportUnnecessaryComparison=false

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal, assert_never, override

import discord

import codex_discord_gpt_creation_journal as journal
import codex_discord_gpt_creation_journal_store as journal_store
import codex_discord_gpt_discord_adapter as adapter
import codex_discord_gpt_lifecycle as lifecycle
import codex_discord_gpt_ownership as own
import codex_discord_gpt_snapshots as snapshots
from codex_discord_gpt_migration import GPT_PROJECT_KEY
from codex_discord_gpt_read_service import load_gpt_mappings_read_only


GptCreationOperation = journal.GptCreationOperation
type WorkflowReason = Literal[
    "missing_mapping", "mapping_identity", "snapshot_state", "many_markers"
]


@dataclass(frozen=True, slots=True)
class GptWorkflowError(RuntimeError):
    codex_thread_id: own.CodexThreadId
    reason: WorkflowReason

    @override
    def __str__(self) -> str:
        return f"GPT workflow stopped for {self.codex_thread_id}: {self.reason}."


class GptUnsyncPreflightError(GptWorkflowError):
    pass


class GptClearJournalError(GptWorkflowError):
    pass


class GptDiscordArchiveError(adapter.GptDiscordError):
    """Discord could not archive and lock the exact retained GPT thread."""


@dataclass(frozen=True, slots=True)
class GptUnsyncWorkflowDeps:
    configured_channel_lock: AbstractAsyncContextManager[bool | None]
    snapshot_store: snapshots.GptSnapshotStore
    discord_client: adapter.DiscordClient
    discord_deps: adapter.GptDiscordAdapterDeps
    finalize_cursor: Callable[[GptCreationOperation], None]


@dataclass(frozen=True, slots=True)
class _ArchiveTarget:
    mapping: own.MirrorThreadOwnership
    thread: discord.Thread
    begin: lifecycle.GptLifecycleOperation


type _Operations = tuple[GptCreationOperation, ...]
type _ClearPlan = tuple[tuple[_ArchiveTarget, ...], _Operations, _Operations]


def _require_owner(db_path: Path, mapping: own.MirrorThreadOwnership) -> None:
    owner = own.get_mirror_thread_owner_by_discord_thread_id(
        db_path, mapping.discord_thread_id
    )
    if owner != mapping:
        raise GptUnsyncPreflightError(mapping.codex_thread_id, "mapping_identity")


async def _load_exact_thread(
    channel: discord.TextChannel,
    mapping: own.MirrorThreadOwnership,
    deps: GptUnsyncWorkflowDeps,
) -> discord.Thread:
    guild = channel.guild
    thread = guild.get_channel(mapping.discord_thread_id)
    if thread is None:
        try:
            thread = await guild.fetch_channel(mapping.discord_thread_id)
        except deps.discord_deps.discord_failure_types as exc:
            raise GptDiscordArchiveError() from exc
    if not isinstance(thread, discord.Thread):
        raise GptDiscordArchiveError()
    identity = thread.id, thread.guild.id, thread.parent_id
    expected = mapping.discord_thread_id, guild.id, mapping.discord_channel_id
    if identity != expected:
        raise GptDiscordArchiveError()
    return thread


def _read_recovery_state(
    db_path: Path,
    operation: GptCreationOperation,
    discord_thread_id: own.DiscordThreadId,
) -> own.MirrorThreadLifecycleState | None:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        return journal_store.mapping_state(conn, operation, discord_thread_id)


async def _archive_target(
    db_path: Path,
    target: _ArchiveTarget,
    deps: GptUnsyncWorkflowDeps,
) -> None:
    owner = target.mapping.codex_thread_id
    _ = lifecycle.transition_gpt_lifecycle(db_path, owner, target.begin)
    if not target.thread.archived or not target.thread.locked:
        try:
            thread = await target.thread.edit(
                archived=True, locked=True, reason="Deactivate GPT sync"
            )
        except deps.discord_deps.discord_failure_types as exc:
            raise GptDiscordArchiveError() from exc
        if not thread.archived or not thread.locked:
            raise GptDiscordArchiveError()
    _ = lifecycle.transition_gpt_lifecycle(
        db_path, owner, lifecycle.GptLifecycleOperation.FINALIZE_DEACTIVATION
    )


@dataclass(frozen=True, slots=True)
class GptUnsyncWorkflow:
    db_path: Path
    deps: GptUnsyncWorkflowDeps

    async def _preflight_unsync(
        self, key: snapshots.GptSnapshotKey, raw_indices: str | None
    ) -> tuple[_ArchiveTarget, ...]:
        selected = self.deps.snapshot_store.select(
            key, snapshots.GptSnapshotKind.SYNCED, raw_indices
        )
        mappings = {
            mapping.codex_thread_id: mapping
            for mapping in load_gpt_mappings_read_only(self.db_path)
        }
        channel = await adapter.resolve_configured_text_channel(
            self.deps.discord_client, self.deps.discord_deps
        )
        targets: list[_ArchiveTarget] = []
        for raw_owner in selected:
            owner = own.CodexThreadId(raw_owner)
            mapping = mappings.get(owner)
            if mapping is None:
                raise GptUnsyncPreflightError(owner, "missing_mapping")
            match mapping.lifecycle_state:
                case (
                    own.MirrorThreadLifecycleState.ACTIVE
                    | own.MirrorThreadLifecycleState.DEACTIVATING
                ):
                    begin = lifecycle.GptLifecycleOperation.BEGIN_DEACTIVATION
                case (
                    own.MirrorThreadLifecycleState.REACTIVATING
                    | own.MirrorThreadLifecycleState.INACTIVE
                ):
                    raise GptUnsyncPreflightError(owner, "snapshot_state")
                case _ as unreachable:
                    assert_never(unreachable)
            if mapping.project_key != GPT_PROJECT_KEY:
                raise GptUnsyncPreflightError(owner, "mapping_identity")
            _require_owner(self.db_path, mapping)
            thread = await _load_exact_thread(channel, mapping, self.deps)
            targets.append(_ArchiveTarget(mapping, thread, begin))
        return tuple(targets)

    async def _preflight_clear(self) -> _ClearPlan:
        _ = lifecycle.audit_gpt_capacity(self.db_path, requested_increase=0)
        mappings = load_gpt_mappings_read_only(self.db_path)
        for mapping in mappings:
            _require_owner(self.db_path, mapping)
        by_owner = {mapping.codex_thread_id: mapping for mapping in mappings}
        operations = journal.load_gpt_creation_protections(self.db_path).unfinished
        channel = await adapter.resolve_configured_text_channel(
            self.deps.discord_client, self.deps.discord_deps
        )
        cancellations: list[GptCreationOperation] = []
        recoveries: list[GptCreationOperation] = []
        for operation in operations:
            owner = operation.codex_thread_id
            match operation.status:
                case journal.GptCreationStatus.PREPARED:
                    cancellations.append(operation)
                case journal.GptCreationStatus.CREATE_STARTED:
                    matches = await adapter.scan_exact_creation_marker(
                        channel, operation, self.deps.discord_deps
                    )
                    if len(matches) == 0:
                        cancellations.append(operation)
                        continue
                    if len(matches) > 1:
                        raise GptClearJournalError(owner, "many_markers")
                    discord_thread_id = own.DiscordThreadId(int(matches[0].id))
                    _ = _read_recovery_state(self.db_path, operation, discord_thread_id)
                    recoveries.append(operation)
                case journal.GptCreationStatus.DISCORD_IDENTIFIED:
                    discord_thread_id = operation.discord_thread_id
                    if discord_thread_id is None:
                        raise GptClearJournalError(owner, "mapping_identity")
                    if (
                        _read_recovery_state(self.db_path, operation, discord_thread_id)
                        is None
                    ):
                        raise GptClearJournalError(owner, "mapping_identity")
                    mapping = by_owner[operation.codex_thread_id]
                    _ = await _load_exact_thread(channel, mapping, self.deps)
                    recoveries.append(operation)
                case _ as unreachable:
                    assert_never(unreachable)
        recovery_owners = {operation.codex_thread_id for operation in recoveries}
        archives: list[_ArchiveTarget] = []
        for mapping in mappings:
            if mapping.codex_thread_id in recovery_owners:
                continue
            match mapping.lifecycle_state:
                case (
                    own.MirrorThreadLifecycleState.ACTIVE
                    | own.MirrorThreadLifecycleState.DEACTIVATING
                    | own.MirrorThreadLifecycleState.REACTIVATING
                ):
                    archives.append(
                        _ArchiveTarget(
                            mapping,
                            await _load_exact_thread(channel, mapping, self.deps),
                            lifecycle.GptLifecycleOperation.BEGIN_CLEAR_DEACTIVATION,
                        )
                    )
                case own.MirrorThreadLifecycleState.INACTIVE:
                    pass
                case _ as unreachable:
                    assert_never(unreachable)
        return tuple(archives), tuple(cancellations), tuple(recoveries)

    async def unsync(
        self, key: snapshots.GptSnapshotKey, raw_indices: str | None
    ) -> None:
        async with self.deps.configured_channel_lock:
            targets = await self._preflight_unsync(key, raw_indices)
            for target in targets:
                await _archive_target(self.db_path, target, self.deps)

    async def sync_clear(self) -> None:
        async with self.deps.configured_channel_lock:
            archives, cancellations, recoveries = await self._preflight_clear()
            for operation in recoveries:
                thread = await adapter.recover_gpt_creation(
                    self.deps.discord_client,
                    adapter.GptCreationRecoveryRequest(
                        self.db_path,
                        operation,
                        operation.thread_title,
                        self.deps.finalize_cursor,
                    ),
                    self.deps.discord_deps,
                )
                mapping = own.get_mirror_thread_owner_by_codex_thread_id(
                    self.db_path, operation.codex_thread_id
                )
                if mapping is None:
                    raise GptClearJournalError(
                        operation.codex_thread_id, "mapping_identity"
                    )
                await _archive_target(
                    self.db_path,
                    _ArchiveTarget(
                        mapping,
                        thread,
                        lifecycle.GptLifecycleOperation.BEGIN_CLEAR_DEACTIVATION,
                    ),
                    self.deps,
                )
            for target in archives:
                await _archive_target(self.db_path, target, self.deps)
            for operation in cancellations:
                journal.cancel_gpt_creation(self.db_path, operation)
