from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Generic, TypeAlias, TypeVar

import codex_discord_gpt_delivery as gpt_delivery
import codex_discord_session_mirror as session_mirror
import codex_discord_session_mirror_cursor as session_mirror_cursor
import codex_discord_session_mirror_delivery_flow as delivery_flow
import codex_discord_session_mirror_event_flow as event_flow
import codex_discord_session_mirror_item_sender as item_sender

ThreadT = TypeVar("ThreadT")
ContextUsageT = TypeVar("ContextUsageT")
EventT = TypeVar("EventT")
ChannelT = TypeVar("ChannelT")

SessionMirrorTargetValue: TypeAlias = session_mirror.SessionMirrorTargetValue
SessionMirrorTargetMapping: TypeAlias = Mapping[str, SessionMirrorTargetValue]
SessionMirrorItem: TypeAlias = item_sender.SessionMirrorItem


@dataclass(frozen=True, slots=True)
class SessionMirrorTargetDeps(Generic[ThreadT, ContextUsageT, EventT, ChannelT]):
    parse_session_mirror_target: Callable[
        [SessionMirrorTargetMapping],
        session_mirror.SessionMirrorTarget | None,
    ]
    choose_thread: delivery_flow.ChooseThreadSync[ThreadT]
    get_thread_context_usage: Callable[[ThreadT], ContextUsageT]
    should_recommend_archive: Callable[[ThreadT, ContextUsageT], bool]
    get_thread_rollout_path: Callable[[ThreadT], str]
    is_active_output_target: Callable[[str], bool]
    archive_skip_logged: set[str]
    is_pending_cursor_target: session_mirror_cursor.PendingCursorPredicate
    clear_pending_cursor_target: session_mirror_cursor.PendingCursorClearer
    update_session_mirror_cursor: Callable[[str, str, int], None]
    get_or_init_session_mirror_cursor: Callable[[str, str, int], int]
    read_new_session_events: delivery_flow.ReadNewSessionEventsSync[EventT]
    get_archive_backlog_max_events: Callable[[], int]
    collect_session_mirror_items: delivery_flow.SessionMirrorItemCollector[EventT]
    get_seen_agent_messages: Callable[[str], dict[str, float]]
    get_seen_user_messages: Callable[[str], dict[str, float]]
    resolve_session_mirror_channel: Callable[[int], Awaitable[ChannelT | None]]
    resolve_target_ref: Callable[[str], tuple[str | None, str]]
    has_session_mirror_event: Callable[[str, str], bool]
    send_session_mirror_item: item_sender.SessionMirrorItemSender[ChannelT]
    claim_session_mirror_event: Callable[[str, str], bool]
    deactivate_session_mirror_output_target: Callable[[str], None]
    log: Callable[[str], None]
    send_typing_pulse: Callable[[ChannelT, str, str], Awaitable[None]] = (
        delivery_flow.noop_send_typing_pulse
    )
    is_thread_busy: Callable[[Path], bool] = delivery_flow.default_thread_busy
    configured_channel_lock: asyncio.Lock | None = None
    active_delivery_lease_deps: gpt_delivery.ActiveDeliveryLeaseDeps | None = None

    def __post_init__(self) -> None:
        _ = gpt_delivery.resolve_active_delivery_lease_configuration(
            self.configured_channel_lock,
            self.active_delivery_lease_deps,
        )


async def _read_events(
    deps: SessionMirrorTargetDeps[ThreadT, ContextUsageT, EventT, ChannelT],
    session_path: Path,
    cursor: int,
    max_events: int | None,
) -> tuple[list[EventT], int]:
    if max_events is None:
        read_events = partial(deps.read_new_session_events, session_path, cursor)
    else:
        read_events = partial(
            deps.read_new_session_events,
            session_path,
            cursor,
            max_events=max_events,
        )
    return await asyncio.to_thread(read_events)


async def _send_typing_pulse_if_busy(
    deps: SessionMirrorTargetDeps[ThreadT, ContextUsageT, EventT, ChannelT],
    codex_thread_id: str,
    discord_thread_id: int,
) -> None:
    if not deps.is_active_output_target(codex_thread_id):
        return
    channel = await deps.resolve_session_mirror_channel(discord_thread_id)
    if channel is None:
        return
    await deps.send_typing_pulse(channel, codex_thread_id, "session_mirror_busy")
    deps.log(
        f"session_mirror_typing_pulse target={codex_thread_id} channel={discord_thread_id}"
    )


async def _deactivate_output_target_if_idle(
    deps: SessionMirrorTargetDeps[ThreadT, ContextUsageT, EventT, ChannelT],
    codex_thread_id: str,
) -> bool:
    if not deps.is_active_output_target(codex_thread_id):
        return False
    try:
        codex_thread = await asyncio.to_thread(
            deps.choose_thread, codex_thread_id, None
        )
    except (OSError, RuntimeError):
        return False
    session_path = Path(deps.get_thread_rollout_path(codex_thread))
    if not session_path.exists():
        return False
    if await asyncio.to_thread(deps.is_thread_busy, session_path):
        return False
    await asyncio.to_thread(
        deps.deactivate_session_mirror_output_target, codex_thread_id
    )
    deps.log(f"session_mirror_output_deactivated_idle target={codex_thread_id}")
    return True


