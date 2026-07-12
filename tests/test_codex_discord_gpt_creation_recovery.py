from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from typing import final

import discord

import codex_discord_gpt_creation_journal as journal
import codex_discord_gpt_cursor as gpt_cursor
import codex_discord_gpt_discord_adapter as adapter
from codex_discord_gpt_ownership import CodexThreadId, DiscordChannelId


FakeDiscordError = RuntimeError
type Operation = journal.GptCreationOperation
type Snapshot = tuple[list[tuple[str, int, str]], list[tuple[str, str, int | None]]]
_TEMP_PREFIX = "app-gpt-discord-sync-todo-12-"
_MAPPING_SQL = "SELECT codex_thread_id,discord_thread_id,lifecycle_state FROM mirror_threads ORDER BY codex_thread_id"
_OPERATION_SQL = "SELECT codex_thread_id,status,discord_thread_id FROM gpt_chat_creation_ops ORDER BY codex_thread_id"
_CURSOR_SQL = "SELECT rollout_path,cursor FROM codex_session_mirror_offsets WHERE codex_thread_id='owner'"
_marker_name = journal.format_gpt_creation_thread_name
_load_ops = journal.load_gpt_creation_protections


@final
class FakeThread:
    def __init__(self, thread_id: int, name: str, parent: FakeTextChannel) -> None:
        self.id = thread_id
        self.name = name
        self.parent = parent
        self.parent_id = parent.id
        self.guild = parent.guild
        self.archived = False
        self.locked = False
        self.before_edit: Callable[[], None] = lambda: None

    async def edit(
        self,
        *,
        name: str | None = None,
        archived: bool | None = None,
        locked: bool | None = None,
        reason: str | None = None,
    ) -> FakeThread:
        _ = archived, locked, reason
        self.before_edit()
        if name is not None:
            self.name = name
        return self


@final
class FakeTextChannel:
    def __init__(self, channel_id: int, guild: FakeGuild) -> None:
        self.id = channel_id
        self.guild = guild
        self.threads: list[FakeThread] = []
        self.archived: list[FakeThread] = []
        self.archived_visits: list[int] = []
        self.scan_failure = False
        self.create_failure = False
        self.created = 0

    async def archived_threads(self, *, limit: int | None) -> AsyncIterator[FakeThread]:
        if limit is not None:
            raise AssertionError("archived scan must request full pagination")
        for index, thread in enumerate(self.archived):
            self.archived_visits.append(index)
            if self.scan_failure and index == 0:
                raise FakeDiscordError("incomplete")
            yield thread

    async def create_thread(
        self,
        *,
        name: str,
        type: discord.ChannelType,
        auto_archive_duration: int,
        reason: str | None = None,
    ) -> FakeThread:
        _ = type, auto_archive_duration, reason
        if self.create_failure:
            raise FakeDiscordError("create failed")
        self.created += 1
        return FakeThread(900 + self.created, name, self)


Text = FakeTextChannel


@final
class FakeGuild:
    def __init__(self) -> None:
        self.id = 1
        self.channels: dict[int, FakeTextChannel | FakeThread] = {}

    def get_channel(self, channel_id: int) -> FakeTextChannel | FakeThread | None:
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> FakeTextChannel | FakeThread:
        if channel_id not in self.channels:
            raise FakeDiscordError("missing")
        return self.channels[channel_id]


@final
class FakeClient:
    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self.guild if guild_id == self.guild.id else None

    async def fetch_guild(self, _guild_id: int) -> FakeGuild:
        return self.guild


def deps() -> adapter.GptDiscordAdapterDeps:
    return adapter.GptDiscordAdapterDeps(
        get_allowed_channel_ids=lambda: {10},
        get_startup_channel_id=lambda ids: 10 if ids == {10} else None,
        get_guild_id=lambda: 1,
        discord_failure_types=(FakeDiscordError,),
        scan_timeout_seconds=1.0,
    )


class GptCreationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def make_db(self, root: str) -> Path:
        return Path(root) / "recovery.sqlite"

    def started(self, db_path: Path, owner: str = "owner") -> Operation:
        intent = journal.GptCreationIntent(
            CodexThreadId(owner), "Original title", DiscordChannelId(10)
        )
        return journal.mark_gpt_creation_started(
            db_path, journal.prepare_gpt_creation(db_path, intent)
        )

    def snapshot(self, db_path: Path) -> Snapshot:
        with closing(sqlite3.connect(db_path)) as conn:
            mappings = conn.execute(_MAPPING_SQL).fetchall()
            operations = conn.execute(_OPERATION_SQL).fetchall()
        return mappings, operations

    async def test_one_exact_marker_atomically_links_activates_renames_then_removes_journal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as root:
            db_path = self.make_db(root)
            operation = self.started(db_path)
            rollout = Path(root) / "rollout.jsonl"
            _ = rollout.write_text('{"type":"event"}\n', encoding="utf-8")
            guild = FakeGuild()
            channel = FakeTextChannel(10, guild)
            marker = FakeThread(77, _marker_name(operation), channel)
            channel.threads = [FakeThread(69, "active ordinary", channel)]
            channel.archived = [
                FakeThread(70, "ordinary", channel),
                FakeThread(71, "ordinary", channel),
                marker,
            ]
            guild.channels = {10: channel, 77: marker}
            order: list[str] = []
            client, configured = FakeClient(guild), deps()

            def finalize_cursor(current: Operation) -> None:
                order.append("cursor")
                _ = gpt_cursor.establish_reactivation_cursor(
                    gpt_cursor.GptCursorRequest(
                        db_path, current.codex_thread_id, rollout
                    )
                )

            def before_edit() -> None:
                order.append("rename")
                mappings, operations = self.snapshot(db_path)
                self.assertEqual(mappings, [("owner", 77, "active")])
                self.assertEqual(operations, [("owner", "discord_identified", 77)])
                if order.count("rename") == 1:
                    raise FakeDiscordError("rename failed")

            marker.before_edit = before_edit
            request = adapter.GptCreationRecoveryRequest(
                db_path, operation, "Final display", finalize_cursor
            )
            with patch.multiple(discord, TextChannel=Text, Thread=FakeThread):
                with self.assertRaises(adapter.GptDiscordRenameError):
                    _ = await adapter.recover_gpt_creation(client, request, configured)
                identified = _load_ops(db_path).unfinished[0]
                retry = adapter.GptCreationRecoveryRequest(
                    db_path, identified, "Final display", finalize_cursor
                )
                result = await adapter.recover_gpt_creation(client, retry, configured)

            self.assertIs(result, marker)
            self.assertEqual(order, ["cursor", "rename", "rename"])
            self.assertEqual(marker.name, "Final display")
            self.assertEqual(channel.archived_visits, [0, 1, 2])
            self.assertEqual(self.snapshot(db_path), ([("owner", 77, "active")], []))
            with closing(sqlite3.connect(db_path)) as conn:
                cursor_rows: list[tuple[str, int]] = conn.execute(
                    _CURSOR_SQL
                ).fetchall()
            self.assertEqual(cursor_rows[0], (str(rollout), rollout.stat().st_size))

    async def test_config_access_type_incomplete_zero_many_and_identity_conflict_do_not_mutate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as root:
            db_path = self.make_db(root)
            operation = self.started(db_path)
            guild = FakeGuild()
            channel = FakeTextChannel(10, guild)
            guild.channels[10] = channel
            client, configured = FakeClient(guild), deps()
            recover = adapter.recover_gpt_creation
            create = adapter.create_gpt_marker_thread
            request = adapter.GptCreationRecoveryRequest(
                db_path, operation, "Final", lambda _current: None
            )
            before = self.snapshot(db_path)
            with patch.multiple(discord, TextChannel=Text, Thread=FakeThread):
                marker_name = _marker_name(operation)
                for mode in ("zero", "many", "incomplete"):
                    channel.archived = (
                        []
                        if mode == "zero"
                        else [
                            FakeThread(77, marker_name, channel),
                            FakeThread(78, marker_name, channel),
                        ]
                    )
                    channel.scan_failure = mode == "incomplete"
                    error = adapter.GptDiscordRecoveryError
                    if mode == "incomplete":
                        error = adapter.GptDiscordScanError
                    with self.subTest(mode=mode), self.assertRaises(error):
                        _ = await recover(client, request, configured)
                    self.assertEqual(self.snapshot(db_path), before)
                    self.assertEqual(channel.created, 0)

                channel.scan_failure = False
                channel.archived = []
                channel.create_failure = True
                with self.assertRaises(adapter.GptDiscordCreateError):
                    _ = await create(client, operation, configured)
                self.assertEqual(self.snapshot(db_path), before)
                self.assertEqual(channel.created, 0)
                channel.create_failure = False
                created = await create(client, operation, configured)
                self.assertEqual(
                    (channel.created, created.name),
                    (1, marker_name),
                )
                marker = FakeThread(77, marker_name, channel)
                channel.archived = [marker]
                with closing(sqlite3.connect(db_path)) as conn:
                    _ = conn.execute(
                        "INSERT INTO mirror_threads VALUES ('owner', 'codex:chats', 'Wrong title', 10, 77, 1, 'gpt_chat', 'reactivating')"
                    )
                    conn.commit()
                conflict_before = self.snapshot(db_path)
                with self.assertRaises(journal.GptCreationAmbiguityError):
                    _ = await recover(client, request, configured)
                self.assertEqual(self.snapshot(db_path), conflict_before)
                self.assertEqual(marker.name, marker_name)
                for mutation in ("discord_channel_id=11", "discord_thread_id=88"):
                    with closing(sqlite3.connect(db_path)) as conn:
                        script = (
                            "UPDATE mirror_threads SET thread_title='Original title', discord_channel_id=10, discord_thread_id=77;"
                            + f"UPDATE mirror_threads SET {mutation};"
                        )
                        _ = conn.executescript(script)
                    conflict_before = self.snapshot(db_path)
                    with self.assertRaises(journal.GptCreationAmbiguityError):
                        _ = await recover(client, request, configured)
                    self.assertEqual(self.snapshot(db_path), conflict_before)
