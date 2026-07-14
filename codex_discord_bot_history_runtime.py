from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, cast, TypeAlias

import codex_discord_diagnostics_history as discord_diagnostics_history
import codex_discord_history_poll as discord_history_poll
ModuleValue: TypeAlias = object


class HistoryPollOwner(Protocol):
    allowed_channel_ids: set[int]
    startup_channel_id: int | None
    history_poll_seconds: float

    def is_closed(self) -> bool: ...

    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[ModuleValue | None, str]: ...

    async def fetch_channel(self, channel_id: int) -> ModuleValue | None: ...

    async def history_poll_loop(self) -> None: ...

    async def poll_history_channel(self, label: str, channel_id: int) -> None: ...

    async def process_discord_message(
        self,
        message: discord_diagnostics_history.DiscordHistoryMessage,
        *,
        source: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class BotHistoryRuntimeDeps:
    history_limit: int
    target_limit: int
    delivery_exceptions: tuple[type[BaseException], ...]
    get_targets: discord_history_poll.HistoryPollTargetsGetter
    claim_message: Callable[[HistoryPollOwner, discord_diagnostics_history.DiscordHistoryMessage], bool]
    mark_processed: Callable[[HistoryPollOwner, discord_diagnostics_history.DiscordHistoryMessage], None]
    release_message: Callable[[HistoryPollOwner, discord_diagnostics_history.DiscordHistoryMessage], bool]
    process_history_poll_message: Callable[
        [HistoryPollOwner, discord_diagnostics_history.DiscordHistoryMessage, int],
        Awaitable[None],
    ]
    format_log_text_len: Callable[[str | None], int | str]
    log: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class BotHistoryRuntime:
    deps: BotHistoryRuntimeDeps

    async def start_history_polling(self, owner: HistoryPollOwner) -> None:
        if owner.history_poll_seconds <= 0:
            self.deps.log("history_poll_disabled")
            return
        history_poll_task = _history_poll_task(owner)
        if history_poll_task and not history_poll_task.done():
            self.deps.log("history_poll_already_running")
            return
        setattr(owner, "_history_poll_task", asyncio.create_task(owner.history_poll_loop()))
        self.deps.log(f"history_poll_started seconds={owner.history_poll_seconds:g}")

    async def history_poll_loop(self, owner: HistoryPollOwner) -> None:
        await discord_history_poll.history_poll_loop(
            discord_history_poll.HistoryPollLoopDeps(
                allowed_channel_ids=owner.allowed_channel_ids,
                startup_channel_id=owner.startup_channel_id,
                poll_seconds=owner.history_poll_seconds,
                target_limit=self.deps.target_limit,
                is_closed=owner.is_closed,
                set_last_at=lambda value: setattr(owner, "_history_poll_last_at", value),
                now_iso=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
                get_targets=self.deps.get_targets,
                poll_history_channel=owner.poll_history_channel,
                delivery_exceptions=self.deps.delivery_exceptions,
                format_traceback=traceback.format_exc,
                sleep=asyncio.sleep,
                log=self.deps.log,
            )
        )

    async def poll_history_channel(
        self,
        owner: HistoryPollOwner,
        label: str,
        channel_id: int,
    ) -> None:
        deps: discord_history_poll.PollHistoryChannelDeps[
            discord_diagnostics_history.DiscordHistoryMessage
        ] = discord_history_poll.PollHistoryChannelDeps(
            get_cached_channel_or_thread=owner.get_cached_channel_or_thread,
            fetch_channel=owner.fetch_channel,
            delivery_exceptions=self.deps.delivery_exceptions,
            history_limit=self.deps.history_limit,
            is_primed_channel=lambda channel_id: channel_id in _history_poll_primed_channels(owner),
            mark_primed_channel=lambda channel_id: _history_poll_primed_channels(owner).add(channel_id),
            claim_message=lambda message: self.deps.claim_message(owner, message),
            mark_processed=lambda message: self.deps.mark_processed(owner, message),
            release_message=lambda message: self.deps.release_message(owner, message),
            is_bootstrap_user_message=lambda message: _is_history_bootstrap_user_message(owner, message),
            process_history_poll_message=lambda message, channel_id: self.deps.process_history_poll_message(
                owner,
                message,
                channel_id,
            ),
            log=self.deps.log,
        )
        await discord_history_poll.poll_history_channel(
            label,
            channel_id,
            deps=deps,
        )

    async def process_history_poll_message(
        self,
        owner: HistoryPollOwner,
        message: discord_diagnostics_history.DiscordHistoryMessage,
        channel_id: int,
    ) -> None:
        if not discord_history_poll.should_process_history_poll_message(message):
            return
        self.deps.log(
            discord_history_poll.format_history_poll_message_log(
                cast(discord_history_poll.HistoryPollMessage, message),
                channel_id,
                format_log_text_len=self.deps.format_log_text_len,
            )
        )
        await owner.process_discord_message(message, source="history_poll")


def _history_poll_task(owner: HistoryPollOwner) -> asyncio.Task[ModuleValue] | None:
    value = getattr(owner, "_history_poll_task", None)
    if isinstance(value, asyncio.Task):
        return value
    return None


def _history_poll_primed_channels(owner: HistoryPollOwner) -> set[int]:
    value = getattr(owner, "_history_poll_primed_channels", None)
    if isinstance(value, set):
        return cast(set[int], value)
    channels: set[int] = set()
    setattr(owner, "_history_poll_primed_channels", channels)
    return channels


def _is_history_bootstrap_user_message(
    owner: HistoryPollOwner,
    message: discord_diagnostics_history.DiscordHistoryMessage,
) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    cutoff = getattr(owner, "_history_poll_bootstrap_after", None)
    created_at = getattr(message, "created_at", None)
    if not isinstance(cutoff, datetime) or not isinstance(created_at, datetime):
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return created_at >= cutoff
