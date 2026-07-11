from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from types import ModuleType
from typing import Protocol, TypeVar, runtime_checkable
import sqlite3
import time

import codex_discord_bridge_command_runtime as discord_bridge_command_runtime
import codex_discord_bot_prefix_command_runtime as discord_bot_prefix_command_runtime
import codex_discord_bot_runner_adapter_runtime as discord_bot_runner_adapter_runtime
import codex_discord_bot_session_mirror_adapter_runtime as discord_bot_session_mirror_adapter_runtime
import codex_discord_bot_session_mirror_delegation_runtime as discord_bot_session_mirror_delegation_runtime
import codex_discord_prefix_mirror_commands as discord_prefix_mirror_commands
import codex_discord_runtime_config as discord_runtime_config
import codex_discord_session_mirror_delegation as discord_session_mirror_delegation


ExportValueT = TypeVar("ExportValueT")


@runtime_checkable
class SessionRunnerWiringModule(Protocol):
    BRIDGE_COMMAND_RUNTIME: discord_bridge_command_runtime.BridgeCommandRuntime
    BRIDGE_MIRROR_STATUS: discord_session_mirror_delegation.MirrorStatusBridge
    BRIDGE_CONTEXT: discord_session_mirror_delegation.ContextFormatBridge
    INTERACTIVE_STATE_APPROVAL: str
    log_line: Callable[[str], None]
    send_chunks: discord_session_mirror_delegation.SendChunksFunc[
        discord_session_mirror_delegation.SessionMirrorOutputChannel, int
    ]


class SessionRunnerWiringContractError(RuntimeError):
    """The bot module is missing a required session-runner wiring member."""


