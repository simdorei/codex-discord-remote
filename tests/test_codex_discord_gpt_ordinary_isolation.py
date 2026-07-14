from __future__ import annotations

import gc
import sqlite3
import tempfile
import unittest
from collections.abc import AsyncIterator, Mapping
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Never, override

import discord

import codex_discord_mirror_channel_store as channel_store
import codex_discord_mirror_runtime as mirror_runtime
import codex_discord_mirror_single_thread as single_thread
import codex_discord_mirror_status_queries as status_queries
import codex_discord_mirror_status_runtime as status_runtime
import codex_discord_mirror_thread_channels as thread_channels
import codex_discord_new_thread_flow as new_thread_flow
import codex_discord_project_paths as project_paths
import codex_discord_store as discord_store
from codex_discord_project_types import BridgeProjectModule, JsonValue, ProjectThread
from codex_discord_store_schema import init_store_schema
from codex_thread_models import ThreadInfo
from tests.test_codex_discord_mirror_status import FakeMirrorBridge

GPT_KEY = "codex:chats"
ORDINARY_KEY = r"C:\repo"
STATES = ("active", "inactive", "deactivating", "reactivating")


class FakeBridge:
    GLOBAL_STATE_PATH: Path = Path("state.json")

    @staticmethod
    def strip_windows_extended_prefix(path: str) -> str:
        return path

    @staticmethod
    def normalize_workspace_path(path: str) -> str:
        return path.lower()

    @staticmethod
    def get_thread_workspace_name(thread: ProjectThread) -> str:
        _ = thread
        return "workspace"

    @staticmethod
    def load_json(path: Path) -> Mapping[str, JsonValue]:
        _ = path
        return {}

    @staticmethod
    def choose_thread(thread_id: str, cwd: str | None) -> ProjectThread:
        _ = thread_id, cwd
        return _thread("chosen", ORDINARY_KEY)


class FakeThread(discord.Thread):
    calls: list[str] = []

    @override
    async def edit(self, **_kwargs: object) -> Never:
        self.calls.append("edit")
        raise AssertionError("unexpected Discord thread edit")


class StoredChannelUnavailableError(RuntimeError):
    pass


class FakeGuild(discord.Guild):
    @override
    def get_thread(self, _thread_id: int) -> discord.Thread | None:
        return None

    @override
    async def fetch_channel(self, _channel_id: int) -> Never:
        raise StoredChannelUnavailableError("Unknown Channel")


class FakeProjectChannel(discord.TextChannel):
    active_threads: list[discord.Thread] = []
    archived_thread: FakeThread | None = None
    calls: list[str] = []

    @property
    @override
    def threads(self) -> list[discord.Thread]:
        return list(self.active_threads)

    @override
    async def archived_threads(self, *, private: bool = False, joined: bool = False, limit: int | None = 100, before: discord.abc.Snowflake | datetime | None = None) -> AsyncIterator[discord.Thread]:
        _ = private, joined, limit, before
        if self.archived_thread is not None:
            yield self.archived_thread
        else:
            self.calls.append("fallback")

    @override
    async def create_thread(self, **_kwargs: object) -> Never:
        self.calls.append("create")
        raise AssertionError("unexpected Discord thread create")


class RuntimeBot:
    def get_channel(self, channel_id: int) -> None:
        _ = channel_id
        return None

    async def fetch_channel(self, channel_id: int) -> Never:
        raise AssertionError(f"unexpected channel fetch: {channel_id}")


def _fake_thread(thread_id: int, name: str, parent_id: int = 22, calls: list[str] | None = None) -> FakeThread:
    thread = FakeThread.__new__(FakeThread)
    thread.id, thread.name, thread.parent_id, thread.calls = thread_id, name, parent_id, calls if calls is not None else []
    return thread


def _fake_channel(candidate: FakeThread, *, archived: bool, calls: list[str] | None = None) -> FakeProjectChannel:
    channel = FakeProjectChannel.__new__(FakeProjectChannel)
    channel.id, channel.guild, channel.active_threads, channel.archived_thread, channel.calls = 22, FakeGuild.__new__(FakeGuild), ([] if archived else [candidate]), (candidate if archived else None), calls if calls is not None else []
    return channel


def _thread(thread_id: str, cwd: str) -> ThreadInfo:
    return ThreadInfo(thread_id, "same-title", cwd, 1, f"{thread_id}.jsonl", "gpt", "high", 1)