async def mirror_session_target(
    target: SessionMirrorTargetMapping,
    *,
    deps: SessionMirrorTargetDeps[ThreadT, ContextUsageT, EventT, ChannelT],
) -> None:
    mirror_target = deps.parse_session_mirror_target(target)
    if mirror_target is None:
        return
    codex_thread_id = mirror_target.codex_thread_id
    discord_thread_id = mirror_target.discord_thread_id
    delivery_configuration = gpt_delivery.resolve_active_delivery_lease_configuration(
        deps.configured_channel_lock,
        deps.active_delivery_lease_deps,
    )
    delivery_identity: gpt_delivery.ActiveDeliveryIdentity | None = None
    if delivery_configuration is not None:
        _configured_channel_lock, lease_deps = delivery_configuration
        delivery_identity = await gpt_delivery.reread_active_delivery_identity(
            codex_thread_id,
            discord_thread_id,
            deps=lease_deps,
        )
        if delivery_identity is None:
            return

    prepared_items = await event_flow.prepare_session_mirror_delivery_items(
        codex_thread_id,
        deps=event_flow.SessionMirrorEventFlowDeps(
            choose_thread=lambda codex_thread_id: asyncio.to_thread(
                deps.choose_thread,
                codex_thread_id,
                None,
            ),
            get_thread_context_usage=lambda codex_thread: asyncio.to_thread(
                deps.get_thread_context_usage,
                codex_thread,
            ),
            should_recommend_archive=deps.should_recommend_archive,
            get_thread_rollout_path=deps.get_thread_rollout_path,
            is_active_output_target=deps.is_active_output_target,
            archive_skip_logged=deps.archive_skip_logged,
            is_pending_cursor_target=deps.is_pending_cursor_target,
            clear_pending_cursor_target=deps.clear_pending_cursor_target,
            update_session_mirror_cursor=lambda codex_thread_id, rollout_path, cursor: (
                asyncio.to_thread(
                    deps.update_session_mirror_cursor,
                    codex_thread_id,
                    rollout_path,
                    cursor,
                )
            ),
            get_or_init_session_mirror_cursor=lambda codex_thread_id, rollout_path, initial_cursor: (
                asyncio.to_thread(
                    deps.get_or_init_session_mirror_cursor,
                    codex_thread_id,
                    rollout_path,
                    initial_cursor,
                )
            ),
            read_events=lambda session_path, cursor, max_events: _read_events(
                deps,
                session_path,
                cursor,
                max_events,
            ),
            get_archive_backlog_max_events=deps.get_archive_backlog_max_events,
            collect_session_mirror_items=deps.collect_session_mirror_items,
            get_seen_agent_messages=deps.get_seen_agent_messages,
            get_seen_user_messages=deps.get_seen_user_messages,
            log=deps.log,
        ),
    )
    if prepared_items is None:
        if await _deactivate_output_target_if_idle(deps, codex_thread_id):
            return
        await _send_typing_pulse_if_busy(deps, codex_thread_id, discord_thread_id)
        return

    if delivery_configuration is None or delivery_identity is None:
        raise gpt_delivery.ActiveDeliveryLeaseConfigurationError()
    configured_channel_lock, lease_deps = delivery_configuration

    _ = await delivery_flow.deliver_and_commit_session_mirror_items(
        codex_thread_id,
        prepared_items.rollout_path,
        prepared_items.next_cursor,
        discord_thread_id=discord_thread_id,
        expected_identity=delivery_identity,
        event_count=len(prepared_items.events),
        items=prepared_items.items,
        deps=delivery_flow.SessionMirrorDeliveryFlowDeps(
            configured_channel_lock=configured_channel_lock,
            active_delivery_lease_deps=lease_deps,
            resolve_session_mirror_channel=deps.resolve_session_mirror_channel,
            resolve_target_ref=deps.resolve_target_ref,
            has_session_mirror_event=lambda digest, codex_thread_id: asyncio.to_thread(
                deps.has_session_mirror_event,
                digest,
                codex_thread_id,
            ),
            send_session_mirror_item=deps.send_session_mirror_item,
            claim_session_mirror_event=lambda digest, codex_thread_id: (
                asyncio.to_thread(
                    deps.claim_session_mirror_event,
                    digest,
                    codex_thread_id,
                )
            ),
            update_session_mirror_cursor=lambda codex_thread_id, rollout_path, cursor: (
                asyncio.to_thread(
                    deps.update_session_mirror_cursor,
                    codex_thread_id,
                    rollout_path,
                    cursor,
                )
            ),
            deactivate_session_mirror_output_target=deps.deactivate_session_mirror_output_target,
            log=deps.log,
        ),
    )
