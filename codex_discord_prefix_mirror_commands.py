from __future__ import annotations

import inspect
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import codex_discord_commands as discord_commands

BRIDGE_SYNC_COMMANDS = {"bridge_sync", "resync", "sync", "bridge"}
MIRROR_COMMAND = "mirror"


class ChannelLike(Protocol):
    @property
    def id(self) -> int:
        ...


class MessageLike(Protocol):
    @property
    def channel(self) -> ChannelLike:
        ...


class MirrorCommandBot(Protocol):
    pass


class SendChunksResult(Protocol):
    pass


class SendChunksFunc(Protocol):
    def __call__(self, target: ChannelLike, text: str, *, context: str = "send_chunks") -> Awaitable[SendChunksResult]:
        ...


class RefreshBridgeFunc(Protocol):
    def __call__(self, bot: MirrorCommandBot, *, limit: int | None = None) -> Awaitable[str]:
        ...


class SyncMirrorFunc(Protocol):
    def __call__(self, bot: MirrorCommandBot, *, limit: int | None = None) -> Awaitable[str]:
        ...


class BuildMirrorFunc(Protocol):
    def __call__(
        self,
        bot: MirrorCommandBot,
        limit: int | None = None,
        *,
        channel_id: int | None = None,
    ) -> str | Awaitable[str]:
        ...


@dataclass(frozen=True, slots=True)
class PrefixMirrorCommandDeps:
    send_chunks: SendChunksFunc
    refresh_discord_bridge_session: RefreshBridgeFunc
    sync_codex_mirror: SyncMirrorFunc
    build_mirror_list: BuildMirrorFunc
    build_mirror_check: BuildMirrorFunc
    log_line: Callable[[str], None]


async def handle_prefix_mirror_command(
    command: str,
    arg: str,
    message: MessageLike,
    bot: MirrorCommandBot,
    *,
    deps: PrefixMirrorCommandDeps,
) -> bool:
    if command in BRIDGE_SYNC_COMMANDS:
        return await _handle_bridge_sync(command, arg, message, bot, deps=deps)
    if command == MIRROR_COMMAND:
        return await _handle_mirror(arg, message, bot, deps=deps)
    return False


async def _handle_bridge_sync(
    command: str,
    arg: str,
    message: MessageLike,
    bot: MirrorCommandBot,
    *,
    deps: PrefixMirrorCommandDeps,
) -> bool:
    bridge_sync_action = discord_commands.parse_bridge_sync_limit(command, arg)
    if bridge_sync_action.usage:
        _ = await deps.send_chunks(message.channel, bridge_sync_action.usage, context="prefix_bridge_sync_usage")
        return True
    _ = await deps.send_chunks(message.channel, "Discord bridge sync started.", context="prefix_bridge_sync_start")
    try:
        output = await deps.refresh_discord_bridge_session(bot, limit=bridge_sync_action.limit)
        _ = await deps.send_chunks(message.channel, output)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK
        deps.log_line("bridge_sync_failed\n" + traceback.format_exc())
        _ = await deps.send_chunks(message.channel, f"Discord bridge sync failed\n\nERROR: {exc}")
    return True


async def _handle_mirror(
    arg: str,
    message: MessageLike,
    bot: MirrorCommandBot,
    *,
    deps: PrefixMirrorCommandDeps,
) -> bool:
    mirror_action = discord_commands.parse_mirror_action(arg)
    if mirror_action.usage:
        _ = await deps.send_chunks(message.channel, mirror_action.usage, context="prefix_mirror_usage")
        return True
    if mirror_action.subcommand == "sync":
        return await _handle_mirror_sync(message, bot, deps=deps)
    if mirror_action.subcommand == "list":
        return await _handle_mirror_build(
            message,
            bot,
            mirror_action.limit,
            builder=deps.build_mirror_list,
            log_context="mirror_list_failed",
            failure_title="Mirror list failed",
            deps=deps,
        )
    if mirror_action.subcommand == "check":
        return await _handle_mirror_build(
            message,
            bot,
            mirror_action.limit,
            builder=deps.build_mirror_check,
            log_context="mirror_check_failed",
            failure_title="Mirror check failed",
            deps=deps,
        )
    return True


async def _handle_mirror_sync(
    message: MessageLike,
    bot: MirrorCommandBot,
    *,
    deps: PrefixMirrorCommandDeps,
) -> bool:
    _ = await deps.send_chunks(message.channel, "Mirror sync started.", context="prefix_mirror_sync_start")
    try:
        output = await deps.sync_codex_mirror(bot, limit=None)
        _ = await deps.send_chunks(message.channel, output)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK
        deps.log_line("mirror_sync_failed\n" + traceback.format_exc())
        _ = await deps.send_chunks(message.channel, f"Mirror sync failed\n\nERROR: {exc}")
    return True


async def _handle_mirror_build(
    message: MessageLike,
    bot: MirrorCommandBot,
    limit: int | None,
    *,
    builder: BuildMirrorFunc,
    log_context: str,
    failure_title: str,
    deps: PrefixMirrorCommandDeps,
) -> bool:
    try:
        result = builder(bot, limit, channel_id=None)
        output = await result if inspect.isawaitable(result) else str(result)
        _ = await deps.send_chunks(message.channel, output)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK
        deps.log_line(log_context + "\n" + traceback.format_exc())
        _ = await deps.send_chunks(message.channel, f"{failure_title}\n\nERROR: {exc}")
    return True
