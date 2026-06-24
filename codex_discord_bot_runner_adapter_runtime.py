from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import cast, TypeAlias

import codex_discord_queue_targets as discord_queue_targets
import codex_discord_runner as discord_runner
import codex_discord_runner_runtime as discord_runner_runtime
import codex_discord_runtime as discord_runtime
from codex_discord_runner_queue import RunnerMap
ModuleValue: TypeAlias = object


@dataclass(frozen=True, slots=True)
class BotRunnerAdapterRuntime:
    module: ModuleType

    def make_runner_runtime(self) -> discord_runner_runtime.RunnerRuntime:
        return discord_runner_runtime.RunnerRuntime(
            discord_runner_runtime.RunnerRuntimeDeps(
                thread_runners=cast(RunnerMap, getattr(self.module, "THREAD_RUNNERS")),
                thread_runners_lock=cast(asyncio.Lock, getattr(self.module, "THREAD_RUNNERS_LOCK")),
                runner_snapshot_lock=cast(discord_runtime.RunnerLockLike, getattr(self.module, "RUNNER_SNAPSHOT_LOCK")),
                snapshot_thread_runners=lambda: cast(
                    discord_runner_runtime.SnapshotThreadRunnersFunc,
                    self._module_func("snapshot_thread_runners"),
                )(),
                get_runtime_state=lambda: cast(
                    Callable[[], discord_runtime.DiscordRuntimeState],
                    self._module_func("get_runtime_state"),
                )(),
                get_busy_state_for_thread=lambda target_thread_id: cast(
                    discord_runner.GetBusyStateFunc,
                    self._module_func("get_busy_state_for_thread"),
                )(target_thread_id),
                resolve_target_ref=lambda target_thread_id: cast(
                    discord_runtime.ResolveTargetRefFunc,
                    self._module_func("resolve_target_ref"),
                )(target_thread_id),
                get_queue_target_bridge=lambda: cast(
                    Callable[[], discord_queue_targets.QueueTargetBridge],
                    self._module_func("get_queue_target_bridge_module"),
                )(),
                get_mirrored_codex_thread_id=lambda channel_id: cast(
                    discord_queue_targets.GetMirroredCodexThreadIdFunc,
                    self._module_func("get_mirrored_codex_thread_id"),
                )(channel_id),
                format_target_ref_for_log=lambda target_ref: cast(
                    Callable[..., str],
                    self._module_func("format_discord_command_label"),
                )(target_ref, limit=80),
                run_prompt_and_send=self.run_prompt_and_send,
                send_chunks=self.send_chunks,
                log=lambda message: cast(Callable[[str], None], self._module_func("log_line"))(message),
            )
        )

    async def run_prompt_and_send(self, channel: ModuleValue, prompt: str, **kwargs: ModuleValue) -> None:
        await cast(discord_runner.RunPromptAndSendFunc, self._module_func("run_prompt_and_send"))(
            channel,
            prompt,
            **kwargs,
        )

    async def send_chunks(self, target: ModuleValue, text: str, **kwargs: ModuleValue) -> int:
        return await cast(discord_runner.SendTextFunc, self._module_func("send_chunks"))(
            target,
            text,
            **kwargs,
        )

    def _module_func(self, name: str) -> ModuleValue:
        return cast(object, getattr(self.module, name))
