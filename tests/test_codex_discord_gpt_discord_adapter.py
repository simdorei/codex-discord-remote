from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from typing import TypeAlias, final
import unittest
from unittest.mock import patch

import discord

import codex_discord_gpt_discord_adapter as adapter
from codex_discord_gpt_ownership import (
    CodexThreadId,
    DiscordChannelId,
    DiscordThreadId,
    MirrorThreadLifecycleState,
    MirrorThreadManagedBy,
    MirrorThreadOwnership,
    get_mirror_thread_owner_by_codex_thread_id,
)
from codex_discord_store_schema import init_store_schema


FakeDiscordError = RuntimeError


@final
class FakeWrongChannel:
    def __init__(self, channel_id: int, guild: FakeGuild) -> None:
        self.id = channel_id
        self.guild = guild


@final
class FakeThread:
    def __init__(self, thread_id: int, name: str, parent: FakeTextChannel) -> None:
        self.id = thread_id
        self.name = name
        self.parent = parent
        self.parent_id = parent.id
        self.guild = parent.guild
        self.archived = True
        self.locked = True
        self.history = ["first", "second"]
        self.edit_failure = False
        self.edit_calls: list[tuple[bool | None, bool | None]] = []

    async def edit(
        self,
        *,
        name: str | None = None,
        archived: bool | None = None,
        locked: bool | None = None,
        reason: str | None = None,
    ) -> FakeThread:
        _ = reason
        self.edit_calls.append((archived, locked))
        if self.edit_failure:
            raise FakeDiscordError("edit failed")
        if name is not None:
            self.name = name
        if archived is not None:
            self.archived = archived
        if locked is not None:
            self.locked = locked
        return self


@final
class FakeTextChannel:
    def __init__(self, channel_id: int, guild: FakeGuild) -> None:
        self.id = channel_id
        self.guild = guild
        self.threads: list[FakeThread] = []
        self.created_names: list[str] = []

    async def archived_threads(self, *, limit: int | None) -> AsyncIterator[FakeThread]:
        _ = limit
        for thread in self.threads[:0]:
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
        self.created_names.append(name)
        thread = FakeThread(999, name, self)
        self.threads.append(thread)
        return thread


FakeChannel: TypeAlias = FakeWrongChannel | FakeThread | FakeTextChannel


@final
class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.channels: dict[int, FakeChannel] = {}
        self.fetch_calls: list[int] = []
        self.get_calls: list[int] = []
        self.fetch_failures: set[int] = set()
        self.cache_enabled = True

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        self.get_calls.append(channel_id)
        return self.channels.get(channel_id) if self.cache_enabled else None

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        self.fetch_calls.append(channel_id)
        if channel_id in self.fetch_failures or channel_id not in self.channels:
            raise FakeDiscordError("inaccessible")
        return self.channels[channel_id]


@final
class FakeClient:
    def __init__(self, guild: FakeGuild | None) -> None:
        self.guild = guild
        self.fetch_guild_calls: list[int] = []
        self.fetch_failure = False

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        if self.guild is not None and self.guild.id == guild_id:
            return self.guild
        return None

    async def fetch_guild(self, guild_id: int) -> FakeGuild:
        self.fetch_guild_calls.append(guild_id)
        if self.fetch_failure or self.guild is None:
            raise FakeDiscordError("inaccessible")
        return self.guild


def make_deps(
    *,
    guild_id: int | None = 1,
    channel_id: int | None = 10,
    allowed: set[int] | None = None,
) -> adapter.GptDiscordAdapterDeps:
    allowed_ids = {10} if allowed is None else allowed
    return adapter.GptDiscordAdapterDeps(
        get_allowed_channel_ids=lambda: set(allowed_ids),
        get_startup_channel_id=lambda ids: channel_id if ids == allowed_ids else None,
        get_guild_id=lambda: guild_id,
        discord_failure_types=(FakeDiscordError,),
        scan_timeout_seconds=1.0,
    )


class GptDiscordAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_archived_locked_no_project_thread_revives_by_exact_id_with_history_and_parent_intact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-12-"
        ) as temp_dir:
            db_path = Path(temp_dir) / "adapter.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                init_store_schema(conn)
                _ = conn.execute(
                    "INSERT INTO mirror_threads VALUES (?, 'codex:chats', ?, ?, ?, 1, 'gpt_chat', 'inactive')",
                    ("no-project", "No project", 55, 77),
                )
                conn.commit()
            mapping = get_mirror_thread_owner_by_codex_thread_id(db_path, "no-project")
            self.assertIsNotNone(mapping)
            guild = FakeGuild(1)
            general = FakeTextChannel(10, guild)
            parent = FakeTextChannel(55, guild)
            retained = FakeThread(77, "No project", parent)
            history = retained.history
            guild.channels = {10: general, 77: retained}
            with (
                patch.object(discord, "TextChannel", FakeTextChannel),
                patch.object(discord, "Thread", FakeThread),
            ):
                result = await adapter.revive_retained_gpt_thread(
                    FakeClient(guild), mapping, deps=make_deps()
                )

            self.assertIs(result, retained)
            self.assertIs(result.parent, parent)
            self.assertIs(result.history, history)
            self.assertEqual(result.history, ["first", "second"])
            self.assertFalse(result.archived)
            self.assertFalse(result.locked)
            self.assertEqual(
                (guild.get_calls, retained.edit_calls), ([10, 77], [(False, False)])
            )
            self.assertEqual(general.created_names, [])

    async def test_missing_retained_id_has_no_replacement(self) -> None:
        guild = FakeGuild(1)
        general = FakeTextChannel(10, guild)
        guild.channels = {10: general}
        guild.fetch_failures.add(77)
        mapping = MirrorThreadOwnership(
            CodexThreadId("no-project"),
            "codex:chats",
            "No project",
            DiscordChannelId(55),
            DiscordThreadId(77),
            1.0,
            MirrorThreadManagedBy.GPT_CHAT,
            MirrorThreadLifecycleState.INACTIVE,
        )
        with (
            patch.object(discord, "TextChannel", FakeTextChannel),
            patch.object(discord, "Thread", FakeThread),
        ):
            with self.assertRaises(adapter.GptDiscordRetainedThreadError):
                _ = await adapter.revive_retained_gpt_thread(
                    FakeClient(guild), mapping, deps=make_deps()
                )
            retained = FakeThread(77, "No project", FakeTextChannel(55, guild))
            retained.edit_failure = True
            guild.channels[77] = retained
            with self.assertRaises(adapter.GptDiscordUnarchiveError):
                _ = await adapter.revive_retained_gpt_thread(
                    FakeClient(guild), mapping, deps=make_deps()
                )
        self.assertEqual(guild.fetch_calls, [77])
        self.assertEqual(general.created_names, [])

    async def test_config_errors_are_distinct_and_cached_then_fetch_stays_in_exact_guild(
        self,
    ) -> None:
        guild = FakeGuild(1)
        channel = FakeTextChannel(10, guild)
        guild.channels[10] = channel
        with patch.object(discord, "TextChannel", FakeTextChannel):
            for deps, error in (
                (make_deps(guild_id=None), adapter.GptDiscordConfigError),
                (make_deps(channel_id=None), adapter.GptDiscordConfigError),
                (make_deps(allowed={11}), adapter.GptDiscordChannelNotAllowedError),
            ):
                with self.subTest(error=error.__name__), self.assertRaises(error):
                    _ = await adapter.resolve_configured_text_channel(
                        FakeClient(guild), deps=deps
                    )

            inaccessible = FakeClient(None)
            inaccessible.fetch_failure = True
            with self.assertRaises(adapter.GptDiscordAccessError):
                _ = await adapter.resolve_configured_text_channel(
                    inaccessible, deps=make_deps()
                )

            wrong_guild = FakeClient(guild)
            with self.assertRaises(adapter.GptDiscordAccessError):
                _ = await adapter.resolve_configured_text_channel(
                    wrong_guild, deps=make_deps(guild_id=2)
                )
            self.assertEqual(wrong_guild.fetch_guild_calls, [2])

            guild.channels[10] = FakeWrongChannel(10, guild)
            with self.assertRaises(adapter.GptDiscordChannelTypeError):
                _ = await adapter.resolve_configured_text_channel(
                    FakeClient(guild), deps=make_deps()
                )

            guild.channels[10] = channel
            client = FakeClient(guild)
            self.assertIs(
                await adapter.resolve_configured_text_channel(client, deps=make_deps()),
                channel,
            )
            self.assertEqual((client.fetch_guild_calls, guild.fetch_calls), ([], []))
            _ = guild.channels.pop(10)
            guild.channels[10] = channel
            guild.cache_enabled = False
            self.assertIs(
                await adapter.resolve_configured_text_channel(client, deps=make_deps()),
                channel,
            )
            self.assertEqual(guild.fetch_calls, [10])
