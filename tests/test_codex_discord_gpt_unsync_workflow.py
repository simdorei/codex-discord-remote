from __future__ import annotations

import asyncio  # noqa: F401  # noqa: ANYIO_OK
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from typing import final, override
import unittest
from unittest.mock import patch

import anyio
import discord

import codex_discord_gpt_discord_adapter as adapter
import codex_discord_gpt_snapshots as snapshots
import codex_discord_gpt_unsync_workflow as workflow
from codex_discord_store_schema import init_store_schema


_MAP_SQL = "INSERT INTO mirror_threads VALUES(?,'codex:chats',?,?,?,1,'gpt_chat',?)"
_CURSOR_SQL = "INSERT INTO codex_session_mirror_offsets VALUES (?, ?, 12, 1)"
_OP_SQL = "INSERT INTO gpt_chat_creation_ops VALUES(?,'codex:chats',?,10,?,?,?,1,1)"
_MAPPINGS_SQL = "SELECT codex_thread_id,discord_thread_id,lifecycle_state FROM mirror_threads ORDER BY 1"
_JOURNALS_SQL = "SELECT codex_thread_id,status FROM gpt_chat_creation_ops ORDER BY 1"
_C_SQL = "SELECT codex_thread_id,cursor FROM codex_session_mirror_offsets ORDER BY 1"
type _MappingRow = tuple[str, int, str]
type _Rows = tuple[list[_MappingRow], list[tuple[str, str]], list[tuple[str, int]]]


class FakeDiscordError(RuntimeError):
    pass


@final
class FakeThread:
    def __init__(self, thread_id: int, name: str, parent: FakeTextChannel) -> None:
        self.id, self.name = thread_id, name
        self.parent_id, self.guild = parent.id, parent.guild
        self.archived = self.locked = self.fail_edit = False
        self.history, self.edit_count = [f"history-{thread_id}"], 0
        self.started, self.release = anyio.Event(), anyio.Event()
        self.release.set()

    async def edit(
        self,
        *,
        name: str | None = None,
        archived: bool | None = None,
        locked: bool | None = None,
        reason: str | None = None,
    ) -> FakeThread:
        assert reason in (None, "Deactivate GPT sync")
        self.edit_count += 1
        self.started.set()
        await self.release.wait()
        if self.fail_edit:
            raise FakeDiscordError("edit failed")
        self.name = self.name if name is None else name
        self.archived = self.archived if archived is None else archived
        self.locked = self.locked if locked is None else locked
        return self


@final
class FakeTextChannel:
    def __init__(self, channel_id: int, guild: FakeGuild) -> None:
        self.id, self.guild = channel_id, guild
        self.threads: list[FakeThread] = []
        self.archived: list[FakeThread] = []
        self.scan_failure = False

    async def archived_threads(self, *, limit: int | None) -> AsyncIterator[FakeThread]:
        if limit is not None:
            raise AssertionError("marker scan must be complete")
        for index, thread in enumerate(self.archived):
            if self.scan_failure and index == 0:
                raise FakeDiscordError("incomplete scan")
            yield thread


@final
class FakeGuild:
    def __init__(self) -> None:
        self.id = 1
        self.channels: dict[int, FakeTextChannel | FakeThread] = {}

    def get_channel(self, channel_id: int) -> FakeTextChannel | FakeThread | None:
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> FakeTextChannel | FakeThread:
        return self.channels[channel_id]

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self if guild_id == self.id else None

    async def fetch_guild(self, _guild_id: int) -> FakeGuild:
        return self


_DISCORD_DEPS = adapter.GptDiscordAdapterDeps(
    get_allowed_channel_ids=lambda: {10},
    get_startup_channel_id=lambda ids: 10 if ids == {10} else None,
    get_guild_id=lambda: 1,
    discord_failure_types=(FakeDiscordError,),
)


