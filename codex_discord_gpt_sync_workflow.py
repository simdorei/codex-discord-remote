import sqlite3
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NamedTuple, assert_never, override

import codex_discord_gpt_candidates as candidates
import codex_discord_gpt_creation_journal as journal
import codex_discord_gpt_cursor as cursor
import codex_discord_gpt_discord_adapter as discord_api
import codex_discord_gpt_lifecycle as lifecycle
import codex_discord_gpt_ownership as own
import codex_discord_gpt_snapshots as snapshots
from codex_discord_mirror_names import get_mirror_thread_name
from codex_thread_models import ThreadInfo


Mapping = own.MirrorThreadOwnership
Operation = journal.GptCreationOperation
State = own.MirrorThreadLifecycleState
Status = journal.GptCreationStatus
get_discord_owner = own.get_mirror_thread_owner_by_discord_thread_id
_RETRY_MESSAGE: Final = "GPT sync stopped; durable state is retryable."


@dataclass(frozen=True, slots=True)
class GptSyncPreflightError(RuntimeError):
    reason: str

    @override
    def __str__(self) -> str:
        return f"GPT sync preflight failed: {self.reason}."


class GptSyncRetryableError(RuntimeError):
    """GPT sync failed after execution began; durable state is retryable."""


@dataclass(frozen=True, slots=True)
class GptSyncRequest:
    db_path: Path
    snapshot_store: snapshots.GptSnapshotStore
    snapshot_key: snapshots.GptSnapshotKey
    raw_indices: str | None
    client: discord_api.DiscordClient
    configured_channel_lock: AbstractAsyncContextManager[None]


class _Plan(NamedTuple):
    source: ThreadInfo
    mapping: Mapping | None
    operation: Operation | None
    mode: Literal["active", "retained", "creation"]
    increase: bool


def _check_mapping(db_path: Path, source: ThreadInfo, mapping: Mapping) -> None:
    if mapping.project_key != "codex:chats" or mapping.thread_title != source.title:
        raise GptSyncPreflightError("mapping identity conflict")
    managed_by = mapping.managed_by
    if managed_by is own.MirrorThreadManagedBy.GPT_CHAT:
        discord_owner = get_discord_owner(db_path, mapping.discord_thread_id)
        if discord_owner != mapping:
            raise GptSyncPreflightError("Discord ownership conflict")
        return
    if managed_by is own.MirrorThreadManagedBy.ORDINARY:
        raise GptSyncPreflightError("mapping owner conflict")
    assert_never(managed_by)


def _existing(db_path: Path, source: ThreadInfo, mapping: Mapping) -> _Plan:
    _check_mapping(db_path, source, mapping)
    state = mapping.lifecycle_state
    if state is State.ACTIVE:
        return _Plan(source, mapping, None, "active", False)
    if state is State.INACTIVE:
        return _Plan(source, mapping, None, "retained", True)
    if state is State.REACTIVATING:
        return _Plan(source, mapping, None, "retained", False)
    if state is State.DEACTIVATING:
        raise GptSyncPreflightError("mapping is deactivating")
    assert_never(state)


def _classify(
    db_path: Path,
    source: ThreadInfo,
    mapping: Mapping | None,
    operation: Operation | None,
) -> _Plan:
    if operation is None:
        if mapping is None:
            return _Plan(source, None, None, "creation", True)
        return _existing(db_path, source, mapping)
    if operation.project_key != "codex:chats" or operation.thread_title != source.title:
        raise GptSyncPreflightError("creation identity conflict")
    status = operation.status
    if status is Status.PREPARED or status is Status.CREATE_STARTED:
        if mapping is not None:
            raise GptSyncPreflightError("partial creation conflict")
        return _Plan(source, mapping, operation, "creation", False)
    if status is Status.DISCORD_IDENTIFIED:
        if mapping is None or operation.discord_thread_id is None:
            raise GptSyncPreflightError("identified creation is incomplete")
        existing = _existing(db_path, source, mapping)
        if (
            existing.increase
            or mapping.discord_thread_id != operation.discord_thread_id
            or mapping.discord_channel_id != operation.discord_parent_channel_id
        ):
            raise GptSyncPreflightError("identified creation conflict")
        return _Plan(source, mapping, operation, "creation", False)
    assert_never(status)


