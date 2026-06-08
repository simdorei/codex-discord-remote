from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import codex_discord_bot as bot


class MirrorSyncCleanupTests(unittest.IsolatedAsyncioTestCase):
    def make_thread(self, thread_id: str, cwd: str, title: str = "thread") -> bot.bridge.ThreadInfo:
        return bot.bridge.ThreadInfo(
            id=thread_id,
            title=title,
            cwd=cwd,
            updated_at=1,
            rollout_path=f"{thread_id}.jsonl",
            model="gpt",
            reasoning_effort="high",
            tokens_used=1,
        )

    async def test_sync_codex_mirror_removes_db_rows_when_no_active_threads(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_recent_threads = bot.bridge.load_recent_threads
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
                        ("stale-project", "stale", 111, 1.0),
                    )
                    conn.execute(
                        "INSERT INTO mirror_threads "
                        "(codex_thread_id, project_key, thread_title, "
                        "discord_channel_id, discord_thread_id, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("stale-thread", "stale-project", "stale", 111, 222, 1.0),
                    )

                bot.bridge.load_recent_threads = lambda limit: []
                guild = FakeGuild()
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: guild,
                )

                output = await bot.sync_codex_mirror(fake_bot, limit=30)

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    thread_rows = conn.execute("SELECT codex_thread_id FROM mirror_threads").fetchall()
                    project_rows = conn.execute("SELECT project_key FROM mirror_projects").fetchall()

            self.assertIn("Mirror sync complete.", output)
            self.assertIn("threads: 0", output)
            self.assertIn("stale_threads_removed: 1", output)
            self.assertIn("stale_projects_removed: 1", output)
            self.assertEqual(thread_rows, [])
            self.assertEqual(project_rows, [])
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_recent_threads = old_load_recent_threads
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    async def test_sync_codex_mirror_removes_rows_outside_requested_limit(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_recent_threads = bot.bridge.load_recent_threads
        old_filter_mirrorable_threads = bot.filter_mirrorable_threads
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
                bot.init_mirror_db()
                scoped_thread = self.make_thread("scoped-thread", str(Path(temp_dir)), "scoped")
                hidden_active_thread = self.make_thread("hidden-active-thread", str(Path(temp_dir)), "hidden")
                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    conn.execute(
                        "INSERT INTO mirror_projects "
                        "(project_key, project_name, discord_channel_id, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (str(Path(temp_dir)), "project", 111, 1.0),
                    )
                    conn.execute(
                        "INSERT INTO mirror_threads "
                        "(codex_thread_id, project_key, thread_title, "
                        "discord_channel_id, discord_thread_id, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            hidden_active_thread.id,
                            str(Path(temp_dir)),
                            "hidden",
                            111,
                            222,
                            1.0,
                        ),
                    )

                load_calls: list[int] = []

                def fake_load_recent_threads(limit: int = 20) -> list[bot.bridge.ThreadInfo]:
                    load_calls.append(limit)
                    if limit == 0:
                        return [scoped_thread, hidden_active_thread]
                    return [scoped_thread]

                async def fake_get_project_channel(guild, category, project_key, project_name):
                    bot.upsert_mirror_project(project_key, project_name, 111)
                    return SimpleNamespace(id=111)

                async def fake_get_thread_channel(codex_thread, project_key, project_channel):
                    bot.upsert_mirror_thread(codex_thread, project_key, codex_thread.title, 111, 333)
                    return SimpleNamespace(id=333)

                bot.bridge.load_recent_threads = fake_load_recent_threads
                bot.filter_mirrorable_threads = lambda threads: list(threads)
                bot.get_or_create_project_channel = fake_get_project_channel
                bot.get_or_create_thread_channel = fake_get_thread_channel
                guild = FakeGuild()
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: guild,
                )

                output = await bot.sync_codex_mirror(fake_bot, limit=7)

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    rows = conn.execute(
                        "SELECT codex_thread_id FROM mirror_threads ORDER BY codex_thread_id"
                    ).fetchall()

            self.assertEqual(load_calls, [7])
            self.assertIn("threads: 1", output)
            self.assertIn("stale_threads_removed: 1", output)
            self.assertEqual(rows, [("scoped-thread",)])
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_recent_threads = old_load_recent_threads
            bot.filter_mirrorable_threads = old_filter_mirrorable_threads
            bot.get_or_create_project_channel = old_get_project_channel
            bot.get_or_create_thread_channel = old_get_thread_channel
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    async def test_sync_codex_mirror_defaults_to_state_root_threads(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_recent_threads = bot.bridge.load_recent_threads
        old_load_user_root_threads = bot.bridge.load_user_root_threads
        old_filter_mirrorable_threads = bot.filter_mirrorable_threads
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

                def fake_load_recent_threads(limit: int = 20) -> list[bot.bridge.ThreadInfo]:
                    raise AssertionError(f"default mirror sync used recent limit instead of DB root scope: {limit}")

                def fake_load_user_root_threads(limit: int = 0) -> list[bot.bridge.ThreadInfo]:
                    load_db_calls.append(f"db-root:{limit}")
                    return [root_thread]

                async def fake_get_project_channel(guild, category, project_key, project_name):
                    bot.upsert_mirror_project(project_key, project_name, 111)
                    return SimpleNamespace(id=111)

                async def fake_get_thread_channel(codex_thread, project_key, project_channel):
                    bot.upsert_mirror_thread(codex_thread, project_key, codex_thread.title, 111, 333)
                    return SimpleNamespace(id=333)

                bot.bridge.load_recent_threads = fake_load_recent_threads
                bot.bridge.load_user_root_threads = fake_load_user_root_threads
                bot.filter_mirrorable_threads = lambda threads: list(threads)
                bot.get_or_create_project_channel = fake_get_project_channel
                bot.get_or_create_thread_channel = fake_get_thread_channel
                guild = FakeGuild()
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: guild,
                )

                output = await bot.sync_codex_mirror(fake_bot)

            self.assertEqual(load_db_calls, ["db-root:0"])
            self.assertIn("threads: 1", output)
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_recent_threads = old_load_recent_threads
            bot.bridge.load_user_root_threads = old_load_user_root_threads
            bot.filter_mirrorable_threads = old_filter_mirrorable_threads
            bot.get_or_create_project_channel = old_get_project_channel
            bot.get_or_create_thread_channel = old_get_thread_channel
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    def test_build_mirror_check_defaults_to_state_root_threads(self) -> None:
        old_load_user_root_threads = bot.bridge.load_user_root_threads
        old_status_builder = bot.discord_mirror_status.build_mirror_check
        root_thread = self.make_thread("root-thread", str(Path.cwd()), "root")
        observed_threads: list[list[bot.bridge.ThreadInfo]] = []

        def fake_load_user_root_threads(limit: int = 0) -> list[bot.bridge.ThreadInfo]:
            return [root_thread]

        def fake_build_mirror_check(**kwargs) -> str:
            observed_threads.append(kwargs["threads"])
            return "Mirror check"

        try:
            bot.bridge.load_user_root_threads = fake_load_user_root_threads
            bot.discord_mirror_status.build_mirror_check = fake_build_mirror_check

            output = bot.build_mirror_check()

            self.assertEqual(output, "Mirror check")
            self.assertEqual(observed_threads, [[root_thread]])
        finally:
            bot.bridge.load_user_root_threads = old_load_user_root_threads
            bot.discord_mirror_status.build_mirror_check = old_status_builder

    def test_build_mirror_list_defaults_to_state_root_thread_ids(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_user_root_threads = bot.bridge.load_user_root_threads
        old_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                bot.MIRROR_DB_PATH = Path(temp_dir) / "mirror.sqlite"
                os.environ["CODEX_DISCORD_LOG_PATH"] = str(Path(temp_dir) / "discord.log")
                root_thread = self.make_thread("root-thread", str(Path(temp_dir)), "root")
                hidden_thread = self.make_thread("hidden-thread", str(Path(temp_dir)), "hidden")
                bot.bridge.load_user_root_threads = lambda limit=0: [root_thread]
                bot.init_mirror_db()
                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    conn.execute(
                        "INSERT INTO mirror_projects "
                        "(project_key, project_name, discord_channel_id, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (str(Path(temp_dir)), "project", 111, 1.0),
                    )
                    conn.execute(
                        "INSERT INTO mirror_threads "
                        "(codex_thread_id, project_key, thread_title, "
                        "discord_channel_id, discord_thread_id, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (hidden_thread.id, str(Path(temp_dir)), "hidden", 111, 222, 3.0),
                    )
                    conn.execute(
                        "INSERT INTO mirror_threads "
                        "(codex_thread_id, project_key, thread_title, "
                        "discord_channel_id, discord_thread_id, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (root_thread.id, str(Path(temp_dir)), "root", 111, 333, 1.0),
                    )

                output = bot.build_mirror_list()

            self.assertIn("/ root", output)
            self.assertNotIn("/ hidden", output)
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_user_root_threads = old_load_user_root_threads
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    async def test_sync_codex_mirror_does_not_cleanup_when_state_root_scope_fails(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_user_root_threads = bot.bridge.load_user_root_threads
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

                bot.bridge.load_user_root_threads = lambda limit=0: (_ for _ in ()).throw(
                    RuntimeError("state root scope failed")
                )
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: FakeGuild(),
                )

                with self.assertRaisesRegex(RuntimeError, "state root scope failed"):
                    await bot.sync_codex_mirror(fake_bot)

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    project_rows = conn.execute("SELECT project_key FROM mirror_projects").fetchall()
                    thread_rows = conn.execute("SELECT codex_thread_id FROM mirror_threads").fetchall()

            self.assertEqual(project_rows, [("project",)])
            self.assertEqual(thread_rows, [("thread-1",)])
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_user_root_threads = old_load_user_root_threads
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    async def test_sync_codex_mirror_keeps_same_project_duplicate_titles_from_state_root_scope(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_user_root_threads = bot.bridge.load_user_root_threads
        old_filter_mirrorable_threads = bot.filter_mirrorable_threads
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
                project = Path(temp_dir) / "project"
                newer_thread = self.make_thread("thread-new", str(project), "same task")
                older_thread = self.make_thread("thread-old", str(project), "same task")
                bot.bridge.load_user_root_threads = lambda limit=0: [newer_thread, older_thread]
                bot.filter_mirrorable_threads = lambda threads: list(threads)

                async def fake_get_project_channel(guild, category, project_key, project_name):
                    bot.upsert_mirror_project(project_key, project_name, 111)
                    return SimpleNamespace(id=111)

                async def fake_get_thread_channel(codex_thread, project_key, project_channel):
                    discord_thread_id = 222 if codex_thread.id == "thread-new" else 333
                    bot.upsert_mirror_thread(
                        codex_thread,
                        project_key,
                        codex_thread.title,
                        111,
                        discord_thread_id,
                    )
                    return SimpleNamespace(id=discord_thread_id)

                bot.get_or_create_project_channel = fake_get_project_channel
                bot.get_or_create_thread_channel = fake_get_thread_channel
                bot.init_mirror_db()
                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    conn.execute(
                        "INSERT INTO mirror_projects "
                        "(project_key, project_name, discord_channel_id, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (str(project), "project", 111, 1.0),
                    )
                    for thread_id, discord_thread_id in (("thread-new", 222), ("thread-old", 333)):
                        conn.execute(
                            "INSERT INTO mirror_threads "
                            "(codex_thread_id, project_key, thread_title, "
                            "discord_channel_id, discord_thread_id, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (thread_id, str(project), "same task", 111, discord_thread_id, 1.0),
                        )

                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: FakeGuild(),
                )

                output = await bot.sync_codex_mirror(fake_bot)

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    rows = conn.execute(
                        "SELECT codex_thread_id FROM mirror_threads ORDER BY codex_thread_id"
                    ).fetchall()

            self.assertIn("threads: 2", output)
            self.assertEqual(rows, [("thread-new",), ("thread-old",)])
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_user_root_threads = old_load_user_root_threads
            bot.filter_mirrorable_threads = old_filter_mirrorable_threads
            bot.get_or_create_project_channel = old_get_project_channel
            bot.get_or_create_thread_channel = old_get_thread_channel
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    def test_load_user_root_threads_reads_db_root_threads_without_subagents(self) -> None:
        old_state_db_path = bot.bridge.STATE_DB_PATH

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                state_db_path = Path(temp_dir) / "state.sqlite"
                bot.bridge.STATE_DB_PATH = state_db_path
                with sqlite3.connect(state_db_path) as conn:
                    conn.execute(
                        """
                        CREATE TABLE threads (
                            id TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            cwd TEXT NOT NULL,
                            updated_at INTEGER NOT NULL,
                            rollout_path TEXT NOT NULL,
                            model TEXT,
                            reasoning_effort TEXT,
                            tokens_used INTEGER NOT NULL DEFAULT 0,
                            archived INTEGER NOT NULL DEFAULT 0,
                            source TEXT NOT NULL,
                            thread_source TEXT
                        )
                        """
                    )
                    conn.executemany(
                        """
                        INSERT INTO threads (
                            id, title, cwd, updated_at, rollout_path, model,
                            reasoning_effort, tokens_used, archived, source, thread_source
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            ("root-1", "root", "C:/repo", 3, "root.jsonl", "gpt", "high", 10, 0, "vscode", None),
                            ("sub-1", "", "C:/repo", 4, "sub.jsonl", "gpt", "high", 20, 0, '{"subagent":{}}', "subagent"),
                            ("archived-1", "archived", "C:/repo", 2, "archived.jsonl", "gpt", "high", 30, 1, "vscode", None),
                            ("empty-title", "", "C:/repo", 1, "empty.jsonl", "gpt", "high", 40, 0, "vscode", None),
                        ],
                    )

                threads = bot.bridge.load_user_root_threads()

            self.assertEqual([thread.id for thread in threads], ["root-1"])
        finally:
            bot.bridge.STATE_DB_PATH = old_state_db_path

    def test_codex_window_title_filter_rejects_discord_bridge_browser_title(self) -> None:
        self.assertTrue(bot.bridge.is_codex_desktop_window_title("Codex"))
        self.assertTrue(bot.bridge.is_codex_desktop_window_title("Codex - thread"))
        self.assertFalse(
            bot.bridge.is_codex_desktop_window_title(
                'Discord | "taxlab" | Codex app bridge - Chrome'
            )
        )
        self.assertFalse(
            bot.bridge.is_codex_desktop_window_title(
                r"관리자: C:\Users\banpo\AppData\Local\OpenAI\Codex\bin\codex.exe"
            )
        )

if __name__ == "__main__":
    unittest.main()