class GptUnsyncWorkflowTests(unittest.IsolatedAsyncioTestCase):
    db_path: Path = Path()

    @override
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="app-gpt-discord-sync-todo-14-")
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "mirror.sqlite"
        with closing(sqlite3.connect(self.db_path)) as conn:
            init_store_schema(conn)

    def mapping(self, owner: str, thread_id: int, state: str, parent: int = 10) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            _ = conn.execute(_MAP_SQL, (owner, owner, parent, thread_id, state))
            _ = conn.execute(_CURSOR_SQL, (owner, f"redacted-{owner}"))

    def journal(self, who: str, state: str, key: str, tid: int | None = None) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            _ = conn.execute(_OP_SQL, (who, who, key, state, tid))

    def rows(self) -> _Rows:
        with closing(sqlite3.connect(self.db_path)) as conn:
            mappings = conn.execute(_MAPPINGS_SQL).fetchall()
            journals = conn.execute(_JOURNALS_SQL).fetchall()
            cursors = conn.execute(_C_SQL).fetchall()
        return mappings, journals, cursors

    def subject(
        self,
        guild: FakeGuild,
        snapshot_store: snapshots.GptSnapshotStore,
        lock: anyio.Lock,
        finalized: list[workflow.GptCreationOperation],
    ) -> workflow.GptUnsyncWorkflow:
        return workflow.GptUnsyncWorkflow(
            self.db_path,
            workflow.GptUnsyncWorkflowDeps(
                configured_channel_lock=lock,
                snapshot_store=snapshot_store,
                discord_client=guild,
                discord_deps=_DISCORD_DEPS,
                finalize_cursor=finalized.append,
            ),
        )

    async def test_unsync_and_clear_converge_exact_ids_to_inactive(self) -> None:
        # Given: a synced snapshot, legacy parents, all states, and every recoverable journal form.
        guild, saved, lock = FakeGuild(), snapshots.GptSnapshotStore(), anyio.Lock()
        general = FakeTextChannel(10, guild)
        guild.channels[10] = general
        threads: dict[str, FakeThread] = {}
        self.mapping("unsync-a", 101, "active", 55)
        thread = FakeThread(101, "unsync-a", FakeTextChannel(55, guild))
        threads["unsync-a"], guild.channels[101] = thread, thread
        key = snapshots.GptSnapshotKey(1, 10, 7)
        _ = saved.replace(key, snapshots.GptSnapshotKind.SYNCED, ("unsync-a",))
        finalized: list[workflow.GptCreationOperation] = []
        subject = self.subject(guild, saved, lock, finalized)
        with patch.multiple(discord, TextChannel=FakeTextChannel, Thread=FakeThread):
            # When: unsync consumes the whole saved selection.
            _ = await subject.unsync(key, "1")
            for owner, thread_id, state, parent in (
                ("active", 201, "active", 55),
                ("deactivating", 202, "deactivating", 10),
                ("reactivating", 203, "reactivating", 10),
                ("inactive", 204, "inactive", 10),
                ("identified", 205, "reactivating", 10),
            ):
                self.mapping(owner, thread_id, state, parent)
                thread = FakeThread(thread_id, owner, FakeTextChannel(parent, guild))
                threads[owner], guild.channels[thread_id] = thread, thread
            self.journal("prepared", "prepared", "a" * 32)
            self.journal("zero", "create_started", "b" * 32)
            self.journal("one", "create_started", "c" * 32)
            self.journal("identified", "discord_identified", "d" * 32, 205)
            marker = FakeThread(206, "[gpt-sync:" + "c" * 32 + "] one", general)
            threads["one"], guild.channels[206] = marker, marker
            general.archived = [marker]
            # When: clear reduces an over-capacity mixed state without a snapshot.
            _ = await subject.sync_clear()

        # Then: exact IDs are archived+locked, audit state is preserved, and journals are gone.
        mappings, journals, cursors = self.rows()
        assert all(state == "inactive" for _, _, state in mappings)
        assert journals == [] and {cursor for _, cursor in cursors} == {12}
        assert [item.codex_thread_id for item in finalized] == ["identified", "one"]
        selection = saved.get(key, snapshots.GptSnapshotKind.SYNCED)
        assert selection.codex_thread_ids == ("unsync-a",)
        assert threads["inactive"].edit_count == 0
        changed = [thread for owner, thread in threads.items() if owner != "inactive"]
        assert all(thread.archived and thread.locked for thread in changed)
        assert threads["active"].parent_id == 55
        assert threads["identified"].name == "identified"
        assert threads["one"].name == "one"

    async def test_all_null_id_and_transitional_failures_are_retryable_and_preserved(
        self,
    ) -> None:
        # Given: one invalid snapshot selection and an active exact thread.
        guild, saved, lock = FakeGuild(), snapshots.GptSnapshotStore(), anyio.Lock()
        general = FakeTextChannel(10, guild)
        guild.channels[10] = general
        self.mapping("kept", 301, "active")
        kept = FakeThread(301, "kept", general)
        guild.channels[301] = kept
        key = snapshots.GptSnapshotKey(1, 10, 7)
        _ = saved.replace(key, snapshots.GptSnapshotKind.SYNCED, ("kept", "missing"))
        subject = self.subject(guild, saved, lock, [])
        before = self.rows()
        with patch.multiple(discord, TextChannel=FakeTextChannel, Thread=FakeThread):
            # When/Then: whole-selection preflight rejects before any mutation.
            with self.assertRaises(workflow.GptUnsyncPreflightError):
                _ = await subject.unsync(key, "1,2")
            self.assertEqual((self.rows(), kept.edit_count), (before, 0))
            self.journal("ambiguous", "create_started", "e" * 32)
            general.archived = [
                FakeThread(302, "[gpt-sync:" + "e" * 32 + "] first", general),
                FakeThread(303, "[gpt-sync:" + "e" * 32 + "] second", general),
            ]
            ambiguous_before = self.rows()
            with self.assertRaises(workflow.GptClearJournalError):
                _ = await subject.sync_clear()
            self.assertEqual(self.rows(), ambiguous_before)
            general.scan_failure = True
            with self.assertRaises(adapter.GptDiscordScanError):
                _ = await subject.sync_clear()
            self.assertEqual(self.rows(), ambiguous_before)
            # Given/When/Then: every mutable state retains retryable audit data on Discord failure.
            general.scan_failure, general.archived, kept.fail_edit = False, [], True
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                _ = conn.execute("DELETE FROM gpt_chat_creation_ops")
            for state in ("active", "deactivating", "reactivating"):
                with self.subTest(state=state):
                    with closing(sqlite3.connect(self.db_path)) as conn, conn:
                        _ = conn.execute(
                            "UPDATE mirror_threads SET lifecycle_state=? WHERE codex_thread_id='kept'",
                            (state,),
                        )
                    kept.started, kept.release = anyio.Event(), anyio.Event()
                    task = asyncio.create_task(subject.sync_clear())
                    await kept.started.wait()
                    self.assertTrue(lock.locked())
                    kept.release.set()
                    with self.assertRaises(workflow.GptDiscordArchiveError):
                        _ = await task
                    expected: _Rows
                    expected = ([("kept", 301, "deactivating")], [], [("kept", 12)])
                    assert self.rows() == expected
                    assert kept.history == ["history-301"]

    async def test_unsync_retries_deactivating_saved_selection(self) -> None:
        # Given: one saved active mapping and a no-sleep Discord edit barrier.
        guild, saved, lock = FakeGuild(), snapshots.GptSnapshotStore(), anyio.Lock()
        general = FakeTextChannel(10, guild)
        guild.channels[10] = general
        self.mapping("retry", 401, "active", 55)
        thread = FakeThread(401, "retry", FakeTextChannel(55, guild))
        guild.channels[401] = thread
        key = snapshots.GptSnapshotKey(1, 10, 7)
        _ = saved.replace(key, snapshots.GptSnapshotKind.SYNCED, ("retry",))
        subject = self.subject(guild, saved, lock, [])
        thread.fail_edit, thread.release = True, anyio.Event()
        with patch.multiple(discord, TextChannel=FakeTextChannel, Thread=FakeThread):
            # When: the first exact-ID archive fails after durable deactivation begins.
            task = asyncio.create_task(subject.unsync(key, "1"))
            await thread.started.wait()
            self.assertTrue(lock.locked())
            thread.release.set()
            with self.assertRaises(workflow.GptDiscordArchiveError):
                _ = await task
            failed = self.rows()
            thread.fail_edit = False
            # When: the same saved SYNCED selection is retried.
            _ = await subject.unsync(key, "1")

        # Then: audit identity survives and that same Discord ID converges to inactive.
        expected: _Rows = ([("retry", 401, "deactivating")], [], [("retry", 12)])
        assert failed == expected
        assert self.rows() == ([("retry", 401, "inactive")], [], [("retry", 12)])
        assert thread.id == 401
        assert thread.parent_id == 55
        assert thread.history == ["history-401"]
        selection = saved.get(key, snapshots.GptSnapshotKind.SYNCED)
        assert selection.codex_thread_ids == ("retry",)
        assert thread.archived and thread.locked and thread.edit_count == 2
