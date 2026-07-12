from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import assert_never, cast, final, override

import codex_discord_gpt_candidates as candidates
import codex_discord_gpt_creation_journal as journal
import codex_discord_gpt_cursor as cursor
import codex_discord_gpt_discord_adapter as discord_api
import codex_discord_gpt_read_service as read_service
import codex_discord_gpt_snapshots as snapshots
import codex_discord_gpt_sync_workflow as sync_workflow
import codex_discord_gpt_unsync_workflow as unsync_workflow
import codex_discord_project_runtime as project_runtime
import codex_discord_store_startup_probe as startup_probe
from codex_discord_gpt_ownership import CodexThreadId
from codex_discord_mirror_names import get_mirror_thread_name
from codex_thread_models import ThreadInfo


type ConfiguredChannelLock = AbstractAsyncContextManager[None]


class GptRuntimeInstallationError(RuntimeError):
    pass


class GptRuntimeNotReadyError(RuntimeError):
    @override
    def __str__(self) -> str:
        return (
            "GPT commands are not ready because startup reconciliation is incomplete."
        )


class GptRuntimeLockError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "GPT runtime received a different configured-channel lock."


class GptRuntimeReconciliationError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "GPT startup reconciliation could not resolve an exact Codex chat."


@dataclass(frozen=True, slots=True)
class GptCommandContext:
    key: snapshots.GptSnapshotKey