def _deps(db_path: Path) -> channel_store.MirrorChannelDeps:
    return channel_store.MirrorChannelDeps(db_path, lambda key: str(key or "").lower(), lambda left, right: left == right, lambda _thread_id, _thread_info: "same-title", lambda _message: None, (RuntimeError,))


def _insert_owner(conn: sqlite3.Connection, codex_id: str, discord_id: int, state: str, *, managed_by: str = "gpt_chat", project_key: str = GPT_KEY) -> None:
    with conn, closing(
        conn.execute(
            "INSERT INTO mirror_threads (codex_thread_id, project_key, thread_title, "
            + "discord_channel_id, discord_thread_id, updated_at, managed_by, lifecycle_state) "
            + "VALUES (?, ?, 'same-title', 22, ?, 1.0, ?, ?)",
            (codex_id, project_key, discord_id, managed_by, state),
        )
    ):
        pass


def _owner(db_path: Path, codex_id: str) -> discord_store.MirrorThreadOwnership | None:
    return discord_store.get_mirror_thread_owner_by_codex_thread_id(db_path, codex_id)


class GptOrdinaryIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_projects_keep_existing_behavior(self) -> None:
        bridge: BridgeProjectModule = FakeBridge()
        generic = _thread("generic", "")
        filesystem = _thread("filesystem", ORDINARY_KEY)
        self.assertTrue(project_paths.is_thread_mirrorable(generic, bridge_module=bridge, projectless_chat_key=GPT_KEY))
        for key in ("projectless:alpha", "projectless:beta", "projectless:multi:segment"):
            self.assertEqual(project_paths.normalize_project_key(key, bridge_module=bridge, projectless_chat_key=GPT_KEY), key)

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ordinary.sqlite"
            deps = _deps(db_path)
            channel_store.upsert_mirror_project(ORDINARY_KEY, "repo", 22, deps=deps)
            channel_store.upsert_mirror_thread(filesystem, ORDINARY_KEY, "title", 22, 33, deps=deps)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = status_queries.load_mirror_check_rows(conn, None)
            self.assertEqual([row["codex_thread_id"] for row in rows], ["filesystem"])
            _ = gc.collect()

    async def test_every_codex_chats_bypass_is_closed(self) -> None:
        bridge: BridgeProjectModule = FakeBridge()
        gpt_thread, ordinary_thread = _thread("gpt-source", GPT_KEY), _thread("ordinary", ORDINARY_KEY)
        self.assertFalse(project_paths.is_thread_mirrorable(gpt_thread, bridge_module=bridge, projectless_chat_key=GPT_KEY))

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "isolation.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                init_store_schema(conn)
                conn.row_factory = sqlite3.Row
                _insert_owner(conn, "gpt-source", 40, "inactive")
                _insert_owner(conn, "ordinary", 41, "active", managed_by="ordinary", project_key=ORDINARY_KEY)
                row_sets = (status_queries.load_mirror_list_rows(conn, 10, None), status_queries.load_mirror_list_rows(conn, 10, ["gpt-source", "ordinary"]), status_queries.load_mirror_check_rows(conn, None), status_queries.load_mirror_check_rows(conn, {GPT_KEY, ORDINARY_KEY}))
                for rows in row_sets:
                    self.assertEqual([row["codex_thread_id"] for row in rows], ["ordinary"])

                status_deps = status_runtime.MirrorStatusRuntimeDeps(
                    db_path=db_path,
                    init_mirror_db=lambda: None,
                    get_mirror_status_bridge_module=lambda: FakeMirrorBridge(),
                    load_mirror_scope_threads=lambda _limit: [gpt_thread, ordinary_thread],
                    load_mirror_check_scope_threads=lambda _limit: [ordinary_thread],
                    filter_threads_for_discord_channel=lambda items, _channel_id: items,
                    filter_mirrorable_threads=lambda items: items,
                    filter_app_server_available_threads=lambda items: items,
                    get_project_key=lambda item: str(item.cwd or ""),
                    get_project_name=lambda item: item.title,
                )
                _, scoped_ids = status_runtime.resolve_mirror_list_scope(None, deps=status_deps)
                self.assertEqual(scoped_ids, ["ordinary"])

                deps = _deps(db_path)
                with self.assertRaises(project_paths.GptChatOrdinaryMirrorError):
                    channel_store.upsert_mirror_project(GPT_KEY, "chat", 22, deps=deps)
                with self.assertRaises(project_paths.GptChatOrdinaryMirrorError):
                    _ = channel_store.find_mirror_project_row_by_key(GPT_KEY, deps=deps)
                with self.assertRaises(project_paths.GptChatOrdinaryMirrorError):
                    channel_store.upsert_mirror_thread(gpt_thread, GPT_KEY, "title", 22, 42, deps=deps)

                runtime_events: list[str] = []
                runtime = _runtime(db_path, [gpt_thread, ordinary_thread], runtime_events)
                _ = await runtime.sync_codex_mirror(RuntimeBot(), limit=2)
                self.assertEqual(runtime_events, ["load:2", f"filter:{ORDINARY_KEY}", f"project:{ORDINARY_KEY}", f"thread:{ORDINARY_KEY}"])
                original_bridge_thread = FakeMirrorBridge.thread
                FakeMirrorBridge.thread = ordinary_thread
                try:
                    list_outputs = (runtime.build_mirror_list(10), status_runtime.build_mirror_list(10, deps=status_deps))
                    check_outputs = (runtime.build_mirror_check(10), status_runtime.build_mirror_check(10, deps=status_deps))
                finally:
                    FakeMirrorBridge.thread = original_bridge_thread
                for output in (*list_outputs, *check_outputs):
                    self.assertNotIn("gpt-source", output)
                self.assertTrue(all("ordinary" in output for output in list_outputs))
                self.assertTrue(all("codex_threads: 1" in output and "mirrored_threads: 1" in output for output in check_outputs))

                single_events: list[str] = []

                async def no_discord(_bot: RuntimeBot) -> Never:
                    single_events.append("discord")
                    raise AssertionError("ordinary Discord path reached")

                bot = RuntimeBot()
                single_deps = single_thread.MirrorSingleThreadDeps[RuntimeBot](no_discord, lambda _guild: no_discord(bot), lambda _id, _ref: gpt_thread, lambda _item: GPT_KEY, lambda _item: "chat", lambda _key, _name, _id: single_events.append("upsert"), lambda _guild, _category, _key, _name: no_discord(bot), lambda _item, _key, _channel: no_discord(bot), (RuntimeError,), lambda _message: None)
                with self.assertRaises(project_paths.GptChatOrdinaryMirrorError):
                    _ = await single_thread.mirror_single_codex_thread(bot, "gpt-source", deps=single_deps)
                self.assertEqual(single_events, [])

                new_events: list[str] = []

                async def mirror_new(bot: RuntimeBot, codex_thread_id: str, *, preferred_project_channel_id: int | None = None) -> FakeThread:
                    _ = bot, codex_thread_id
                    new_events.append(str(preferred_project_channel_id))
                    return _fake_thread(88, "new")

                flow_deps = new_thread_flow.NewThreadFlowDeps[RuntimeBot, ThreadInfo, FakeThread](lambda _channel_id: None, lambda _argv: (0, "target_thread: gpt-source"), lambda _output, key: "gpt-source" if key == "target_thread" else None, lambda _id, _ref: gpt_thread, lambda _item: GPT_KEY, lambda _channel_id, _key: new_events.append("resolve") or None, mirror_new, lambda _thread_value, _thread_id: no_discord(bot), (RuntimeError,), lambda _message: None)
                exit_code, output = await new_thread_flow.run_discord_new_thread(bot, 22, "prompt", deps=flow_deps)
                self.assertEqual((exit_code, new_events), (0, []))
                self.assertIn("excluded from ordinary Discord mirroring", output)

                for archived in (False, True):
                    for index, state in enumerate(STATES):
                        incoming_id, discord_id = f"ordinary-{archived}-{state}", 100 + int(archived) * 10 + index
                        _insert_owner(conn, incoming_id, 500 + discord_id, "active", managed_by="ordinary", project_key=ORDINARY_KEY)
                        _insert_owner(conn, f"gpt-{archived}-{state}", discord_id, state)
                        candidate, channel = _fake_thread(discord_id, "same-title"), _fake_channel(_fake_thread(discord_id, "same-title"), archived=archived)
                        before = (_owner(db_path, incoming_id), _owner(db_path, f"gpt-{archived}-{state}"))
                        with self.subTest(path="archived" if archived else "active", state=state):
                            with self.assertRaises(discord_store.GptOwnershipOverwriteError):
                                _ = await thread_channels.ensure_mirror_thread_channel(ordinary_thread, ORDINARY_KEY, channel, candidate, "renamed", deps=deps)
                            with self.assertRaises(discord_store.GptOwnershipOverwriteError):
                                _ = await thread_channels.get_or_create_thread_channel(_thread(incoming_id, ORDINARY_KEY), ORDINARY_KEY, channel, deps=deps)
                            self.assertEqual((candidate.name, _owner(db_path, incoming_id), _owner(db_path, f"gpt-{archived}-{state}")), ("same-title", *before))

                for archived in (False, True):
                    for index, state in enumerate(STATES):
                        suffix, discord_id = f"{archived}-{state}", 300 + int(archived) * 10 + index
                        _insert_owner(conn, f"duplicate-gpt-{suffix}", discord_id, state)
                        _insert_owner(conn, f"duplicate-ordinary-{suffix}", discord_id, "active", managed_by="ordinary", project_key=ORDINARY_KEY)
                        calls: list[str] = []
                        candidate = _fake_thread(discord_id, "same-title", calls=calls)
                        channel = _fake_channel(candidate, archived=archived, calls=calls)
                        before = (_owner(db_path, f"duplicate-gpt-{suffix}"), _owner(db_path, f"duplicate-ordinary-{suffix}"))
                        with self.subTest(duplicate_path="archived" if archived else "active", state=state):
                            with self.assertRaises(discord_store.DiscordOwnershipConflictError):
                                _ = await thread_channels.get_or_create_thread_channel(_thread(f"duplicate-target-{suffix}", ORDINARY_KEY), ORDINARY_KEY, channel, deps=deps)
                            self.assertEqual((calls, candidate.name, _owner(db_path, f"duplicate-gpt-{suffix}"), _owner(db_path, f"duplicate-ordinary-{suffix}")), ([], "same-title", *before))
            _ = gc.collect()


