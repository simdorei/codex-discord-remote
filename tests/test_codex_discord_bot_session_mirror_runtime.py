from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from dataclasses import replace
from pathlib import Path
import unittest
from unittest import mock

import codex_discord_bot_session_mirror_runtime as session_mirror_runtime
import codex_discord_gpt_delivery as gpt_delivery
import codex_discord_session_mirror_target as session_mirror_target
from codex_session_events import JsonEvent
from codex_thread_models import ThreadContextUsage, ThreadInfo
from tests.test_codex_discord_bot_session_mirror_factory import (
    FakeChannel,
    make_test_runtime,
)


class FakeOwner:
    session_mirror_poll_seconds: float = 1.0
    _session_mirror_archive_skip_logged: set[str] = set()

    def session_mirror_archive_skip_logged(self) -> set[str]:
        return self._session_mirror_archive_skip_logged

    def is_closed(self) -> bool:
        return False

    def get_cached_channel_or_thread(
        self,
        channel_id: int,
    ) -> tuple[FakeChannel | None, str]:
        _ = channel_id
        return None, "miss"

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        _ = channel_id
        return FakeChannel()

    def get_session_mirror_seen_agent_messages(
        self, codex_thread_id: str
    ) -> dict[str, float]:
        _ = codex_thread_id
        return {}

    def get_session_mirror_seen_user_messages(
        self, codex_thread_id: str
    ) -> dict[str, float]:
        _ = codex_thread_id
        return {}

    async def session_mirror_loop(self) -> None:
        return None

    async def resolve_session_mirror_channel(
        self,
        discord_thread_id: int,
    ) -> FakeChannel | None:
        _ = discord_thread_id
        return None

    async def send_session_mirror_item(
        self,
        channel: FakeChannel,
        item: session_mirror_target.SessionMirrorItem,
        *,
        target_thread_id: str,
        target_ref: str,
    ) -> None:
        _ = (channel, item, target_thread_id, target_ref)

    async def mirror_session_target(
        self,
        target: session_mirror_runtime.SessionMirrorTargetMapping,
    ) -> None:
        _ = target


class BotSessionMirrorRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_passes_exact_lock_identity_to_target(self) -> None:
        configured_channel_lock = asyncio.Lock()
        runtime = make_test_runtime(configured_channel_lock, Path("runtime.sqlite"))
        observed: list[
            session_mirror_target.SessionMirrorTargetDeps[
                ThreadInfo,
                ThreadContextUsage,
                JsonEvent,
                FakeChannel,
            ]
        ] = []

        async def capture_target(
            target: session_mirror_runtime.SessionMirrorTargetMapping,
            *,
            deps: session_mirror_target.SessionMirrorTargetDeps[
                ThreadInfo,
                ThreadContextUsage,
                JsonEvent,
                FakeChannel,
            ],
        ) -> None:
            _ = target
            observed.append(deps)

        with mock.patch.object(
            session_mirror_target,
            "mirror_session_target",
            capture_target,
        ):
            await runtime.mirror_session_target(
                FakeOwner(),
                {"codex_thread_id": "thread", "discord_thread_id": 200},
            )

        self.assertEqual(len(observed), 1)
        self.assertIs(observed[0].configured_channel_lock, configured_channel_lock)
        lease_deps = observed[0].active_delivery_lease_deps
        if lease_deps is None:
            self.fail("runtime target did not receive the active delivery lease")
        self.assertIs(
            lease_deps.configured_channel_lock,
            configured_channel_lock,
        )

    def test_runtime_rejects_second_lock_before_delivery(self) -> None:
        configured_channel_lock = asyncio.Lock()
        runtime = make_test_runtime(configured_channel_lock, Path("runtime.sqlite"))

        with self.assertRaises(gpt_delivery.ConfiguredChannelLockMismatchError):
            _ = replace(runtime.deps, configured_channel_lock=asyncio.Lock())


if __name__ == "__main__":
    _ = unittest.main()
