from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast, TypeAlias

import anyio
from anyio.to_thread import run_sync

import codex_app_server_transport as app_server_transport
import codex_discord_bot_lifecycle_runtime as discord_bot_lifecycle_runtime
import codex_discord_bot_message_runtime as discord_bot_message_runtime
import codex_discord_bot_session_mirror_runtime as discord_bot_session_mirror_runtime
import codex_discord_gpt_runtime as discord_gpt_runtime
import codex_discord_gpt_discord_adapter as discord_gpt_discord_adapter
import codex_discord_id_values as discord_id_values
import codex_discord_interaction_gate as discord_interaction_gate
import codex_discord_interaction_gate_runtime as discord_interaction_gate_runtime
import codex_discord_mirror_access as discord_mirror_access
import codex_discord_project_runtime as discord_project_runtime
import codex_discord_store_startup_probe as discord_store_startup_probe
import codex_discord_interaction_log as discord_interaction_log
import codex_discord_ready_cleanup as discord_ready_cleanup
import codex_discord_startup_notify as discord_startup_notify

ModuleValue: TypeAlias = object


class DiscordObjectFactory(Protocol):
    def __call__(self, *, id: int) -> ModuleValue: ...


class DiscordAbcModule(Protocol):
    Messageable: type[ModuleValue]


class DiscordModule(Protocol):
    Object: DiscordObjectFactory
    abc: DiscordAbcModule


class ReconciledMirrorSyncRuntime(Protocol):
    def sync_reconciled_codex_mirror(
        self, bot: discord_mirror_access.MirrorAccessBot, *, limit: int | None = None
    ) -> Awaitable[str]: ...