async def _preflight(request: GptSyncRequest) -> tuple[int, tuple[_Plan, ...]]:
    selected = request.snapshot_store.select(
        request.snapshot_key, snapshots.GptSnapshotKind.LIST, request.raw_indices
    )
    selected_set = set(selected)
    current = tuple(
        item for item in candidates.load_gpt_candidates(0) if item.id in selected_set
    )
    sources = {item.id: item for item in current}
    if len(current) != len(selected) or set(sources) != selected_set:
        raise GptSyncPreflightError("saved source is unavailable or duplicated")
    mappings = {
        key: own.get_mirror_thread_owner_by_codex_thread_id(request.db_path, key)
        for key in selected
    }
    operations = {
        str(item.codex_thread_id): item
        for item in journal.load_gpt_creation_protections(request.db_path).unfinished
    }
    plans = tuple(
        _classify(request.db_path, sources[key], mappings.get(key), operations.get(key))
        for key in selected
    )
    _ = lifecycle.audit_gpt_capacity(
        request.db_path, requested_increase=sum(item.increase for item in plans)
    )
    configured = await discord_api.resolve_configured_text_channel(request.client)
    key = request.snapshot_key
    if (configured.guild.id, configured.id) != (
        key.guild_id,
        key.configured_general_channel_id,
    ):
        raise GptSyncPreflightError("configured channel identity changed")
    if any(
        item.operation is not None
        and int(item.operation.discord_parent_channel_id) != configured.id
        for item in plans
    ):
        raise GptSyncPreflightError("creation parent identity conflict")
    return int(configured.id), plans


def _reserve(
    request: GptSyncRequest, configured_channel_id: int, plans: tuple[_Plan, ...]
) -> tuple[_Plan, ...]:
    reserved: list[_Plan] = []
    for item in plans:
        mode = item.mode
        if mode == "active":
            reserved.append(item)
            continue
        if mode == "retained":
            _ = lifecycle.transition_gpt_lifecycle(
                request.db_path,
                item.source.id,
                lifecycle.GptLifecycleOperation.BEGIN_REACTIVATION,
            )
            reserved.append(item)
            continue
        if mode == "creation":
            operation = item.operation or journal.prepare_gpt_creation(
                request.db_path,
                journal.GptCreationIntent(
                    own.CodexThreadId(item.source.id),
                    item.source.title,
                    own.DiscordChannelId(configured_channel_id),
                ),
            )
            reserved.append(item._replace(operation=operation))
            continue
        assert_never(mode)
    return tuple(reserved)


def _cursor(request: GptSyncRequest, source: ThreadInfo) -> None:
    _ = cursor.establish_reactivation_cursor(
        cursor.GptCursorRequest(
            request.db_path, own.CodexThreadId(source.id), Path(source.rollout_path)
        )
    )


async def _finish_creation(
    request: GptSyncRequest, item: _Plan, operation: Operation
) -> None:
    def finalize_cursor(_operation: Operation) -> None:
        _cursor(request, item.source)

    final_name = get_mirror_thread_name(
        item.source, get_thread_ui_name=lambda _thread_id, _thread: ""
    )
    _ = await discord_api.recover_gpt_creation(
        request.client,
        journal.GptCreationRecoveryRequest(
            request.db_path, operation, final_name, finalize_cursor
        ),
    )


async def _execute(request: GptSyncRequest, item: _Plan) -> None:
    mode = item.mode
    if mode == "active":
        return
    if mode == "retained":
        if item.mapping is None:
            raise GptSyncPreflightError("retained mapping disappeared")
        _ = await discord_api.revive_retained_gpt_thread(request.client, item.mapping)
        _cursor(request, item.source)
        _ = lifecycle.transition_gpt_lifecycle(
            request.db_path,
            item.source.id,
            lifecycle.GptLifecycleOperation.FINALIZE_REACTIVATION,
        )
        return
    if mode == "creation":
        operation = item.operation
        if operation is None:
            raise GptSyncPreflightError("creation reservation disappeared")
        status = operation.status
        if status is Status.PREPARED:
            operation = journal.mark_gpt_creation_started(request.db_path, operation)
            thread = await discord_api.create_gpt_marker_thread(
                request.client, operation
            )
            operation = journal.handoff_gpt_creation(
                request.db_path, operation, own.DiscordThreadId(int(thread.id))
            )
            await _finish_creation(request, item, operation)
            return
        if status is Status.CREATE_STARTED:
            await _finish_creation(request, item, operation)
            return
        if status is Status.DISCORD_IDENTIFIED:
            await _finish_creation(request, item, operation)
            return
        assert_never(status)
    assert_never(mode)


async def sync_gpt_selection(request: GptSyncRequest) -> None:
    async with request.configured_channel_lock:
        configured_channel_id, plans = await _preflight(request)
        try:
            reserved = _reserve(request, configured_channel_id, plans)
            for item in reserved:
                await _execute(request, item)
        except (
            discord_api.GptDiscordError,
            lifecycle.GptLifecycleError,
            journal.GptCreationAmbiguityError,
            journal.GptCreationMutationError,
            cursor.GptCursorError,
            sqlite3.Error,
        ) as exc:
            raise GptSyncRetryableError(_RETRY_MESSAGE) from exc
