from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Protocol, cast, TypeAlias

import codex_discord_help as discord_help
import codex_discord_slash_commands as discord_slash_commands
import codex_discord_slash_registration as discord_slash_registration
import codex_discord_slash_runtime_commands as discord_slash_runtime_commands
ModuleValue: TypeAlias = object


class RuntimeConfigModule(Protocol):
    def discord_qa_commands_enabled(self) -> bool: ...

    def discord_host_commands_enabled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class BotSlashRegistrationAdapterRuntime:
    module: ModuleType

    def build_help(self) -> str:
        runtime_config = cast(RuntimeConfigModule, getattr(self.module, "discord_runtime_config"))
        return discord_help.build_help(
            qa_commands_enabled=runtime_config.discord_qa_commands_enabled(),
            host_commands_enabled=runtime_config.discord_host_commands_enabled(),
        )

    def register_commands(self, bot: discord_slash_commands.SlashCommandBot) -> None:
        discord_slash_registration.register_commands(
            bot,
            discord_slash_registration.SlashRegistrationDeps(
                check_interaction_allowed=cast(
                    discord_slash_registration.InteractionAllowedChecker[
                        discord_slash_commands.SlashCommandBot,
                        object,
                    ],
                    self._module_func("check_interaction_allowed"),
                ),
                require_discord_interaction=cast(
                    discord_slash_registration.DiscordInteractionResolver[object],
                    self._module_func("require_discord_interaction"),
                ),
                send_interaction_not_allowed=cast(
                    Callable[[object], Awaitable[None]],
                    self._module_func("send_interaction_not_allowed"),
                ),
                send_interaction_chunks=cast(
                    discord_slash_registration.DiscordInteractionChunksSender[object],
                    self._module_func("send_interaction_chunks"),
                ),
                run_interaction_bridge_and_send=cast(
                    discord_slash_registration.DiscordInteractionBridgeRunner[object],
                    self._module_func("run_interaction_bridge_and_send"),
                ),
                send_interaction_response_tracked=cast(
                    discord_slash_registration.DiscordInteractionResponseSender[object],
                    self._module_func("send_interaction_response_tracked"),
                ),
                build_help=self.build_help,
                build_where_message=cast(Callable[[int | None], str], self._module_func("build_where_message")),
                build_context_message=cast(
                    discord_slash_commands.ContextMessageBuilder,
                    self._module_func("build_context_message"),
                ),
                build_context_refresh_message=cast(
                    discord_slash_commands.ContextRefreshMessageBuilder,
                    self._module_func("build_context_refresh_message"),
                ),
                build_weekly_usage_message=cast(
                    discord_slash_commands.WeeklyUsageMessageBuilder,
                    self._module_func("build_weekly_usage_message"),
                ),
                clamp_context_refresh_limit=cast(Callable[[int], int], self._module_func("clamp_context_refresh_limit")),
                resolve_discord_thread_target_args=cast(
                    Callable[[int | None, str | None], list[str]],
                    self._module_func("resolve_discord_thread_target_args"),
                ),
                build_mirror_check=cast(Callable[[], str], self._module_func("build_mirror_check")),
                build_runtime_discord_doctor_message=cast(
                    discord_slash_runtime_commands.RuntimeDoctorMessageBuilder,
                    self._module_func("build_runtime_discord_doctor_message"),
                ),
                build_runners_message=cast(Callable[[], Awaitable[str]], self._module_func("build_runners_message")),
                retract_queued_ask_for_request=cast(
                    discord_slash_runtime_commands.RuntimeQueueRetractor,
                    self._module_func("retract_queued_ask_for_request"),
                ),
                refresh_runtime_discord_bridge_session=cast(
                    discord_slash_runtime_commands.RuntimeBridgeSessionRefresher,
                    self._module_func("refresh_runtime_discord_bridge_session"),
                ),
                discord_qa_commands_enabled=self.discord_qa_commands_enabled,
                run_runtime_discord_button_qa=cast(
                    discord_slash_runtime_commands.RuntimeButtonQaRunner,
                    self._module_func("run_runtime_discord_button_qa"),
                ),
                handle_slash_new=cast(
                    discord_slash_registration.SlashNewHandler[
                        discord_slash_commands.SlashCommandBot,
                        object,
                    ],
                    self._module_func("handle_slash_new"),
                ),
                handle_slash_ask=cast(
                    discord_slash_registration.SlashPromptHandler[object],
                    self._module_func("handle_slash_ask"),
                ),
                handle_slash_interview=cast(
                    discord_slash_registration.SlashPromptHandler[object],
                    self._module_func("handle_slash_interview"),
                ),
                log_line=cast(Callable[[str], None], self._module_func("log_line")),
            ),
        )

    def discord_qa_commands_enabled(self) -> bool:
        runtime_config = cast(RuntimeConfigModule, getattr(self.module, "discord_runtime_config"))
        return runtime_config.discord_qa_commands_enabled()

    def _module_func(self, name: str) -> ModuleValue:
        return cast(object, getattr(self.module, name))
