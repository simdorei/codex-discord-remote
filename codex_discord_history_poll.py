from __future__ import annotations

from asyncio import CancelledError  # noqa: ANYIO_OK
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, cast

import codex_discord_diagnostics_history as discord_diagnostics_history


MessageT = TypeVar("MessageT")
HistoryMessageT_co = TypeVar("HistoryMessageT_co", covariant=True)
GetCachedHistoryChannel = Callable[[int], tuple[object | None, str]]
FetchHistoryChannel = Callable[[int], Awaitable[object | None]]
ClaimHistoryMessage = Callable[[MessageT], bool]
HistoryBootstrapPredicate = Callable[[MessageT], bool]
ProcessHistoryPollMessage = Callable[[MessageT, int], Awaitable[None]]
LogFunc = Callable[[str], None]
FormatLogTextLenFunc = Callable[[str], int | str]
PollHistoryChannelRunner = Callable[[str, int], Awaitable[None]]
SleepFunc = Callable[[float], Awaitable[None]]
SetLastAtFunc = Callable[[str], None]
NowIsoFunc = Callable[[], str]
TracebackFormatter = Callable[[], str]


class HistoryPollChannel(Protocol):
    @property
    def id(self) -> int | str | None: ...


class HistoryPollMessage(discord_diagnostics_history.DiscordHistoryMessage, Protocol):
    @property
    def channel(self) -> HistoryPollChannel | None: ...


class HistorySource(Protocol[HistoryMessageT_co]):
    def history(self, *, limit: int) -> AsyncIterator[HistoryMessageT_co]: ...


class HistoryPollTargetsGetter(Protocol):
    def __call__(
        self,
        allowed_channel_ids: set[int],
        startup_channel_id: int | None,
        *,
        limit: int = 50,
    ) -> list[tuple[str, int]]: ...


@dataclass(frozen=True, slots=True)
class PollHistoryChannelDeps(Generic[MessageT]):
    get_cached_channel_or_thread: GetCachedHistoryChannel
    fetch_channel: FetchHistoryChannel
    delivery_exceptions: tuple[type[BaseException], ...]
    history_limit: int
    is_primed_channel: Callable[[int], bool]
    mark_primed_channel: Callable[[int], None]
    claim_message: ClaimHistoryMessage[MessageT]
    is_bootstrap_user_message: HistoryBootstrapPredicate[MessageT]
    process_history_poll_message: ProcessHistoryPollMessage[MessageT]
    log: LogFunc


@dataclass(frozen=True, slots=True)
class HistoryPollLoopDeps:
    allowed_channel_ids: set[int]
    startup_channel_id: int | None
    poll_seconds: float
    target_limit: int
    is_closed: Callable[[], bool]
    set_last_at: SetLastAtFunc
    now_iso: NowIsoFunc
    get_targets: HistoryPollTargetsGetter
    poll_history_channel: PollHistoryChannelRunner
    delivery_exceptions: tuple[type[BaseException], ...]
    format_traceback: TracebackFormatter
    sleep: SleepFunc
    log: LogFunc


def should_process_history_poll_message(message: discord_diagnostics_history.DiscordHistoryMessage) -> bool:
    author = message.author
    return author is None or not author.bot


async def poll_history_channel(
    label: str,
    channel_id: int,
    *,
    deps: PollHistoryChannelDeps[MessageT],
) -> None:
    channel, source = deps.get_cached_channel_or_thread(channel_id)
    if channel is None:
        try:
            channel = await deps.fetch_channel(channel_id)
            source = "fetch"
        except deps.delivery_exceptions as exc:
            deps.log(
                f"history_poll_channel_failed label={label} channel={channel_id} "
                + f"error_type={type(exc).__name__}"
            )
            return
    if not callable(getattr(channel, "history", None)):
        deps.log(f"history_poll_channel_skipped label={label} channel={channel_id} reason=no_history")
        return

    history_channel = cast(HistorySource[MessageT], channel)
    is_primed = deps.is_primed_channel(int(channel_id))
    claimed_messages: list[MessageT] = []
    try:
        async for message in history_channel.history(limit=deps.history_limit):
            if deps.claim_message(message):
                claimed_messages.append(message)
    except deps.delivery_exceptions as exc:
        deps.log(
            f"history_poll_channel_failed label={label} channel={channel_id} "
            + f"source={source} error_type={type(exc).__name__}"
        )
        return
    if not is_primed:
        deps.mark_primed_channel(int(channel_id))
        bootstrap_messages = [
            message
            for message in reversed(claimed_messages)
            if deps.is_bootstrap_user_message(message)
        ]
        deps.log(
            f"history_poll_primed label={label} channel={channel_id} "
            + f"source={source} messages={len(claimed_messages)} "
            + f"bootstrap_user_messages={len(bootstrap_messages)}"
        )
        for message in bootstrap_messages:
            await deps.process_history_poll_message(message, channel_id)
        return
    for message in reversed(claimed_messages):
        await deps.process_history_poll_message(message, channel_id)


async def history_poll_loop(deps: HistoryPollLoopDeps) -> None:
    while not deps.is_closed():
        try:
            deps.set_last_at(deps.now_iso())
            targets = deps.get_targets(
                deps.allowed_channel_ids,
                deps.startup_channel_id,
                limit=deps.target_limit,
            )
            for label, channel_id in targets:
                await deps.poll_history_channel(label, channel_id)
        except CancelledError:
            raise
        except deps.delivery_exceptions:
            deps.log("history_poll_cycle_failed\n" + deps.format_traceback())
        await deps.sleep(deps.poll_seconds)


def format_history_poll_message_log(
    message: HistoryPollMessage,
    channel_id: int,
    *,
    format_log_text_len: FormatLogTextLenFunc,
) -> str:
    channel = message.channel
    author = message.author
    resolved_channel_id = channel_id if channel is None or channel.id is None else channel.id
    author_id = "-" if author is None else author.id
    content = message.content or ""
    return (
        f"history_poll_message channel={resolved_channel_id} "
        f"user={author_id} content_len={format_log_text_len(content)}"
    )