def _unreachable() -> Never:
    raise AssertionError("unused dependency reached")


def _runtime(db_path: Path, threads: list[ThreadInfo], events: list[str]) -> mirror_runtime.MirrorRuntime[RuntimeBot, str, str, str, str]:
    class ScopeBridge:
        def load_user_root_threads(self) -> list[ThreadInfo]:
            return list(threads)

        def load_recent_threads(self, limit: int = 20) -> list[ThreadInfo]:
            return list(threads[:limit])

        def filter_thread_list_for_target(
            self,
            items: list[ThreadInfo],
            target_thread_id: str,
            cwd: str | None,
        ) -> list[ThreadInfo]:
            _ = cwd
            return [item for item in items if item.id == target_thread_id]

    def load(limit: int | None) -> list[ThreadInfo]:
        events.append(f"load:{limit}")
        return threads

    def mirrorable(items: list[ThreadInfo]) -> list[ThreadInfo]:
        events.extend(f"filter:{item.cwd}" for item in items)
        return items

    async def guild(_bot: RuntimeBot) -> str:
        return "guild"

    async def category(_guild: str) -> str:
        return "category"

    async def project(_guild: str, _category: str, key: str, _name: str) -> str:
        events.append(f"project:{key}")
        return key

    async def thread(_item: ThreadInfo, key: str, _project: str) -> str:
        events.append(f"thread:{key}")
        return key

    async def unused_sync(bot: RuntimeBot, *, limit: int | None = None) -> str:
        _ = bot
        _ = limit
        return "unused"

    deps = mirror_runtime.MirrorRuntimeDeps[RuntimeBot, str, str, str, str](
        lambda: db_path, lambda: ScopeBridge(), load, lambda items, _channel_id: items, mirrorable, lambda items: items,
        guild, category, lambda _id, _ref: threads[0], lambda item: str(item.cwd or ""), lambda item: item.title,
        lambda _key, _name, _id: None, project, thread, lambda _id: None, lambda _id: None,
        lambda left, right: left == right, _unreachable, unused_sync, lambda _bot: None, lambda: None,
        lambda: FakeMirrorBridge(), (RuntimeError,), lambda _message: None,
    )
    return mirror_runtime.MirrorRuntime(deps)


if __name__ == "__main__":
    _ = unittest.main()
