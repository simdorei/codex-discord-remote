from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import cast

import discord

import codex_discord_mirror_channels as mirror_channels
import codex_discord_mirror_thread_channels as mirror_thread_channels
from codex_thread_models import ThreadInfo


class MirrorThreadRemapTests(unittest.IsolatedAsyncioTestCase):
    def _thread_info(self) -> ThreadInfo:
        return ThreadInfo(
            id="thread-1",
            title="Thread",
            cwd=str(Path("C:/repo")),
            updated_at=1,
            rollout_path="thread.jsonl",
            model="gpt",
            reasoning_effort="high",
            tokens_used=1,
        )

    async def test_get_or_create_thread_channel_remaps_unavailable_stored_thread(self) -> None:
        original_thread = discord.Thread

        class FakeFetchError(RuntimeError):
            pass

        class FakeThread:
            def __init__(self, thread_id: int, name: str) -> None:
                self.id: int = thread_id
                self.name: str = name

            async def edit(self, *, name: str, reason: str) -> FakeThread:
                _ = reason
                self.name = name
                return self

        class FakeGuild:
            def __init__(self) -> None:
                self.fetch_calls: list[int] = []

            def get_thread(self, _thread_id: int) -> None:
                return None

            async def fetch_channel(self, thread_id: int) -> FakeThread:
                self.fetch_calls.append(thread_id)
                raise FakeFetchError("Unknown Channel")

        class EmptyArchivedThreads:
            async def __aiter__(self) -> EmptyArchivedThreads:
                return self

            async def __anext__(self) -> FakeThread:
                raise StopAsyncIteration

        class FakeTextChannel:
            def __init__(self, thread: FakeThread) -> None:
                self.id: int = 222
                self.guild: FakeGuild = FakeGuild()
                self.threads: list[FakeThread] = [thread]
                self.created_threads: list[str] = []

            def archived_threads(self, *, limit: int) -> EmptyArchivedThreads:
                _ = limit
                return EmptyArchivedThreads()

            async def create_thread(self, *, name: str, **_kwargs: str) -> FakeThread:
                self.created_threads.append(name)
                return FakeThread(555, name)

        logs: list[str] = []
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            deps = mirror_channels.MirrorChannelDeps(
                db_path=db_path,
                normalize_project_key=lambda project_key: str(project_key or "").lower(),
                project_keys_match=lambda left, right: left == right,
                get_thread_ui_name=lambda _thread_id, _thread: "unused",
                log=logs.append,
                fetch_failure_types=(FakeFetchError,),
            )
            codex_thread = self._thread_info()
            mirror_channels.upsert_mirror_thread(
                codex_thread,
                "Project",
                "unused",
                222,
                333,
                deps=deps,
            )
            reusable_thread = FakeThread(444, "unused")
            project_channel = FakeTextChannel(reusable_thread)

            try:
                discord.Thread = FakeThread
                result = await mirror_thread_channels.get_or_create_thread_channel(
                    codex_thread,
                    "Project",
                    cast(discord.TextChannel, cast(object, project_channel)),
                    deps=deps,
                )
            finally:
                discord.Thread = original_thread

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT discord_thread_id FROM mirror_threads WHERE codex_thread_id = ?",
                    (codex_thread.id,),
                ).fetchone()

        self.assertIs(result, reusable_thread)
        self.assertEqual(project_channel.guild.fetch_calls, [333])
        self.assertEqual(project_channel.created_threads, [])
        self.assertEqual(row, (444,))
        self.assertIn("mirror_thread_remapped", "\n".join(logs))
        self.assertIn("old_id=333", "\n".join(logs))
        self.assertIn("new_id=444", "\n".join(logs))
        self.assertIn("reason=", "\n".join(logs))


if __name__ == "__main__":
    _ = unittest.main()