@dataclass(frozen=True, slots=True)
class BotSessionRunnerWiringRuntime:
    module: ModuleType

    def install(self) -> None:
        self._install_bridge_target_exports()
        self._install_session_mirror_runtime()
        self._install_runner_runtime()
        self._install_context_exhaustion_helpers()
        self._install_session_mirror_delegation()
        self._install_prefix_command_runtime()

    def _install_bridge_target_exports(self) -> None:
        bridge = self._typed_module().BRIDGE_COMMAND_RUNTIME
        _ = self._set(
            "resolve_target_ref",
            bridge.resolve_target_ref,
        )
        _ = self._set(
            "get_interactive_state_for_thread",
            bridge.get_interactive_state_for_thread,
        )
        _ = self._set(
            "get_busy_state_for_thread",
            bridge.get_busy_state_for_thread,
        )

    def _install_session_mirror_runtime(self) -> None:
        configured_channel_lock = asyncio.Lock()
        _ = self._set("CONFIGURED_CHANNEL_LOCK", configured_channel_lock)
        adapter_runtime = (
            discord_bot_session_mirror_adapter_runtime.BotSessionMirrorAdapterRuntime(
                module=self.module,
                configured_channel_lock=configured_channel_lock,
            )
        )
        _ = self._set("SESSION_MIRROR_ADAPTER_RUNTIME", adapter_runtime)
        _ = self._set(
            "SESSION_MIRROR_RUNTIME", adapter_runtime.make_session_mirror_runtime()
        )

    def _install_runner_runtime(self) -> None:
        adapter_runtime = discord_bot_runner_adapter_runtime.BotRunnerAdapterRuntime(
            module=self.module
        )
        runner_runtime = adapter_runtime.make_runner_runtime()
        _ = self._set("RUNNER_ADAPTER_RUNTIME", adapter_runtime)
        _ = self._set("RUNNER_RUNTIME", runner_runtime)
        _ = self._set("build_runners_message", runner_runtime.build_runners_message)
        _ = self._set(
            "resolve_queue_command_target", runner_runtime.resolve_queue_command_target
        )
        _ = self._set(
            "retract_queued_ask_for_request",
            runner_runtime.retract_queued_ask_for_request,
        )
        _ = self._set("codex_app_turn_slot", runner_runtime.codex_app_turn_slot)
        _ = self._set("get_thread_runner", runner_runtime.get_thread_runner)
        _ = self._set(
            "wait_for_codex_thread_idle", runner_runtime.wait_for_codex_thread_idle
        )
        _ = self._set("enqueue_thread_ask", runner_runtime.enqueue_thread_ask)
        _ = self._set("retract_thread_ask", runner_runtime.retract_thread_ask)
        _ = self._set(
            "report_thread_runner_job_failed",
            runner_runtime.report_thread_runner_job_failed,
        )
        _ = self._set("thread_runner_loop", runner_runtime.thread_runner_loop)

    def _install_context_exhaustion_helpers(self) -> None:
        _ = self._set(
            "is_context_exhausted_no_reply_state",
            discord_session_mirror_delegation.is_context_exhausted_no_reply_state,
        )
        _ = self._set(
            "build_context_exhausted_prompt_message",
            partial(
                discord_session_mirror_delegation.build_context_exhausted_prompt_message,
                format_token_k_func=self._format_token_k,
            ),
        )
        _ = self._set(
            "send_context_exhausted_prompt_notice_if_needed",
            self._send_context_exhausted_prompt_notice_if_needed,
        )

    def _install_session_mirror_delegation(self) -> None:
        runtime = discord_bot_session_mirror_delegation_runtime.BotSessionMirrorDelegationRuntime(
            module=self.module
        )
        _ = self._set("SESSION_MIRROR_DELEGATION_RUNTIME", runtime)
        _ = self._set(
            "should_delegate_output_to_session_mirror",
            runtime.should_delegate_output_to_session_mirror,
        )
        _ = self._set(
            "should_delegate_session_mirror_output",
            runtime.should_delegate_session_mirror_output,
        )
        _ = self._set(
            "prepare_session_mirror_delegation",
            runtime.prepare_session_mirror_delegation,
        )
        _ = self._set(
            "prepare_mapped_session_mirror_output",
            runtime.prepare_mapped_session_mirror_output,
        )
        _ = self._set("build_prefix_mirror_list", runtime.build_prefix_mirror_list)
        _ = self._set("build_prefix_mirror_check", runtime.build_prefix_mirror_check)
        _ = self._set(
            "prepare_prefix_mapped_session_mirror_output",
            runtime.prepare_prefix_mapped_session_mirror_output,
        )
        _ = self._set(
            "prepare_prefix_session_mirror_delegation",
            runtime.prepare_prefix_session_mirror_delegation,
        )

    def _install_prefix_command_runtime(self) -> None:
        module = self._typed_module()
        host_reboot_allowed_user_ids_configured = bool(
            discord_runtime_config.get_discord_allowed_user_ids()
        )
        runtime: discord_bot_prefix_command_runtime.BotPrefixCommandRuntime[
            discord_prefix_mirror_commands.MirrorCommandBot
        ] = discord_bot_prefix_command_runtime.BotPrefixCommandRuntime(
            discord_bot_prefix_command_runtime.BotPrefixCommandRuntimeDeps(
                module=self.module,
                interactive_state_approval=module.INTERACTIVE_STATE_APPROVAL,
                qa_commands_enabled=discord_runtime_config.discord_qa_commands_enabled,
                host_commands_enabled=discord_runtime_config.discord_host_commands_enabled,
                host_reboot_allowed_user_ids_configured=lambda: (
                    host_reboot_allowed_user_ids_configured
                ),
                monotonic=time.monotonic,
            )
        )
        _ = self._set("PREFIX_COMMAND_RUNTIME", runtime)
        _ = self._set(
            "_make_prefix_command_deps_factory",
            runtime.make_prefix_command_deps_factory,
        )
        _ = self._set(
            "_make_prefix_prompt_command_deps", runtime.make_prefix_prompt_command_deps
        )
        _ = self._set(
            "_make_prefix_mirror_command_deps", runtime.make_prefix_mirror_command_deps
        )
        _ = self._set(
            "_make_prefix_steer_command_deps", runtime.make_prefix_steer_command_deps
        )
        _ = self._set(
            "_make_prefix_status_command_deps", runtime.make_prefix_status_command_deps
        )
        _ = self._set(
            "_make_prefix_queue_command_deps", runtime.make_prefix_queue_command_deps
        )
        _ = self._set(
            "_make_prefix_archive_command_deps",
            runtime.make_prefix_archive_command_deps,
        )
        _ = self._set(
            "_make_prefix_approval_command_deps",
            runtime.make_prefix_approval_command_deps,
        )
        _ = self._set(
            "_make_prefix_qa_command_deps", runtime.make_prefix_qa_command_deps
        )
        _ = self._set(
            "_make_prefix_new_command_deps", runtime.make_prefix_new_command_deps
        )
        _ = self._set(
            "_make_prefix_host_command_deps", runtime.make_prefix_host_command_deps
        )

    async def _send_chunks(
        self,
        target: discord_session_mirror_delegation.SessionMirrorOutputChannel,
        text: str,
        *,
        context: str,
    ) -> int:
        return await self._typed_module().send_chunks(target, text, context=context)

    async def _send_context_exhausted_prompt_notice_if_needed(
        self,
        channel: discord_session_mirror_delegation.SessionMirrorOutputChannel,
        target_thread_id: str | None,
        target_ref: str,
    ) -> bool:
        module = self._typed_module()
        return await discord_session_mirror_delegation.send_context_exhausted_prompt_notice_if_needed(
            channel,
            target_thread_id,
            target_ref,
            bridge_module=module.BRIDGE_MIRROR_STATUS,
            send_chunks_func=self._send_chunks,
            format_token_k_func=self._format_token_k,
            expected_exceptions=(OSError, RuntimeError, sqlite3.Error),
            log_func=module.log_line,
        )

    def _format_token_k(self, value: int) -> str:
        return self._typed_module().BRIDGE_CONTEXT.format_token_k(value)

    def _typed_module(self) -> SessionRunnerWiringModule:
        if isinstance(self.module, SessionRunnerWiringModule):
            return self.module
        raise SessionRunnerWiringContractError(
            "bot module does not satisfy the session runner wiring contract"
        )

    def _set(self, name: str, value: ExportValueT) -> ExportValueT:
        setattr(self.module, name, value)
        return value
