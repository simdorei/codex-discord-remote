from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Coroutine, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import ClassVar
import unittest
from unittest import mock

import codex_discord_bot_session_mirror_adapter_runtime as session_mirror_adapter_runtime
import codex_discord_bot_session_mirror_factory as session_mirror_factory
import codex_discord_bot_session_mirror_runtime as session_mirror_runtime
import codex_discord_bot_session_runner_wiring_runtime as runner_wiring
import codex_discord_session_mirror_item_delivery as item_delivery
import codex_discord_session_mirror_target as session_mirror_target
from codex_session_events import JsonEvent
from codex_thread_models import ThreadContextUsage, ThreadInfo


class FakeChannel:
    pass


@dataclass(frozen=True, slots=True)
class FakeMirrorRuntime:
    configured_channel_lock: asyncio.Lock


@dataclass(frozen=True, slots=True)
class FakeAdapterRuntime:
    module: ModuleType
    configured_channel_lock: asyncio.Lock

    def make_session_mirror_runtime(self) -> FakeMirrorRuntime:
        return FakeMirrorRuntime(self.configured_channel_lock)


class FakeBotModule(ModuleType):
    CONFIGURED_CHANNEL_LOCK: ClassVar[asyncio.Lock]
    SESSION_MIRROR_ADAPTER_RUNTIME: ClassVar[FakeAdapterRuntime]
    SESSION_MIRROR_RUNTIME: ClassVar[FakeMirrorRuntime]


class FakeEventsBridge:
    def choose_thread(self, thread_id: str | None, cwd: str | None) -> ThreadInfo:
        _ = (thread_id, cwd)
        return ThreadInfo("thread", "title", ".", 1, "rollout.jsonl", "gpt", "", 0)

    def read_new_session_events(
        self,
        session_path: Path,
        cursor: int,
        *,
        max_events: int | None = None,
    ) -> tuple[list[JsonEvent], int]:
        _ = (session_path, max_events)
        return [], cursor

    def get_thread_context_usage(self, thread: ThreadInfo) -> ThreadContextUsage:
        _ = thread
        return ThreadContextUsage(0, 0, 0, 0, 0, 0.0)

    def should_recommend_archive(
        self, thread: ThreadInfo, usage: ThreadContextUsage
    ) -> bool:
        _ = (thread, usage)
        return False

    def is_thread_busy(self, session_path: Path) -> bool:
        _ = session_path
        return True


async def _load_targets(
    db_path: Path,
    limit: int,
) -> Sequence[session_mirror_runtime.SessionMirrorTargetMapping]:
    _ = (db_path, limit)
    return ()


def _create_task(coro: Coroutine[None, None, None]) -> asyncio.Task[None]:
    return asyncio.create_task(coro)


async def _sleep(seconds: float) -> None:
    _ = seconds


async def _send_interactive(
    channel: FakeChannel,
    target_thread_id: str,
    target_ref: str,
    state: str,
    prompt: str,
    options: item_delivery.InteractiveOptions,
) -> None:
    _ = (channel, target_thread_id, target_ref, state, prompt, options)


async def _send_chunks(channel: FakeChannel, content: str, *, context: str) -> None:
    _ = (channel, content, context)


async def _send_attachment(
    channel: FakeChannel,
    content: str,
    attachment_url: str,
    filename: str,
    *,
    context: str,
) -> None:
    _ = (channel, content, attachment_url, filename, context)


def _collect_items(
    codex_thread_id: str,
    events: list[JsonEvent],
    *,
    seen_agent_messages: dict[str, float],
    seen_user_messages: dict[str, float],
) -> list[session_mirror_target.SessionMirrorItem]:
    _ = (codex_thread_id, events, seen_agent_messages, seen_user_messages)
    return []


def _update_cursor(codex_thread_id: str, rollout_path: str, cursor: int) -> None:
    _ = (codex_thread_id, rollout_path, cursor)


def _has_event(digest: str, codex_thread_id: str) -> bool:
    _ = (digest, codex_thread_id)
    return False


def _claim_event(digest: str, codex_thread_id: str) -> bool:
    _ = (digest, codex_thread_id)
    return True


def make_test_runtime(
    configured_channel_lock: asyncio.Lock,
    db_path: Path,
) -> session_mirror_runtime.SessionMirrorRuntime[FakeChannel]:
    return session_mirror_factory.make_session_mirror_runtime(
        configured_channel_lock=configured_channel_lock,
        target_limit=10,
        archive_backlog_max_events_default=10,
        delivery_exceptions=(OSError,),
        fetch_failure_types=(OSError,),
        get_db_path=lambda: db_path,
        load_targets_in_thread=_load_targets,
        create_task=_create_task,
        sleep=_sleep,
        is_messageable=lambda _channel: True,
        parse_interactive_notice=lambda _text: ("", "", []),
        send_interactive_prompt=_send_interactive,
        send_chunks=_send_chunks,
        send_attachment=_send_attachment,
        collect_session_mirror_items=_collect_items,
        get_archive_skip_logged=lambda _owner: set(),
        resolve_target_ref=lambda target: (target, target),
        is_active_output_target=lambda _target: True,
        is_pending_cursor_target=lambda _target: False,
        clear_pending_cursor_target=lambda _target: None,
        update_session_mirror_cursor=_update_cursor,
        get_or_init_session_mirror_cursor=lambda _target, _path, cursor: cursor,
        has_session_mirror_event=_has_event,
        claim_session_mirror_event=_claim_event,
        deactivate_session_mirror_output_target=lambda _target: None,
        events_bridge=FakeEventsBridge(),
        log=lambda _message: None,
    )


class BotSessionMirrorFactoryTests(unittest.TestCase):
    def test_wiring_exports_one_lock_through_adapter_and_runtime(self) -> None:
        module = FakeBotModule("fake_session_mirror_bot")
        wiring = runner_wiring.BotSessionRunnerWiringRuntime(module=module)

        skipped_installers = (
            "_install_bridge_target_exports",
            "_install_runner_runtime",
            "_install_context_exhaustion_helpers",
            "_install_session_mirror_delegation",
            "_install_prefix_command_runtime",
        )
        with ExitStack() as stack:
            for installer_name in skipped_installers:
                _ = stack.enter_context(
                    mock.patch.object(
                        runner_wiring.BotSessionRunnerWiringRuntime,
                        installer_name,
                    )
                )
            _ = stack.enter_context(
                mock.patch.object(
                    session_mirror_adapter_runtime,
                    "BotSessionMirrorAdapterRuntime",
                    FakeAdapterRuntime,
                )
            )
            wiring.install()

        exported_lock = module.CONFIGURED_CHANNEL_LOCK
        self.assertIs(
            module.SESSION_MIRROR_ADAPTER_RUNTIME.configured_channel_lock,
            exported_lock,
        )
        self.assertIs(
            module.SESSION_MIRROR_RUNTIME.configured_channel_lock,
            exported_lock,
        )

    def test_factory_preserves_configured_channel_lock_identity(self) -> None:
        configured_channel_lock = asyncio.Lock()

        runtime = make_test_runtime(configured_channel_lock, Path("factory.sqlite"))

        self.assertIs(runtime.deps.configured_channel_lock, configured_channel_lock)
        self.assertIs(
            runtime.deps.active_delivery_lease_deps.configured_channel_lock,
            configured_channel_lock,
        )


if __name__ == "__main__":
    _ = unittest.main()
