from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeAlias, TypeVar

import codex_discord_gpt_delivery as gpt_delivery
import codex_discord_session_mirror_commit as session_mirror_commit
import codex_discord_session_mirror_item_sender as session_mirror_item_sender

ChannelT = TypeVar("ChannelT")
ThreadT_co = TypeVar("ThreadT_co", covariant=True)
EventT = TypeVar("EventT")
SessionMirrorItem: TypeAlias = session_mirror_item_sender.SessionMirrorItem


class ChooseThreadSync(Protocol[ThreadT_co]):
    def __call__(self, thread_id: str | None, cwd: str | None) -> ThreadT_co: ...


class ReadNewSessionEventsSync(Protocol[EventT]):
    def __call__(
        self,
        session_path: Path,
        cursor: int,
        *,
        max_events: int | None = None,
    ) -> tuple[list[EventT], int]: ...


class SessionMirrorItemCollector(Protocol[EventT]):
    def __call__(
        self,
        codex_thread_id: str,
        events: list[EventT],
        *,
        seen_agent_messages: dict[str, float],
        seen_user_messages: dict[str, float],
    ) -> list[SessionMirrorItem]: ...


class _TypingChannel(Protocol):
    pass


async def noop_send_typing_pulse(
    channel: _TypingChannel,
    target_thread_id: str,
    context: str,
) -> None:
    _ = (channel, target_thread_id, context)


def default_thread_busy(session_path: Path) -> bool:
    _ = session_path
    return True


@dataclass(frozen=True, slots=True)
class SessionMirrorDeliveryFlowDeps(Generic[ChannelT]):
    configured_channel_lock: asyncio.Lock
    active_delivery_lease_deps: gpt_delivery.ActiveDeliveryLeaseDeps
    resolve_session_mirror_channel: Callable[[int], Awaitable[ChannelT | None]]
    resolve_target_ref: Callable[[str], tuple[str | None, str]]
    has_session_mirror_event: session_mirror_item_sender.SessionMirrorEventChecker
    send_session_mirror_item: session_mirror_item_sender.SessionMirrorItemSender[
        ChannelT
    ]
    claim_session_mirror_event: session_mirror_item_sender.SessionMirrorEventClaimer
    update_session_mirror_cursor: session_mirror_commit.CursorUpdater
    deactivate_session_mirror_output_target: (
        session_mirror_commit.OutputTargetDeactivator
    )
    log: session_mirror_commit.LogFunc

    def __post_init__(self) -> None:
        gpt_delivery.require_configured_channel_lock(
            self.configured_channel_lock,
            self.active_delivery_lease_deps,
        )


async def deliver_and_commit_session_mirror_items(
    codex_thread_id: str,
    rollout_path: str,
    next_cursor: int,
    *,
    discord_thread_id: int,
    expected_identity: gpt_delivery.ActiveDeliveryIdentity,
    event_count: int,
    items: Sequence[SessionMirrorItem],
    deps: SessionMirrorDeliveryFlowDeps[ChannelT],
) -> bool:
    if (
        str(expected_identity.codex_thread_id) != codex_thread_id
        or int(expected_identity.discord_thread_id) != discord_thread_id
    ):
        return False
    async with gpt_delivery.active_delivery_lease(
        expected_identity,
        configured_channel_lock=deps.configured_channel_lock,
        deps=deps.active_delivery_lease_deps,
    ) as active_identity:
        if not active_identity:
            return False
        channel = await deps.resolve_session_mirror_channel(discord_thread_id)
        if channel is None:
            return False

        _resolved_thread_id, target_ref = deps.resolve_target_ref(codex_thread_id)
        send_result = (
            await session_mirror_item_sender.send_unclaimed_session_mirror_items(
                channel,
                items,
                codex_thread_id=codex_thread_id,
                target_ref=target_ref,
                deps=session_mirror_item_sender.SessionMirrorItemSenderDeps(
                    has_session_mirror_event=deps.has_session_mirror_event,
                    send_session_mirror_item=deps.send_session_mirror_item,
                    claim_session_mirror_event=deps.claim_session_mirror_event,
                ),
            )
        )
        await session_mirror_commit.commit_session_mirror_delivery(
            codex_thread_id,
            rollout_path,
            next_cursor,
            discord_thread_id=discord_thread_id,
            event_count=event_count,
            sent_count=send_result.sent_count,
            terminal_sent=send_result.terminal_sent,
            deps=session_mirror_commit.SessionMirrorCommitDeps(
                update_session_mirror_cursor=deps.update_session_mirror_cursor,
                deactivate_session_mirror_output_target=deps.deactivate_session_mirror_output_target,
                log=deps.log,
            ),
        )
        return True
