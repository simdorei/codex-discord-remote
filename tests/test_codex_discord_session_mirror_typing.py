from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

import codex_discord_bot_session_mirror_runtime as bot_session_mirror_runtime
import codex_discord_session_mirror as session_mirror
import codex_discord_session_mirror_target as session_mirror_target


class FakeThread:
    def __init__(self, rollout_path: str) -> None:
        self.rollout_path = rollout_path


class FakeChannel:
    id = 222


class SessionMirrorTypingTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_forwards_target_id_to_typing_pulse(self) -> None:
        typing_pulses: list[tuple[int, str, str]] = []
        runtime_deps = mock.Mock()
        runtime_deps.get_archive_skip_logged.return_value = set()

        async def send_typing_pulse(channel: FakeChannel, target_thread_id: str, context: str) -> None:
            typing_pulses.append((channel.id, target_thread_id, context))

        runtime_deps.send_typing_pulse = send_typing_pulse
        runtime = bot_session_mirror_runtime.SessionMirrorRuntime(
            cast(bot_session_mirror_runtime.SessionMirrorRuntimeDeps[FakeChannel], runtime_deps)
        )
        owner = mock.Mock()

        async def exercise_runtime_callback(target: object, *, deps: object) -> None:
            _ = target
            callback = cast(
                session_mirror_target.SessionMirrorTargetDeps[object, object, object, FakeChannel],
                deps,
            ).send_typing_pulse
            await callback(FakeChannel(), "thread-1", "session_mirror_busy")

        with mock.patch.object(
            bot_session_mirror_runtime.discord_session_mirror_target,
            "mirror_session_target",
            exercise_runtime_callback,
        ):
            await runtime.mirror_session_target(
                cast(bot_session_mirror_runtime.SessionMirrorOwner[FakeChannel], owner),
                {"codex_thread_id": "thread-1", "discord_thread_id": 222},
            )

        self.assertEqual(typing_pulses, [(222, "thread-1", "session_mirror_busy")])

    async def test_active_output_target_without_events_sends_typing_pulse(self) -> None:
        channels: list[int] = []
        typing_pulses: list[tuple[int, str, str]] = []
        logs: list[str] = []

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            session_path.write_text("", encoding="utf-8")

            async def resolve_channel(discord_thread_id: int) -> FakeChannel | None:
                channels.append(discord_thread_id)
                return FakeChannel()

            async def send_typing_pulse(channel: FakeChannel, target_thread_id: str, context: str) -> None:
                typing_pulses.append((channel.id, target_thread_id, context))

            await session_mirror_target.mirror_session_target(
                {"codex_thread_id": "thread-1", "discord_thread_id": 222},
                deps=session_mirror_target.SessionMirrorTargetDeps(
                    parse_session_mirror_target=session_mirror.parse_session_mirror_target,
                    choose_thread=lambda thread_id, cwd: FakeThread(str(session_path)),
                    get_thread_context_usage=lambda thread: object(),
                    should_recommend_archive=lambda thread, usage: False,
                    get_thread_rollout_path=lambda thread: thread.rollout_path,
                    is_active_output_target=lambda thread_id: thread_id == "thread-1",
                    archive_skip_logged=set(),
                    is_pending_cursor_target=lambda thread_id: False,
                    clear_pending_cursor_target=lambda thread_id: None,
                    update_session_mirror_cursor=lambda thread_id, rollout_path, cursor: None,
                    get_or_init_session_mirror_cursor=lambda thread_id, rollout_path, initial_cursor: 0,
                    read_new_session_events=lambda session_path, cursor, max_events=None: ([], 0),
                    get_archive_backlog_max_events=lambda: 10,
                    collect_session_mirror_items=lambda thread_id, events, **kwargs: [],
                    get_seen_agent_messages=lambda thread_id: {},
                    get_seen_user_messages=lambda thread_id: {},
                    resolve_session_mirror_channel=resolve_channel,
                    resolve_target_ref=lambda thread_id: (thread_id, thread_id),
                    has_session_mirror_event=lambda digest, thread_id: False,
                    send_session_mirror_item=lambda channel, item, **kwargs: None,
                    claim_session_mirror_event=lambda digest, thread_id: True,
                    deactivate_session_mirror_output_target=lambda thread_id: None,
                    send_typing_pulse=send_typing_pulse,
                    log=logs.append,
                ),
            )

        self.assertEqual(channels, [222])
        self.assertEqual(typing_pulses, [(222, "thread-1", "session_mirror_busy")])
        self.assertEqual(logs, ["session_mirror_typing_pulse target=thread-1 channel=222"])


if __name__ == "__main__":
    _ = unittest.main()
