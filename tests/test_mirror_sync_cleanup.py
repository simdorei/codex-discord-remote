from __future__ import annotations

import os
import sqlite3
import tempfile
import time
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

    async def test_sync_codex_mirror_removes_rows_outside_visible_limit(self) -> None:
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
                visible_thread = self.make_thread("visible-thread", str(Path(temp_dir)), "visible")
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
                        return [visible_thread, hidden_active_thread]
                    return [visible_thread]

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
            self.assertEqual(rows, [("visible-thread",)])
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

    async def test_sync_codex_mirror_defaults_to_ui_visible_threads(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_recent_threads = bot.bridge.load_recent_threads
        old_load_ui_visible_threads = getattr(bot.bridge, "load_ui_visible_threads", None)
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
                visible_thread = self.make_thread("visible-thread", str(Path(temp_dir)), "visible")
                load_ui_calls: list[str] = []

                def fake_load_recent_threads(limit: int = 20) -> list[bot.bridge.ThreadInfo]:
                    raise AssertionError(f"default mirror sync used DB limit instead of UI visible: {limit}")

                def fake_load_ui_visible_threads() -> list[bot.bridge.ThreadInfo]:
                    load_ui_calls.append("ui-visible")
                    return [visible_thread]

                async def fake_get_project_channel(guild, category, project_key, project_name):
                    bot.upsert_mirror_project(project_key, project_name, 111)
                    return SimpleNamespace(id=111)

                async def fake_get_thread_channel(codex_thread, project_key, project_channel):
                    bot.upsert_mirror_thread(codex_thread, project_key, codex_thread.title, 111, 333)
                    return SimpleNamespace(id=333)

                bot.bridge.load_recent_threads = fake_load_recent_threads
                bot.bridge.load_ui_visible_threads = fake_load_ui_visible_threads
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

            self.assertEqual(load_ui_calls, ["ui-visible"])
            self.assertIn("threads: 1", output)
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_recent_threads = old_load_recent_threads
            if old_load_ui_visible_threads is None:
                delattr(bot.bridge, "load_ui_visible_threads")
            else:
                bot.bridge.load_ui_visible_threads = old_load_ui_visible_threads
            bot.filter_mirrorable_threads = old_filter_mirrorable_threads
            bot.get_or_create_project_channel = old_get_project_channel
            bot.get_or_create_thread_channel = old_get_thread_channel
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    def test_build_mirror_check_defaults_to_ui_visible_threads(self) -> None:
        old_load_ui_visible_threads = getattr(bot.bridge, "load_ui_visible_threads", None)
        old_status_builder = bot.discord_mirror_status.build_mirror_check
        visible_thread = self.make_thread("visible-thread", str(Path.cwd()), "visible")
        observed_threads: list[list[bot.bridge.ThreadInfo]] = []

        def fake_load_ui_visible_threads() -> list[bot.bridge.ThreadInfo]:
            return [visible_thread]

        def fake_build_mirror_check(**kwargs) -> str:
            observed_threads.append(kwargs["threads"])
            return "Mirror check"

        try:
            bot.bridge.load_ui_visible_threads = fake_load_ui_visible_threads
            bot.discord_mirror_status.build_mirror_check = fake_build_mirror_check

            output = bot.build_mirror_check()

            self.assertEqual(output, "Mirror check")
            self.assertEqual(observed_threads, [[visible_thread]])
        finally:
            if old_load_ui_visible_threads is None:
                delattr(bot.bridge, "load_ui_visible_threads")
            else:
                bot.bridge.load_ui_visible_threads = old_load_ui_visible_threads
            bot.discord_mirror_status.build_mirror_check = old_status_builder

    def test_build_mirror_list_defaults_to_actual_ui_visible_thread_ids(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_ui_visible_threads = bot.bridge.load_ui_visible_threads
        old_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                bot.MIRROR_DB_PATH = Path(temp_dir) / "mirror.sqlite"
                os.environ["CODEX_DISCORD_LOG_PATH"] = str(Path(temp_dir) / "discord.log")
                visible_thread = self.make_thread("visible-thread", str(Path(temp_dir)), "visible")
                hidden_thread = self.make_thread("hidden-thread", str(Path(temp_dir)), "hidden")
                bot.bridge.load_ui_visible_threads = lambda: [visible_thread]
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
                        (visible_thread.id, str(Path(temp_dir)), "visible", 111, 333, 1.0),
                    )

                output = bot.build_mirror_list()

            self.assertIn("/ visible", output)
            self.assertNotIn("/ hidden", output)
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_ui_visible_threads = old_load_ui_visible_threads
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    def test_visible_sidebar_matching_does_not_treat_project_heading_as_thread(self) -> None:
        thread = self.make_thread("thread-1", str(Path.cwd()), "taxlab task detail")

        matched = bot.bridge.match_visible_sidebar_threads(["taxlab"], [thread])

        self.assertEqual(matched, [])

    def test_visible_sidebar_matching_uses_project_heading_for_duplicate_titles(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            project_a = Path(temp_dir) / "project-a"
            project_b = Path(temp_dir) / "project-b"
            thread_a = self.make_thread("thread-a", str(project_a), "same task")
            thread_b = self.make_thread("thread-b", str(project_b), "same task")

            matched = bot.bridge.match_visible_sidebar_threads(
                [bot.bridge.get_thread_workspace_name(thread_b), "same task2분"],
                [thread_a, thread_b],
            )

        self.assertEqual(matched, [thread_b])

    def test_load_ui_visible_threads_rejects_same_project_duplicate_titles(self) -> None:
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                project = Path(temp_dir) / "project"
                newer_thread = self.make_thread("thread-new", str(project), "same task")
                older_thread = self.make_thread("thread-old", str(project), "same task")
                bot.bridge.scan_visible_sidebar_names = lambda: [
                    bot.bridge.get_thread_workspace_name(newer_thread),
                    "same task2분",
                ]
                bot.bridge.load_recent_threads = lambda limit=20: [newer_thread, older_thread]

                with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                    bot.bridge.load_ui_visible_threads()
        finally:
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads

    def test_load_ui_visible_threads_uses_visible_age_for_same_project_duplicates(self) -> None:
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                project = Path(temp_dir) / "project"
                newer_thread = self.make_thread("thread-new", str(project), "same task")
                older_thread = self.make_thread("thread-old", str(project), "same task")
                now = int(time.time())
                newer_thread.updated_at = now - (5 * 60)
                older_thread.updated_at = now - (2 * 3600)
                bot.bridge.scan_visible_sidebar_names = lambda: [
                    bot.bridge.get_thread_workspace_name(newer_thread),
                    "same task5분",
                ]
                bot.bridge.load_recent_threads = lambda limit=20: [newer_thread, older_thread]

                matched = bot.bridge.load_ui_visible_threads()

            self.assertEqual(matched, [newer_thread])
        finally:
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads

    def test_load_ui_visible_threads_rejects_empty_active_state_after_ui_scan(self) -> None:
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads

        try:
            bot.bridge.scan_visible_sidebar_names = lambda: ["visible task2분"]
            bot.bridge.load_recent_threads = lambda limit=20: []

            with self.assertRaisesRegex(RuntimeError, "active thread state is empty"):
                bot.bridge.load_ui_visible_threads()
        finally:
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads

    def test_load_ui_visible_threads_rejects_duplicate_titles_without_project_heading(self) -> None:
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                thread_a = self.make_thread("thread-a", str(Path(temp_dir) / "project-a"), "same task")
                thread_b = self.make_thread("thread-b", str(Path(temp_dir) / "project-b"), "same task")
                bot.bridge.scan_visible_sidebar_names = lambda: ["same task2분"]
                bot.bridge.load_recent_threads = lambda limit=20: [thread_a, thread_b]

                with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                    bot.bridge.load_ui_visible_threads()
        finally:
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads

    def test_load_ui_visible_threads_rejects_duplicate_workspace_headings(self) -> None:
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                thread_a = self.make_thread("thread-a", str(Path(temp_dir) / "one" / "repo"), "same task")
                thread_b = self.make_thread("thread-b", str(Path(temp_dir) / "two" / "repo"), "same task")
                bot.bridge.scan_visible_sidebar_names = lambda: ["repo", "same task2분"]
                bot.bridge.load_recent_threads = lambda limit=20: [thread_a, thread_b]

                with self.assertRaisesRegex(RuntimeError, "ambiguous|unmatched"):
                    bot.bridge.load_ui_visible_threads()
        finally:
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads

    def test_load_ui_visible_threads_rejects_ambiguous_heading_after_previous_project(self) -> None:
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                prior_thread = self.make_thread(
                    "thread-prior",
                    str(Path(temp_dir) / "project-a"),
                    "same task",
                )
                repo_a = self.make_thread("thread-a", str(Path(temp_dir) / "one" / "repo"), "same task")
                repo_b = self.make_thread("thread-b", str(Path(temp_dir) / "two" / "repo"), "same task")
                bot.bridge.scan_visible_sidebar_names = lambda: ["project-a", "repo", "same task2분"]
                bot.bridge.load_recent_threads = lambda limit=20: [prior_thread, repo_a, repo_b]

                with self.assertRaisesRegex(RuntimeError, "ambiguous|unmatched"):
                    bot.bridge.load_ui_visible_threads()
        finally:
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads

    async def test_sync_codex_mirror_does_not_cleanup_when_ui_visible_scope_fails(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_load_ui_visible_threads = bot.bridge.load_ui_visible_threads
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

                bot.bridge.load_ui_visible_threads = lambda: (_ for _ in ()).throw(
                    RuntimeError("ambiguous visible scope")
                )
                fake_bot = SimpleNamespace(
                    guild_id=1,
                    guilds=[],
                    user=None,
                    get_guild=lambda guild_id: FakeGuild(),
                )

                with self.assertRaisesRegex(RuntimeError, "ambiguous visible scope"):
                    await bot.sync_codex_mirror(fake_bot)

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    project_rows = conn.execute("SELECT project_key FROM mirror_projects").fetchall()
                    thread_rows = conn.execute("SELECT codex_thread_id FROM mirror_threads").fetchall()

            self.assertEqual(project_rows, [("project",)])
            self.assertEqual(thread_rows, [("thread-1",)])
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.load_ui_visible_threads = old_load_ui_visible_threads
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    async def test_sync_codex_mirror_does_not_cleanup_same_project_duplicate_titles(self) -> None:
        old_db_path = bot.MIRROR_DB_PATH
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads
        old_filter_mirrorable_threads = bot.filter_mirrorable_threads
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
                bot.bridge.scan_visible_sidebar_names = lambda: [
                    bot.bridge.get_thread_workspace_name(newer_thread),
                    "same task2분",
                ]
                bot.bridge.load_recent_threads = lambda limit=20: [newer_thread, older_thread]
                bot.filter_mirrorable_threads = lambda threads: list(threads)
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

                with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                    await bot.sync_codex_mirror(fake_bot)

                with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
                    rows = conn.execute(
                        "SELECT codex_thread_id FROM mirror_threads ORDER BY codex_thread_id"
                    ).fetchall()

            self.assertEqual(rows, [("thread-new",), ("thread-old",)])
        finally:
            bot.MIRROR_DB_PATH = old_db_path
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads
            bot.filter_mirrorable_threads = old_filter_mirrorable_threads
            if old_log_path is None:
                os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
            else:
                os.environ["CODEX_DISCORD_LOG_PATH"] = old_log_path

    def test_load_ui_visible_threads_rejects_unmatched_visible_rows(self) -> None:
        old_scan_visible_sidebar_names = bot.bridge.scan_visible_sidebar_names
        old_load_recent_threads = bot.bridge.load_recent_threads

        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                thread = self.make_thread("thread-1", str(Path(temp_dir)), "visible task")
                project_heading = bot.bridge.get_thread_workspace_name(thread)
                bot.bridge.scan_visible_sidebar_names = lambda: [
                    project_heading,
                    "visible task1시간",
                    "unknown task2분",
                ]
                bot.bridge.load_recent_threads = lambda limit=20: [thread]

                with self.assertRaisesRegex(RuntimeError, "unmatched"):
                    bot.bridge.load_ui_visible_threads()
        finally:
            bot.bridge.scan_visible_sidebar_names = old_scan_visible_sidebar_names
            bot.bridge.load_recent_threads = old_load_recent_threads

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

    def test_scan_visible_sidebar_names_uses_selected_codex_window_handle(self) -> None:
        old_find_codex_window = bot.bridge.find_codex_window
        old_focus_window = bot.bridge.focus_window
        old_subprocess_run = bot.bridge.subprocess.run
        captured: dict[str, object] = {}

        def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
            captured["command"] = command
            captured["creationflags"] = kwargs.get("creationflags")
            return SimpleNamespace(
                returncode=0,
                stdout="VISIBLE_NAME:dGFzaw==\n",
                stderr="",
            )

        try:
            bot.bridge.find_codex_window = lambda: bot.bridge.WindowInfo(
                hwnd=123456,
                title="Codex",
                left=0,
                top=0,
                right=1000,
                bottom=800,
            )
            bot.bridge.focus_window = lambda window: captured.setdefault("focused", window.hwnd)
            bot.bridge.subprocess.run = fake_run

            names = bot.bridge.scan_visible_sidebar_names()

            command = captured["command"]
            self.assertEqual(names, ["task"])
            self.assertEqual(captured["focused"], 123456)
            self.assertIsInstance(command, list)
            script = command[-1]
            self.assertIsInstance(script, str)
            self.assertIn("[IntPtr]123456", script)
            self.assertNotIn("*Codex*", script)
            self.assertIn("$rect.Width -gt 420", script)
            self.assertIn("$rect.Right -gt (Get-SidebarRightBoundary $WindowRect)", script)
            self.assertEqual(
                captured["creationflags"],
                getattr(bot.bridge.subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            bot.bridge.find_codex_window = old_find_codex_window
            bot.bridge.focus_window = old_focus_window
            bot.bridge.subprocess.run = old_subprocess_run


if __name__ == "__main__":
    unittest.main()
