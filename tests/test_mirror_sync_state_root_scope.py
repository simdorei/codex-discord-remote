from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import codex_discord_bot as bot
from codex_thread_models import ThreadInfo
from tests.mirror_sync_bridge_types import bridge_module, codex_discord_bot


class MirrorSyncStateRootScopeTests(unittest.IsolatedAsyncioTestCase):
    def make_thread(self, thread_id: str, cwd: str, title: str = "thread") -> ThreadInfo:
        return ThreadInfo(
            id=thread_id,
            title=title,
            cwd=cwd,
            updated_at=1,
            rollout_path=f"{thread_id}.jsonl",
            model="gpt",
            reasoning_effort="high",
            tokens_used=1,
        )

    async def test_sync_codex_mirror_defaults_to_state_root_threads(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        bridge = bridge_module()
        old_load_recent_threads = bridge.load_recent_threads
        old_load_user_root_threads = bridge.load_user_root_threads
        old_filter_mirrorable_threads = bot.filter_mirrorable_threads
        old_filter_app_server_available_threads = bot.filter_app_server_available_threads
        old_get_project_channel = bot.get_or_create_project_channel
        old_get_thread_channel = bot.get_or_create_thread_channel
        old_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")

        class FakeGuild:
            def __init__(self) -> None:
                self.categories = [SimpleNamespace(name="Codex", id=999)]
                self.text_channels = []

            def get_channel(self, channel_id: int) -> None:
                return None

            def get_thread(self, thread_id: int) -> None:
                return None

            async def fetch_channel(self, channel_id: int) -> SimpleNamespace:
                return SimpleNamespace(id=channel_id)

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                bot.MIRROR_DB_PATH = Path(temp_dir) / "mirror.sqlite"
                os.environ["CODEX_DISCORD_LOG_PATH"] = str(Path(temp_dir) / "discord.log")
                root_thread = self.make_thread("root-thread", str(Path(temp_dir)), "root")
                load_db_calls: list[str] = []

                def fake_load_recent_threads(limit: int = 20) -> list[ThreadInfo]:
                    raise AssertionError(f"default mirror sync used recent limit instead of DB root scope: {limit}")

                def fake_load_user_root_threads(limit: int = 0) -> list[ThreadInfo]:
                    load_db_calls.append(f"db-root:{limit}")
                    return [root_thread]

                async def fake_get_project_channel(guild, category, project_key, project_name):
                    bot.upsert_mirror_project(project_key, project_name, 111)
                    return SimpleNamespace(id=111)

                async def fake_get_thread_channel(codex_thread, project_key, project_channel):
                    bot.upsert_mirror_thread(codex_thread, project_key, codex_thread.title, 111, 333)
                    return SimpleNamespace(id=333)

                bridge.load_recent_threads = fake_load_recent_threads
                bridge.load_user_root_threads = fake_load_user_root_threads
                bot.filter_mirrorable_threads = lambda threads: list(threads)
                bot.filter_app_server_available_threads = lambda threads: list(threads)
                bot.get_or_create_project_channel = fake_get_project_channel
                bot.get_or_create_thread_channel = fake_get_thread_channel
                guild = FakeGuild()
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: guild,
                )

                output = await bot.sync_codex_mirror(codex_discord_bot(fake_bot))

            self.assertEqual(load_db_calls, ["db-root:0"])
            self.assertIn("threads: 1", output)
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bridge.load_recent_threads = old_load_recent_threads
            bridge.load_user_root_threads = old_load_user_root_threads
            bot.filter_mirrorable_threads = old_filter_mirrorable_threads
            bot.filter_app_server_available_threads = old_filter_app_server_available_threads
            bot.get_or_create_project_channel = old_get_project_channel
            bot.get_or_create_thread_channel = old_get_thread_channel
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    async def test_sync_codex_mirror_excludes_registered_gpt_root(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        bridge = bridge_module()
        old_load_user_root_threads = bridge.load_user_root_threads
        old_filter_mirrorable_threads = bot.filter_mirrorable_threads
        old_filter_app_server_available_threads = bot.filter_app_server_available_threads
        old_get_project_channel = bot.get_or_create_project_channel
        old_get_thread_channel = bot.get_or_create_thread_channel
        old_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")

        class FakeGuild:
            def __init__(self) -> None:
                self.categories = [SimpleNamespace(name="Codex", id=999)]
                self.text_channels = []

            def get_channel(self, channel_id: int) -> None:
                _ = channel_id
                return None

            def get_thread(self, thread_id: int) -> None:
                _ = thread_id
                return None

            async def fetch_channel(self, channel_id: int) -> SimpleNamespace:
                return SimpleNamespace(id=channel_id)

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                bot.MIRROR_DB_PATH = Path(temp_dir) / "mirror.sqlite"
                os.environ["CODEX_DISCORD_LOG_PATH"] = str(Path(temp_dir) / "discord.log")
                bot.init_mirror_db()
                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    _ = conn.execute(
                        "ALTER TABLE mirror_threads ADD COLUMN managed_by TEXT NOT NULL DEFAULT 'ordinary'"
                    )
                    _ = conn.execute(
                        "ALTER TABLE mirror_threads ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'active'"
                    )
                    _ = conn.execute(
                        "INSERT INTO mirror_projects VALUES (?, ?, ?, ?)",
                        ("codex:chats", "GPT", 900, 1.0),
                    )
                    _ = conn.execute(
                        "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            "gpt-root",
                            "codex:chats",
                            "GPT",
                            900,
                            901,
                            1.0,
                            "gpt_chat",
                            "active",
                        ),
                    )
                ordinary = self.make_thread("ordinary-root", str(Path(temp_dir)))
                gpt = self.make_thread("gpt-root", str(Path(temp_dir)))
                mirrored_ids: list[str] = []

                bridge.load_user_root_threads = lambda limit=0: [ordinary, gpt]
                bot.filter_mirrorable_threads = lambda threads: list(threads)
                bot.filter_app_server_available_threads = lambda threads: list(threads)

                async def get_project_channel(guild, category, project_key, project_name):
                    _ = (guild, category, project_name)
                    return SimpleNamespace(id=111, key=project_key)

                async def get_thread_channel(codex_thread, project_key, project_channel):
                    _ = (project_key, project_channel)
                    mirrored_ids.append(codex_thread.id)
                    return SimpleNamespace(id=333)

                bot.get_or_create_project_channel = get_project_channel
                bot.get_or_create_thread_channel = get_thread_channel
                guild = FakeGuild()
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: guild,
                )

                output = await bot.sync_codex_mirror(codex_discord_bot(fake_bot))

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    gpt_row = conn.execute(
                        "SELECT managed_by FROM mirror_threads WHERE codex_thread_id = 'gpt-root'"
                    ).fetchone()

            self.assertEqual(mirrored_ids, ["ordinary-root"])
            self.assertEqual(gpt_row, ("gpt_chat",))
            self.assertIn("threads: 1", output)
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bridge.load_user_root_threads = old_load_user_root_threads
            bot.filter_mirrorable_threads = old_filter_mirrorable_threads
            bot.filter_app_server_available_threads = old_filter_app_server_available_threads
            bot.get_or_create_project_channel = old_get_project_channel
            bot.get_or_create_thread_channel = old_get_thread_channel
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    async def test_sync_codex_mirror_does_not_cleanup_when_state_root_scope_fails(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        bridge = bridge_module()
        old_load_user_root_threads = bridge.load_user_root_threads
        old_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")

        class FakeGuild:
            def __init__(self) -> None:
                self.categories = [SimpleNamespace(name="Codex", id=999)]
                self.text_channels = []

            def get_channel(self, channel_id: int) -> None:
                return None

            def get_thread(self, thread_id: int) -> None:
                return None

            async def fetch_channel(self, channel_id: int) -> SimpleNamespace:
                return SimpleNamespace(id=channel_id)

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                bot.MIRROR_DB_PATH = Path(temp_dir) / "mirror.sqlite"
                os.environ["CODEX_DISCORD_LOG_PATH"] = str(Path(temp_dir) / "discord.log")
                bot.init_mirror_db()
                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    conn.execute(
                        "INSERT INTO mirror_projects "
                        "(project_key, project_name, discord_channel_id, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        ("project", "project", 111, 1.0),
                    )
                    conn.execute(
                        "INSERT INTO mirror_threads "
                        "(codex_thread_id, project_key, thread_title, "
                        "discord_channel_id, discord_thread_id, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("thread-1", "project", "thread", 111, 222, 1.0),
                    )

                bridge.load_user_root_threads = lambda limit=0: (_ for _ in ()).throw(
                    RuntimeError("state root scope failed")
                )
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: FakeGuild(),
                )

                with self.assertRaisesRegex(RuntimeError, "state root scope failed"):
                    await bot.sync_codex_mirror(codex_discord_bot(fake_bot))

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    project_rows = conn.execute("SELECT project_key FROM mirror_projects").fetchall()
                    thread_rows = conn.execute("SELECT codex_thread_id FROM mirror_threads").fetchall()

            self.assertEqual(project_rows, [("project",)])
            self.assertEqual(thread_rows, [("thread-1",)])
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bridge.load_user_root_threads = old_load_user_root_threads
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path


if __name__ == "__main__":
    unittest.main()
