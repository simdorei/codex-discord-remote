import unittest
from dataclasses import dataclass

import codex_discord_prefix_mirror_commands as prefix_mirror


@dataclass(frozen=True)
class FakeChannel:
    id: int = 222


@dataclass(frozen=True)
class FakeMessage:
    channel: FakeChannel

    @classmethod
    def make(cls, channel_id: int = 222) -> "FakeMessage":
        return cls(channel=FakeChannel(channel_id))


class PrefixMirrorCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_deps(
        self,
        *,
        bridge_output: str = "Discord bridge sync complete.",
        mirror_sync_output: str = "Mirror sync complete.",
        mirror_list_output: str = "Mirror list.",
        mirror_check_output: str = "Mirror check.",
    ) -> tuple[
        prefix_mirror.PrefixMirrorCommandDeps,
        list[str],
        list[tuple[str, object, int | None, int | None]],
        list[str],
    ]:
        sent: list[str] = []
        calls: list[tuple[str, object, int | None, int | None]] = []
        logs: list[str] = []

        async def send_chunks(target: object, text: str, *, context: str = "send_chunks") -> object:
            _ = target
            sent.append(f"{context}:{text}")
            return len(text)

        async def refresh(bot: object, *, limit: int | None = None) -> str:
            calls.append(("bridge", bot, limit, None))
            return bridge_output

        async def sync(bot: object, *, limit: int | None = None) -> str:
            calls.append(("mirror_sync", bot, limit, None))
            return mirror_sync_output

        def mirror_list(bot: object, limit: int | None = None, *, channel_id: int | None = None) -> str:
            calls.append(("mirror_list", bot, limit, channel_id))
            return mirror_list_output

        def mirror_check(bot: object, limit: int | None = None, *, channel_id: int | None = None) -> str:
            calls.append(("mirror_check", bot, limit, channel_id))
            return mirror_check_output

        deps = prefix_mirror.PrefixMirrorCommandDeps(
            send_chunks=send_chunks,
            refresh_discord_bridge_session=refresh,
            sync_codex_mirror=sync,
            build_mirror_list=mirror_list,
            build_mirror_check=mirror_check,
            log_line=logs.append,
        )
        return deps, sent, calls, logs

    async def test_dispatches_happy_path_bridge_and_mirror_commands(self) -> None:
        deps, sent, calls, logs = self.make_deps()
        message = FakeMessage.make()
        fake_bot = object()

        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("bridge", "sync 17", message, fake_bot, deps=deps))
        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "sync", message, fake_bot, deps=deps))
        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "list 9", message, fake_bot, deps=deps))
        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "check 7", message, fake_bot, deps=deps))

        self.assertEqual(
            sent,
            [
                "prefix_bridge_sync_start:Discord bridge sync started.",
                "send_chunks:Discord bridge sync complete.",
                "prefix_mirror_sync_start:Mirror sync started.",
                "send_chunks:Mirror sync complete.",
                "send_chunks:Mirror list.",
                "send_chunks:Mirror check.",
            ],
        )
        self.assertEqual(
            calls,
            [
                ("bridge", fake_bot, 17, None),
                ("mirror_sync", fake_bot, None, None),
                ("mirror_list", fake_bot, 9, None),
                ("mirror_check", fake_bot, 7, None),
            ],
        )
        self.assertEqual(logs, [])

    async def test_preserves_usage_errors_failures_and_unhandled_commands(self) -> None:
        deps, sent, calls, logs = self.make_deps()
        message = FakeMessage.make()
        fake_bot = object()

        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("bridge", "bad 1", message, fake_bot, deps=deps))
        self.assertEqual(sent[-1], "prefix_bridge_sync_usage:Usage: !bridge sync [limit]")

        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "sync 1", message, fake_bot, deps=deps))
        self.assertEqual(sent[-1], "prefix_mirror_usage:Usage: !mirror sync")

        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "bad", message, fake_bot, deps=deps))
        self.assertEqual(sent[-1], "prefix_mirror_usage:Usage: !mirror sync | !mirror list [limit] | !mirror check [limit]")

        async def failing_refresh(bot: object, *, limit: int | None = None) -> str:
            _ = bot, limit
            raise RuntimeError("refresh failed")

        deps = prefix_mirror.PrefixMirrorCommandDeps(
            send_chunks=deps.send_chunks,
            refresh_discord_bridge_session=failing_refresh,
            sync_codex_mirror=deps.sync_codex_mirror,
            build_mirror_list=deps.build_mirror_list,
            build_mirror_check=deps.build_mirror_check,
            log_line=logs.append,
        )
        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("sync", "", message, fake_bot, deps=deps))
        self.assertIn("Discord bridge sync failed\n\nERROR: refresh failed", sent[-1])
        self.assertTrue(any(line.startswith("bridge_sync_failed\n") for line in logs))

        async def failing_sync(bot: object, *, limit: int | None = None) -> str:
            _ = bot, limit
            raise RuntimeError("sync failed")

        def failing_list(bot: object, limit: int | None = None, *, channel_id: int | None = None) -> str:
            _ = bot, limit, channel_id
            raise RuntimeError("list failed")

        def failing_check(bot: object, limit: int | None = None, *, channel_id: int | None = None) -> str:
            _ = bot, limit, channel_id
            raise RuntimeError("check failed")

        deps = prefix_mirror.PrefixMirrorCommandDeps(
            send_chunks=deps.send_chunks,
            refresh_discord_bridge_session=deps.refresh_discord_bridge_session,
            sync_codex_mirror=failing_sync,
            build_mirror_list=failing_list,
            build_mirror_check=failing_check,
            log_line=logs.append,
        )
        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "sync", message, fake_bot, deps=deps))
        self.assertIn("Mirror sync failed\n\nERROR: sync failed", sent[-1])
        self.assertTrue(any(line.startswith("mirror_sync_failed\n") for line in logs))

        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "list 3", message, fake_bot, deps=deps))
        self.assertIn("Mirror list failed\n\nERROR: list failed", sent[-1])
        self.assertTrue(any(line.startswith("mirror_list_failed\n") for line in logs))

        self.assertTrue(await prefix_mirror.handle_prefix_mirror_command("mirror", "check 3", message, fake_bot, deps=deps))
        self.assertIn("Mirror check failed\n\nERROR: check failed", sent[-1])
        self.assertTrue(any(line.startswith("mirror_check_failed\n") for line in logs))

        self.assertFalse(await prefix_mirror.handle_prefix_mirror_command("where", "", message, fake_bot, deps=deps))
        self.assertEqual(calls, [])
