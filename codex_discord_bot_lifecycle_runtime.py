from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence, Sized
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Protocol, TypeVar, assert_never, cast

import codex_discord_bot_message_runtime as discord_bot_message_runtime
import codex_discord_bot_session_mirror_runtime as discord_bot_session_mirror_runtime
import codex_discord_gpt_runtime as discord_gpt_runtime
import codex_discord_interaction_log as discord_interaction_log
import codex_discord_project_runtime as discord_project_runtime
from codex_discord_message_dispatch import InboundMessage
from codex_discord_session_mirror import (
    SessionMirrorItem,
    SessionMirrorTargetMapping,
)


GuildObjectT = TypeVar("GuildObjectT")
GuildObjectContraT = TypeVar("GuildObjectContraT", contravariant=True)
ChannelT = TypeVar("ChannelT")


class SlashCommand(Protocol):
    @property
    def name(self) -> str: ...


class SlashCommandTree(Protocol[GuildObjectContraT]):
    def copy_global_to(self, *, guild: GuildObjectContraT) -> None: ...

    def sync(
        self, guild: GuildObjectContraT | None = None
    ) -> Awaitable[Sequence[SlashCommand]]: ...


class SetupHookBot(Protocol[GuildObjectContraT]):
    guild_id: int | None
    tree: SlashCommandTree[GuildObjectContraT]


class ReadyBot(Protocol):
    user: discord_interaction_log.DiscordUserLogValue
    guilds: Sized
    startup_channel_id: int | None

    def log_startup_diagnostics(self) -> Awaitable[None]: ...


class AsyncReadyHook(Protocol):
    def __call__(self) -> Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class GptMessageRuntime:
    runtime: discord_gpt_runtime.GptRuntime
    inner: discord_bot_message_runtime.BotMessageRuntime
    log: Callable[[str], None]

    async def process_discord_message(
        self,
        owner: discord_bot_message_runtime.MessageRuntimeOwner,
        message: InboundMessage,
        *,
        source: str,
    ) -> None:
        channel = message.channel
        decision = self.runtime.resolve_exact_channel_decision(
            int(channel.id), getattr(channel, "name", None)
        )
        match decision:
            case discord_project_runtime.ExactChannelActive():
                if not self.runtime.ready:
                    self.log(
                        "gpt_message_ignored reason=startup_reconciliation_incomplete"
                    )
                    return
            case discord_project_runtime.ExactChannelBlocked(reason=reason):
                self.log(f"gpt_message_ignored reason={reason}")
                return
            case discord_project_runtime.ExactChannelUnknown():
                pass
            case _:
                assert_never(decision)
        await self.inner.process_discord_message(owner, message, source=source)


@dataclass(frozen=True, slots=True)
class GptSessionMirrorRuntime(Generic[ChannelT]):
    runtime: discord_gpt_runtime.GptRuntime
    inner: discord_bot_session_mirror_runtime.SessionMirrorRuntime[ChannelT]
    log: Callable[[str], None]

    async def start_session_mirroring(
        self, owner: discord_bot_session_mirror_runtime.SessionMirrorOwner[ChannelT]
    ) -> None:
        await self.inner.start_session_mirroring(owner)

    async def session_mirror_loop(
        self, owner: discord_bot_session_mirror_runtime.SessionMirrorOwner[ChannelT]
    ) -> None:
        await self.inner.session_mirror_loop(owner)

    async def resolve_session_mirror_channel(
        self,
        owner: discord_bot_session_mirror_runtime.SessionMirrorOwner[ChannelT],
        discord_thread_id: int,
    ) -> ChannelT | None:
        return await self.inner.resolve_session_mirror_channel(owner, discord_thread_id)

    async def send_session_mirror_item(
        self,
        channel: ChannelT,
        item: SessionMirrorItem,
        *,
        target_thread_id: str,
        target_ref: str,
    ) -> None:
        await self.inner.send_session_mirror_item(
            channel,
            item,
            target_thread_id=target_thread_id,
            target_ref=target_ref,
        )

    async def mirror_session_target(
        self,
        owner: discord_bot_session_mirror_runtime.SessionMirrorOwner[ChannelT],
        target: SessionMirrorTargetMapping,
    ) -> None:
        parsed = self.inner.deps.parse_session_mirror_target(target)
        if parsed is None:
            return
        decision = self.runtime.resolve_exact_channel_decision(
            parsed.discord_thread_id, None
        )
        match decision:
            case discord_project_runtime.ExactChannelActive():
                if not self.runtime.ready:
                    self.log(
                        "gpt_session_delivery_ignored "
                        + "reason=startup_reconciliation_incomplete"
                    )
                    return
                await self.inner.mirror_session_target(owner, target)
            case discord_project_runtime.ExactChannelBlocked(reason=reason):
                self.log(f"gpt_session_delivery_ignored reason={reason}")
            case discord_project_runtime.ExactChannelUnknown():
                await self.inner.mirror_session_target(owner, target)
            case _:
                assert_never(decision)