@final
class GptRuntime:
    __slots__ = (
        "_configured_channel_lock",
        "_db_path",
        "_discord_deps",
        "_ready",
        "_reconciliation",
        "_snapshot_store",
    )

    def __init__(
        self,
        db_path: Path,
        *,
        discord_deps: discord_api.GptDiscordAdapterDeps = discord_api.DEFAULT_DEPS,
    ) -> None:
        self._db_path = db_path
        self._discord_deps = discord_deps
        self._snapshot_store = snapshots.GptSnapshotStore()
        self._configured_channel_lock: ConfiguredChannelLock | None = None
        self._reconciliation: startup_probe.ReconciliationComplete | None = None
        self._ready = False

    @property
    def snapshot_store(self) -> snapshots.GptSnapshotStore:
        return self._snapshot_store

    @property
    def configured_channel_lock(self) -> ConfiguredChannelLock:
        lock = self._configured_channel_lock
        if lock is None:
            raise GptRuntimeLockError()
        return lock

    @property
    def reconciliation(self) -> startup_probe.ReconciliationComplete | None:
        return self._reconciliation

    @property
    def ready(self) -> bool:
        return self._ready

    def bind_configured_channel_lock(self, lock: ConfiguredChannelLock) -> None:
        current = self._configured_channel_lock
        if current is not None and current is not lock:
            raise GptRuntimeLockError()
        self._configured_channel_lock = lock

    def require_ready(self) -> None:
        if not self._ready:
            raise GptRuntimeNotReadyError()

    def require_reconciliation(self) -> startup_probe.ReconciliationComplete:
        reconciliation = self._reconciliation
        if reconciliation is None:
            raise startup_probe.ReconciliationRequiredError()
        return reconciliation

    def mirror_reconciliation(
        self, limit: int | None
    ) -> startup_probe.ReconciliationComplete | None:
        if self._reconciliation is not None:
            return self._reconciliation
        if limit is not None:
            return None
        mappings = read_service.load_gpt_mappings_read_only(self._db_path)
        protections = journal.load_gpt_creation_protections(self._db_path)
        if mappings or protections.unfinished:
            raise startup_probe.ReconciliationRequiredError()
        return None

    async def command_context(
        self,
        client: discord_api.DiscordClient,
        guild_id: int | None,
        author_id: int,
    ) -> GptCommandContext:
        self.require_ready()
        channel = await discord_api.resolve_configured_text_channel(
            client, deps=self._discord_deps
        )
        if guild_id is None or guild_id != channel.guild.id:
            raise discord_api.GptDiscordAccessError()
        return GptCommandContext(
            snapshots.GptSnapshotKey(channel.guild.id, channel.id, author_id)
        )

    def read_service(self) -> read_service.GptReadService:
        return read_service.GptReadService(
            self._db_path,
            read_service.create_gpt_read_deps(self._snapshot_store),
        )

    async def sync(
        self,
        client: discord_api.DiscordClient,
        context: GptCommandContext,
        raw_indices: str | None,
    ) -> None:
        await sync_workflow.sync_gpt_selection(
            sync_workflow.GptSyncRequest(
                self._db_path,
                self._snapshot_store,
                context.key,
                raw_indices,
                client,
                self.configured_channel_lock,
            )
        )

    def _source_for(self, owner: CodexThreadId) -> ThreadInfo:
        matches = tuple(
            source for source in candidates.load_gpt_candidates(0) if source.id == owner
        )
        if len(matches) != 1:
            raise GptRuntimeReconciliationError()
        return matches[0]

    def _finalize_cursor(self, operation: journal.GptCreationOperation) -> None:
        source = self._source_for(operation.codex_thread_id)
        _ = cursor.establish_reactivation_cursor(
            cursor.GptCursorRequest(
                self._db_path,
                operation.codex_thread_id,
                Path(source.rollout_path),
            )
        )

    def unsync_workflow(
        self, client: discord_api.DiscordClient
    ) -> unsync_workflow.GptUnsyncWorkflow:
        return unsync_workflow.GptUnsyncWorkflow(
            self._db_path,
            unsync_workflow.GptUnsyncWorkflowDeps(
                self.configured_channel_lock,
                self._snapshot_store,
                client,
                self._discord_deps,
                self._finalize_cursor,
            ),
        )

    async def reconcile(self, client: discord_api.DiscordClient) -> None:
        self._ready = False
        self._reconciliation = None
        _ = await discord_api.resolve_configured_text_channel(
            client, deps=self._discord_deps
        )
        lock = self.configured_channel_lock
        async with lock:
            protections = journal.load_gpt_creation_protections(self._db_path)
            for operation in protections.unfinished:
                if operation.status is journal.GptCreationStatus.PREPARED:
                    journal.cancel_gpt_creation(self._db_path, operation)
                    continue
                source = self._source_for(operation.codex_thread_id)
                final_name = get_mirror_thread_name(
                    source, get_thread_ui_name=lambda _thread_id, _thread: ""
                )
                _ = await discord_api.recover_gpt_creation(
                    client,
                    journal.GptCreationRecoveryRequest(
                        self._db_path,
                        operation,
                        final_name,
                        self._finalize_cursor,
                    ),
                    self._discord_deps,
                )
            reconciliation_factory = cast(
                Callable[[ConfiguredChannelLock], startup_probe.ReconciliationComplete],
                startup_probe.ReconciliationComplete,
            )
            self._reconciliation = reconciliation_factory(lock)
            self._ready = True

    def resolve_exact_channel_decision(
        self, channel_id: int | None, channel_name: str | None
    ) -> project_runtime.ExactChannelDecision:
        return project_runtime.resolve_exact_channel_decision(
            self._db_path, channel_id, channel_name
        )

    def resolve_routable_thread_id(
        self,
        fallback: Callable[[int | None], str | None],
        channel_id: int | None,
    ) -> str | None:
        decision = self.resolve_exact_channel_decision(channel_id, None)
        match decision:
            case project_runtime.ExactChannelActive(codex_thread_id=owner):
                return owner
            case project_runtime.ExactChannelBlocked():
                return None
            case project_runtime.ExactChannelUnknown():
                return fallback(channel_id)
            case _:
                assert_never(decision)


def install_gpt_runtime(module: ModuleType) -> GptRuntime:
    current = cast(object, getattr(module, "GPT_RUNTIME", None))
    if current is None:
        runtime = GptRuntime(cast(Path, getattr(module, "MIRROR_DB_PATH")))
        setattr(module, "GPT_RUNTIME", runtime)
    elif isinstance(current, GptRuntime):
        runtime = current
    else:
        raise GptRuntimeInstallationError("GPT_RUNTIME has an invalid runtime type.")
    setattr(
        module, "resolve_exact_channel_decision", runtime.resolve_exact_channel_decision
    )
    return runtime