@dataclass(frozen=True, slots=True)
class BotLifecycleAdapterRuntime:
    module: ModuleType

    def make_lifecycle_runtime(
        self,
    ) -> discord_bot_lifecycle_runtime.BotLifecycleRuntime[ModuleValue]:
        runtime = discord_gpt_runtime.install_gpt_runtime(self.module)

        def startup_targets(
            allowed: set[int], startup: int | None, *, limit: int = 50
        ) -> list[tuple[str, int]]:
            result = discord_store_startup_probe.get_reconciled_startup_probe_targets(
                runtime.reconciliation,
                cast(Path, getattr(self.module, "MIRROR_DB_PATH")),
                allowed,
                startup,
                limit=limit,
            )
            return list(result.targets)

        setattr(self.module, "get_startup_probe_targets", startup_targets)
        self.prepare_gpt_runtime()
        return discord_bot_lifecycle_runtime.BotLifecycleRuntime(
            discord_bot_lifecycle_runtime.BotLifecycleRuntimeDeps(
                app_server_transport_enabled=self.app_server_transport_enabled,
                start_app_server_transport=self.start_app_server_transport,
                run_in_thread=lambda func: run_sync(func),
                register_commands=self.register_commands,
                make_guild_object=self.make_guild_object,
                wait_for_slash_sync=self.wait_for_slash_sync,
                run_ready_maintenance=self.run_ready_maintenance,
                send_startup_notice=self.send_startup_notice,
                format_user_id=discord_interaction_log.format_discord_user_id_for_log,
                delivery_exceptions=cast(
                    tuple[type[BaseException], ...],
                    getattr(self.module, "DISCORD_DELIVERY_EXCEPTIONS"),
                ),
                log=cast(Callable[[str], None], self._module_func("log_line")),
                prepare_gpt_runtime=self.prepare_gpt_runtime,
                reconcile_gpt_runtime=self.reconcile_gpt_runtime,
            )
        )

    def prepare_gpt_runtime(self) -> None:
        runtime = self._gpt_runtime()
        runtime.bind_configured_channel_lock(
            cast(
                discord_gpt_runtime.ConfiguredChannelLock,
                getattr(self.module, "CONFIGURED_CHANNEL_LOCK"),
            )
        )
        current = cast(object, getattr(self.module, "MESSAGE_RUNTIME"))
        if not isinstance(current, discord_bot_lifecycle_runtime.GptMessageRuntime):
            ordinary = cast(discord_bot_message_runtime.BotMessageRuntime, current)

            def resolve_thread_id(channel_id: int | None) -> str | None:
                return runtime.resolve_routable_thread_id(
                    ordinary.deps.get_mirrored_codex_thread_id, channel_id
                )

            routed = discord_bot_message_runtime.BotMessageRuntime(
                replace(
                    ordinary.deps,
                    get_mirrored_codex_thread_id=resolve_thread_id,
                )
            )
            setattr(
                self.module,
                "MESSAGE_RUNTIME",
                discord_bot_lifecycle_runtime.GptMessageRuntime(
                    runtime,
                    routed,
                    cast(Callable[[str], None], self._module_func("log_line")),
                ),
            )
        session = cast(object, getattr(self.module, "SESSION_MIRROR_RUNTIME"))
        if not isinstance(
            session, discord_bot_lifecycle_runtime.GptSessionMirrorRuntime
        ):
            setattr(
                self.module,
                "SESSION_MIRROR_RUNTIME",
                discord_bot_lifecycle_runtime.GptSessionMirrorRuntime(
                    runtime,
                    cast(
                        discord_bot_session_mirror_runtime.SessionMirrorRuntime[
                            ModuleValue
                        ],
                        session,
                    ),
                    cast(Callable[[str], None], self._module_func("log_line")),
                ),
            )
        setattr(
            self.module, "check_interaction_allowed", self.check_interaction_allowed
        )
        command_wiring = cast(
            ReconciledMirrorSyncRuntime, getattr(self.module, "COMMAND_WIRING_RUNTIME")
        )
        setattr(
            self.module,
            "sync_codex_mirror",
            command_wiring.sync_reconciled_codex_mirror,
        )

    async def reconcile_gpt_runtime(
        self, bot: discord_bot_lifecycle_runtime.ReadyBot
    ) -> None:
        await self._gpt_runtime().reconcile(
            cast(discord_gpt_discord_adapter.DiscordClient, cast(object, bot))
        )

    def check_interaction_allowed(
        self,
        bot: discord_interaction_gate.InteractionGateBot,
        interaction: discord_interaction_gate.InteractionLike,
    ) -> bool:
        runtime = self._gpt_runtime()

        def resolve(
            channel_id: discord_interaction_gate.DiscordIdValue,
            channel_name: str | None,
        ) -> discord_project_runtime.ExactChannelDecision:
            return runtime.resolve_exact_channel_decision(
                discord_id_values.coerce_discord_id_value(channel_id), channel_name
            )

        return discord_interaction_gate.check_interaction_allowed(
            discord_interaction_gate_runtime.InteractionGateBotAdapter(
                cast(
                    discord_interaction_gate_runtime.RuntimeInteractionGateBot,
                    cast(object, bot),
                )
            ),
            interaction,
            log_func=cast(Callable[[str], None], self._module_func("log_line")),
            get_interaction_command_name_func=cast(
                discord_interaction_gate.CommandNameFunc,
                self._module_func("get_interaction_gate_command_name"),
            ),
            is_mirrored_channel_id_func=cast(
                discord_interaction_gate.MirroredChannelFunc,
                self._module_func("is_mirrored_interaction_channel_id"),
            ),
            resolve_exact_channel_decision_func=resolve,
        )

    def _gpt_runtime(self) -> discord_gpt_runtime.GptRuntime:
        return cast(discord_gpt_runtime.GptRuntime, getattr(self.module, "GPT_RUNTIME"))

    def app_server_transport_enabled(self) -> bool:
        return cast(
            Callable[[], bool], self._module_func("app_server_transport_enabled")
        )()

    def start_app_server_transport(self) -> None:
        app_server_transport.DEFAULT_CLIENT.start()

    def register_commands(
        self, bot: discord_bot_lifecycle_runtime.SetupHookBot[ModuleValue]
    ) -> None:
        cast(
            Callable[[discord_bot_lifecycle_runtime.SetupHookBot[object]], None],
            self._module_func("register_commands"),
        )(bot)

    def make_guild_object(self, guild_id: int) -> ModuleValue:
        discord_module = cast(DiscordModule, getattr(self.module, "discord"))
        return discord_module.Object(id=guild_id)

    async def wait_for_slash_sync(
        self,
        awaitable: Awaitable[Sequence[discord_bot_lifecycle_runtime.SlashCommand]],
        timeout: float,
    ) -> Sequence[discord_bot_lifecycle_runtime.SlashCommand]:
        with anyio.fail_after(timeout):
            return await awaitable

    async def run_ready_maintenance(
        self, bot: discord_bot_lifecycle_runtime.ReadyBot
    ) -> None:
        await discord_ready_cleanup.run_ready_maintenance(
            cast(
                Callable[
                    [discord_bot_lifecycle_runtime.ReadyBot],
                    discord_ready_cleanup.ReadyMaintenanceDeps,
                ],
                self._module_func("_make_ready_maintenance_deps"),
            )(bot)
        )

    async def send_startup_notice(
        self, bot: discord_bot_lifecycle_runtime.ReadyBot
    ) -> None:
        runtime_config = cast(object, getattr(self.module, "discord_runtime_config"))
        await discord_startup_notify.send_startup_notice_if_enabled(
            cast(discord_startup_notify.StartupNotifyClient[object], cast(object, bot)),
            bot.startup_channel_id,
            notify_enabled=cast(
                Callable[[], bool],
                getattr(runtime_config, "discord_startup_notify_enabled"),
            ),
            is_messageable=self.is_messageable,
            send_chunks=self.send_chunks,
            build_startup_notice=cast(
                Callable[[], str], self._module_func("build_startup_notice")
            ),
            log=cast(Callable[[str], None], self._module_func("log_line")),
            delivery_exceptions=cast(
                tuple[type[BaseException], ...],
                getattr(self.module, "DISCORD_DELIVERY_EXCEPTIONS"),
            ),
        )

    def is_messageable(self, channel: ModuleValue) -> bool:
        discord_module = cast(DiscordModule, getattr(self.module, "discord"))
        return isinstance(channel, discord_module.abc.Messageable)

    async def send_chunks(
        self, channel: ModuleValue, text: str, *, context: str
    ) -> int:
        return await cast(
            discord_startup_notify.StartupNoticeSender[object],
            self._module_func("send_chunks"),
        )(channel, text, context=context)

    def _module_func(self, name: str) -> ModuleValue:
        return cast(object, getattr(self.module, name))
