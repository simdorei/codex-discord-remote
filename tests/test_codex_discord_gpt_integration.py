from __future__ import annotations

from contextlib import closing
import inspect
from pathlib import Path
import sqlite3
import tempfile
from types import FunctionType
from typing import cast
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

import codex_discord_bot_lifecycle_runtime as lifecycle_runtime
import codex_discord_gpt_candidates as candidates
import codex_discord_gpt_discord_adapter as adapter
import codex_discord_gpt_ownership as ownership
import codex_discord_gpt_runtime as gpt_runtime
import codex_discord_prefix_gpt_commands as prefix
import codex_discord_project_runtime as project_runtime
import codex_discord_store_session_mirror as session_mirror_store
from codex_thread_models import ThreadInfo
import tests.test_codex_discord_bot_session_mirror_factory as mirror_factory_fakes
from tests.test_codex_discord_bot_session_mirror_runtime import FakeOwner
import tests.test_codex_discord_gpt_discord_adapter as adapter_fakes
import tests.test_codex_discord_gpt_delivery as delivery_tests


class GptIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_sync_synced_unsync_resync_uses_one_runtime_lock_and_ready_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-15-"
        ) as temp:
            db_path = Path(temp) / "happy.sqlite"
            rollout = Path(temp) / "rollout.jsonl"
            _ = rollout.write_text("{}\n", encoding="utf-8")
            source = ThreadInfo(
                "app-chat", "No project", "", 1, str(rollout), "gpt", "ultra", 1
            )
            _init_mapping(db_path, source.id, source.title, discord_thread_id=200)
            guild = adapter_fakes.FakeGuild(1)
            general = adapter_fakes.FakeTextChannel(10, guild)
            retained = adapter_fakes.FakeThread(200, source.title, general)
            retained.archived = retained.locked = False
            history = retained.history
            guild.channels = {10: general, 200: retained}
            discord_deps = adapter_fakes.make_deps()
            runtime = gpt_runtime.GptRuntime(db_path, discord_deps=discord_deps)
            lock = delivery_tests.new_configured_channel_lock()
            runtime.bind_configured_channel_lock(lock)
            self.assertIs(runtime.configured_channel_lock, lock)
            snapshot_store = runtime.snapshot_store
            sender = AsyncMock(return_value=1)
            command_deps = prefix.PrefixGptCommandDeps(
                _client(guild), runtime, cast(prefix.SendChunks, sender)
            )
            handle = prefix.handle_prefix_gpt_command
            with (
                patch.object(candidates, "load_gpt_candidates", return_value=(source,)),
                patch.object(
                    adapter, "resolve_configured_text_channel", return_value=general
                ),
                patch.object(discord, "TextChannel", adapter_fakes.FakeTextChannel),
                patch.object(discord, "Thread", adapter_fakes.FakeThread),
            ):
                await runtime.reconcile(_client(guild))
                commands = "list 5|sync 1, 1|synced|unsync 1, 1|list|sync 1|sync_clear"
                for arg in commands.split("|"):
                    self.assertTrue(
                        await handle("gpt", arg, _message(), deps=command_deps)
                    )
            self.assertEqual(
                [call.args[1] for call in sender.await_args_list],
                [
                    "App-native Codex chats:\n1. No project",
                    "GPT sync complete.",
                    "Synced app-native Codex chats:\n"
                    + "1. No project [available; configured]",
                    "GPT unsync complete.",
                    "App-native Codex chats:\n1. No project",
                    "GPT sync complete.",
                    "GPT sync clear complete.",
                ],
            )
            self.assertIs(runtime.snapshot_store, snapshot_store)
            mapping = ownership.get_mirror_thread_owner_by_codex_thread_id(
                db_path, source.id
            )
            self.assertEqual(
                None
                if mapping is None
                else (mapping.discord_thread_id, mapping.lifecycle_state),
                (200, ownership.MirrorThreadLifecycleState.INACTIVE),
            )
            self.assertIs(retained.history, history)
            self.assertEqual(
                retained.edit_calls,
                [(True, True), (False, False), (True, True)],
            )
            self.assertEqual(general.created_names, [])
            cursor_row = session_mirror_store.get_session_mirror_offset(
                db_path, source.id
            )
            self.assertEqual(
                None if cursor_row is None else cursor_row[:2],
                (str(rollout), rollout.stat().st_size),
            )

            on_ready = cast(
                FunctionType, getattr(lifecycle_runtime.BotLifecycleRuntime, "on_ready")
            )
            ready_source = inspect.getsource(on_ready)
            reconcile_at = ready_source.index("reconcile_gpt_runtime")
            for hook in ("start_history_polling", "start_session_mirroring"):
                self.assertLess(reconcile_at, ready_source.index(hook))

    async def test_config_access_type_journal_and_both_delivery_lock_orders_have_no_fallback(
        self,
    ) -> None:
        guild = adapter_fakes.FakeGuild(1)
        guild.channels[10] = adapter_fakes.FakeWrongChannel(10, guild)
        cases = (
            (_client(guild), adapter_fakes.make_deps(guild_id=None)),
            (_client(None), adapter_fakes.make_deps()),
            (_client(guild), adapter_fakes.make_deps()),
        )
        errors = (
            adapter.GptDiscordConfigError,
            adapter.GptDiscordAccessError,
            adapter.GptDiscordChannelTypeError,
        )
        with patch.object(discord, "TextChannel", adapter_fakes.FakeTextChannel):
            for (client, deps), error in zip(cases, errors, strict=True):
                with self.subTest(error=error.__name__), self.assertRaises(error):
                    _ = await adapter.resolve_configured_text_channel(client, deps)

        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-15-"
        ) as temp:
            db_path = Path(temp) / "failure.sqlite"
            _init_mapping(db_path, "gpt-thread", "GPT", discord_thread_id=200)
            discord_deps = adapter_fakes.make_deps()
            runtime = gpt_runtime.GptRuntime(db_path, discord_deps=discord_deps)
            lock = delivery_tests.new_configured_channel_lock()
            runtime.bind_configured_channel_lock(lock)
            guild.channels[10] = adapter_fakes.FakeTextChannel(10, guild)
            with (
                patch.object(discord, "TextChannel", adapter_fakes.FakeTextChannel),
                patch(
                    "codex_discord_gpt_creation_journal.load_gpt_creation_protections",
                    side_effect=RuntimeError("journal failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "journal failed"),
            ):
                await runtime.reconcile(_client(guild))
            self.assertFalse(runtime.ready)
            self.assertIsNone(runtime.reconciliation)
            with patch.object(
                project_runtime,
                "resolve_exact_channel_decision",
                return_value=project_runtime.ExactChannelBlocked("journal"),
            ):
                self.assertIsNone(
                    runtime.resolve_routable_thread_id(lambda _value: "fallback", 77)
                )
            scenarios = delivery_tests.GptDeliveryTests()
            await scenarios.assert_delivery_first(lock)
            await scenarios.assert_deactivation_first(lock)

            logs: list[str] = []
            inner = mirror_factory_fakes.make_test_runtime(lock, db_path)
            mirror = lifecycle_runtime.GptSessionMirrorRuntime(
                runtime, inner, logs.append
            )
            target = {
                "codex_thread_id": "gpt-thread",
                "discord_channel_id": 100,
                "discord_thread_id": 200,
            }
            actual_reader = session_mirror_store.get_session_mirror_delivery_identity
            with patch.object(
                session_mirror_store,
                "get_session_mirror_delivery_identity",
                wraps=actual_reader,
            ) as reader:
                await mirror.mirror_session_target(FakeOwner(), target)
                self.assertEqual(reader.call_count, 0)
                self.assertEqual(
                    logs,
                    [
                        "gpt_session_delivery_ignored reason=startup_reconciliation_incomplete"
                    ],
                )
                with patch.object(
                    discord, "TextChannel", adapter_fakes.FakeTextChannel
                ):
                    await runtime.reconcile(_client(guild))
                await mirror.mirror_session_target(FakeOwner(), target)
                self.assertEqual(reader.call_count, 1)
                _update_mapping(db_path, managed_by="ordinary", state="inactive")
                decision = runtime.resolve_exact_channel_decision(200, None)
                self.assertIsInstance(decision, project_runtime.ExactChannelUnknown)
                await mirror.mirror_session_target(FakeOwner(), target)
                self.assertEqual(reader.call_count, 2)
                _update_mapping(db_path, managed_by="gpt_chat", state="inactive")
                decision = runtime.resolve_exact_channel_decision(200, None)
                self.assertIsInstance(decision, project_runtime.ExactChannelBlocked)
                await mirror.mirror_session_target(FakeOwner(), target)
                self.assertEqual(reader.call_count, 2)
            self.assertEqual(
                logs[-1], "gpt_session_delivery_ignored reason=gpt_inactive"
            )

        sender = AsyncMock(return_value=1)
        command_deps = prefix.PrefixGptCommandDeps(
            _client(None),
            runtime,
            cast(prefix.SendChunks, sender),
        )
        handle = prefix.handle_prefix_gpt_command
        for malformed in "|list 1 2|sync|synced 1|unsync 1 2|sync-clear|clear".split(
            "|"
        ):
            self.assertTrue(
                await handle("gpt", malformed, _message(), deps=command_deps)
            )
        self.assertFalse(await handle("gpts", "list", _message(), deps=command_deps))

    async def test_no_project_chat_resync_restores_the_same_discord_thread(
        self,
    ) -> None:
        case = adapter_fakes.GptDiscordAdapterTests()
        await case.test_archived_locked_no_project_thread_revives_by_exact_id_with_history_and_parent_intact()


def _client(guild: adapter_fakes.FakeGuild | None) -> adapter.DiscordClient:
    return cast(adapter.DiscordClient, cast(object, adapter_fakes.FakeClient(guild)))


def _message() -> prefix.GptMessage:
    value = Mock(channel=Mock(id=10), author=Mock(id=20), guild=Mock(id=1))
    return cast(prefix.GptMessage, cast(object, value))


def _init_mapping(
    db_path: Path, owner: str, title: str, *, discord_thread_id: int
) -> None:
    _ = ownership.get_mirror_thread_owner_by_codex_thread_id(db_path, owner)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        _ = conn.execute(
            "INSERT INTO mirror_threads VALUES "
            + "(?, 'codex:chats', ?, 10, ?, 1.0, 'gpt_chat', 'active')",
            (owner, title, discord_thread_id),
        )


def _update_mapping(db_path: Path, *, managed_by: str, state: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        _ = conn.execute(
            "UPDATE mirror_threads SET managed_by = ?, lifecycle_state = ? "
            + "WHERE codex_thread_id = 'gpt-thread'",
            (managed_by, state),
        )


if __name__ == "__main__":
    _ = unittest.main()