@dataclass(frozen=True, slots=True)
class BotLifecycleRuntimeDeps(Generic[GuildObjectT]):
    app_server_transport_enabled: Callable[[], bool]
    start_app_server_transport: Callable[[], None]
    run_in_thread: Callable[[Callable[[], None]], Awaitable[None]]
    register_commands: Callable[[SetupHookBot[GuildObjectT]], None]
    make_guild_object: Callable[[int], GuildObjectT]
    wait_for_slash_sync: Callable[
        [Awaitable[Sequence[SlashCommand]], float],
        Awaitable[Sequence[SlashCommand]],
    ]
    run_ready_maintenance: Callable[[ReadyBot], Awaitable[None]]
    send_startup_notice: Callable[[ReadyBot], Awaitable[None]]
    format_user_id: Callable[[discord_interaction_log.DiscordUserLogValue], str]
    delivery_exceptions: tuple[type[BaseException], ...]
    log: Callable[[str], None]
    prepare_gpt_runtime: Callable[[], None]
    reconcile_gpt_runtime: Callable[[ReadyBot], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BotLifecycleRuntime(Generic[GuildObjectT]):
    deps: BotLifecycleRuntimeDeps[GuildObjectT]

    async def setup_hook(self, bot: SetupHookBot[GuildObjectT]) -> None:
        self.deps.log("setup_hook_start")
        self.deps.prepare_gpt_runtime()
        if self.deps.app_server_transport_enabled():
            try:
                await self.deps.run_in_thread(self.deps.start_app_server_transport)
                self.deps.log("setup_hook_app_server_transport_ready")
            except self.deps.delivery_exceptions as exc:
                self.deps.log(
                    f"setup_hook_app_server_transport_failed error={str(exc)[:300]}"
                )
        self.deps.register_commands(bot)
        try:
            if bot.guild_id:
                guild = self.deps.make_guild_object(bot.guild_id)
                bot.tree.copy_global_to(guild=guild)
                self.deps.log(f"setup_hook_sync_guild guild_id={bot.guild_id}")
                synced = await self.deps.wait_for_slash_sync(
                    bot.tree.sync(guild=guild), 20.0
                )
            else:
                self.deps.log("setup_hook_sync_global")
                synced = await self.deps.wait_for_slash_sync(bot.tree.sync(), 20.0)
            command_names = sorted(command.name for command in synced)
            self._set_slash_sync_state(
                bot, status="ok", commands=",".join(command_names) or "-"
            )
            self.deps.log(
                f"setup_hook_synced commands={','.join(command_names) or '-'}"
            )
        except self.deps.delivery_exceptions as exc:
            self._set_slash_sync_state(
                bot, status=f"skipped:{type(exc).__name__}", commands="-"
            )
            self.deps.log(f"setup_hook_sync_skipped error={exc}")
        self.deps.log("setup_hook_done")

    async def on_ready(self, bot: ReadyBot) -> None:
        self.deps.log(
            " ".join(
                (
                    f"ready user_id={self.deps.format_user_id(bot.user)}",
                    f"guilds={len(bot.guilds)}",
                )
            )
        )
        await self.deps.run_ready_maintenance(bot)
        await self._call_optional_ready_hook(bot, "start_stop_marker_watcher")
        await self.deps.reconcile_gpt_runtime(bot)
        await self._call_optional_ready_hook(bot, "start_history_polling")
        await self.deps.send_startup_notice(bot)
        await self._call_optional_ready_hook(bot, "start_session_mirroring")
        await bot.log_startup_diagnostics()

    async def _call_optional_ready_hook(self, bot: ReadyBot, name: str) -> None:
        hook = getattr(bot, name, None)
        if not callable(hook):
            return
        await cast(AsyncReadyHook, hook)()

    def _set_slash_sync_state(
        self,
        bot: SetupHookBot[GuildObjectT],
        *,
        status: str,
        commands: str,
    ) -> None:
        setattr(
            bot,
            "_slash_sync_last_at",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        setattr(bot, "_slash_sync_status", status)
        setattr(bot, "_slash_sync_commands", commands)
