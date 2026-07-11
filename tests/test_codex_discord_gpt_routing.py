from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Final, TypeAlias, override
import unittest
from unittest.mock import patch

import codex_discord_gpt_creation_journal as journal
import codex_discord_gpt_ownership as ownership
import codex_discord_message_target as message_target
import codex_discord_project_runtime as project_runtime
import send_discord_attachment as attachment_sender
import send_discord_attachment_target as attachment_target
from aiohttp import FormData
from codex_thread_models import ThreadInfo
from send_discord_attachment_types import (
    AttachmentTargetError,
    DiscordMessageResponse,
)
from codex_discord_store_schema import init_store_schema
from tests.test_send_discord_attachment_thread_ref import (
    FakeClientSession,
    FakeResponse,
    attachment_args,
)


MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]
JournalRow: TypeAlias = tuple[str, str, str, int, str, str, int | None, float, float]
_TEMP_PREFIX: Final = "app-gpt-discord-sync-todo-08-"


def _mapping(
    owner: str,
    discord_id: int,
    state: str = "active",
    *,
    managed_by: str = "gpt_chat",
) -> MirrorRow:
    project = "codex:chats" if managed_by == "gpt_chat" else "project"
    return owner, project, owner, 100, discord_id, 1.0, managed_by, state


def _journal(
    owner: str,
    nonce: str,
    status: str,
    discord_id: int | None,
) -> JournalRow:
    return owner, "codex:chats", owner, 100, nonce, status, discord_id, 1.0, 1.0


class GptRoutingTests(unittest.TestCase):
    def _db_path(self, temp_dir: str) -> Path:
        db_path = Path(temp_dir) / "routing.sqlite"
        with closing(sqlite3.connect(db_path)) as conn:
            init_store_schema(conn)
        return db_path

    def _insert(
        self,
        db_path: Path,
        mappings: list[MirrorRow],
        journals: list[JournalRow] | None = None,
    ) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            _ = conn.executemany(
                "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", mappings
            )
            if journals:
                _ = conn.executemany(
                    "INSERT INTO gpt_chat_creation_ops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    journals,
                )
            conn.commit()

    def _thread(self, owner: str, temp_dir: str) -> ThreadInfo:
        return ThreadInfo(
            id=owner,
            title="Task",
            cwd=temp_dir,
            updated_at=1,
            rollout_path="rollout.jsonl",
            model="gpt",
            reasoning_effort="",
            tokens_used=0,
        )

    def test_exact_active_owner_routes_and_unknown_general_channel_remains_ordinary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=_TEMP_PREFIX, ignore_cleanup_errors=True
        ) as temp_dir:
            db_path = self._db_path(temp_dir)
            self._insert(db_path, [_mapping("gpt-active", 201)])
            active = project_runtime.resolve_exact_channel_decision(
                db_path, 201, "Active"
            )
            unknown = project_runtime.resolve_exact_channel_decision(
                db_path, 100, "general"
            )
            fallback_calls: list[int | None] = []

            def fallback(channel_id: int | None) -> str | None:
                fallback_calls.append(channel_id)
                return None

            active_target = message_target.resolve_discord_message_target(
                fallback,
                201,
                100,
                exact_channel_decision=active,
            )
            general_target = message_target.resolve_discord_message_target(
                fallback,
                100,
                None,
                exact_channel_decision=unknown,
            )

        self.assertIsInstance(active, project_runtime.ExactChannelActive)
        self.assertEqual(active_target.target_thread_id, "gpt-active")
        self.assertEqual(active_target.target_source, "gpt")
        self.assertIsInstance(unknown, project_runtime.ExactChannelUnknown)
        self.assertEqual(general_target.target_source, "selected")
        self.assertEqual(fallback_calls, [100, None])

    def test_exact_lookups_close_database_immediately(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=_TEMP_PREFIX, ignore_cleanup_errors=True
        ) as temp_dir:
            db_path = self._db_path(temp_dir)
            _ = ownership.get_mirror_thread_owner_by_discord_thread_id(db_path, 401)
            _ = journal.load_gpt_creation_protections(db_path)
            db_path.unlink()
            Path(temp_dir).rmdir()

    def test_inactive_and_exact_journal_id_or_marker_block_every_fallback_and_attachment_option(
        self,
    ) -> None:
        marker = "[gpt-sync:" + ("a" * 32) + "] Pending"
        with tempfile.TemporaryDirectory(
            prefix=_TEMP_PREFIX, ignore_cleanup_errors=True
        ) as temp_dir:
            db_path = self._db_path(temp_dir)
            self._insert(
                db_path,
                [
                    _mapping("inactive", 301, "inactive"),
                    _mapping("deactivating", 302, "deactivating"),
                    _mapping("reactivating", 303, "reactivating"),
                    _mapping("journal-id", 304),
                ],
                [
                    _journal("journal-id", "b" * 32, "discord_identified", 304),
                    _journal("journal-marker", "a" * 32, "create_started", None),
                ],
            )
            decisions = (
                project_runtime.resolve_exact_channel_decision(
                    db_path, 301, "Inactive"
                ),
                project_runtime.resolve_exact_channel_decision(
                    db_path, 302, "Stopping"
                ),
                project_runtime.resolve_exact_channel_decision(
                    db_path, 303, "Starting"
                ),
                project_runtime.resolve_exact_channel_decision(db_path, 304, "Pending"),
                project_runtime.resolve_exact_channel_decision(db_path, 999, marker),
            )
            blocked_target = message_target.resolve_discord_message_target(
                lambda _channel_id: self.fail("blocked routing reached fallback"),
                301,
                100,
                exact_channel_decision=decisions[0],
            )
            with self.assertRaises(AttachmentTargetError):
                _ = attachment_target.resolve_attachment_target(
                    attachment_args(channel_id="301"), db_path=db_path
                )
            with self.assertRaises(AttachmentTargetError):
                _ = attachment_target.resolve_attachment_target(
                    attachment_args(channel_id="304"), db_path=db_path
                )
            with patch.object(
                attachment_target,
                "resolve_thread_from_ref",
                return_value=self._thread("inactive", temp_dir),
            ):
                for args in (
                    attachment_args(channel_id=None, thread_ref="inactive"),
                    attachment_args(channel_id=None, work_thread="inactive"),
                ):
                    with (
                        self.subTest(option=args),
                        self.assertRaises(AttachmentTargetError),
                    ):
                        _ = attachment_target.resolve_attachment_target(
                            args, db_path=db_path
                        )

        self.assertTrue(
            all(
                isinstance(decision, project_runtime.ExactChannelBlocked)
                for decision in decisions
            )
        )
        self.assertEqual(blocked_target.target_source, "blocked")


class AttachmentPostBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_operation_past_final_check_can_complete(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=_TEMP_PREFIX, ignore_cleanup_errors=True
        ) as temp_dir:
            root = Path(temp_dir)
            attachment_path = root / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            db_path = root / "race.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                init_store_schema(conn)
                _ = conn.execute(
                    "INSERT INTO mirror_threads VALUES (?, 'codex:chats', 'GPT', 100, 778, 1.0, 'gpt_chat', 'active')",
                    ("gpt-thread",),
                )
                conn.commit()

            class DeactivateOnPostSession(FakeClientSession):
                @override
                def post(
                    self,
                    url: str,
                    *,
                    headers: dict[str, str],
                    data: FormData,
                ) -> FakeResponse:
                    with closing(sqlite3.connect(db_path)) as conn:
                        _ = conn.execute(
                            "UPDATE mirror_threads SET lifecycle_state = 'inactive' WHERE codex_thread_id = 'gpt-thread'"
                        )
                        conn.commit()
                    return super().post(url, headers=headers, data=data)

            response_body: DiscordMessageResponse = {
                "id": "m1",
                "channel_id": "778",
                "attachments": [],
            }
            fake_session = DeactivateOnPostSession(
                FakeResponse(status=200, text=json.dumps(response_body))
            )
            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token"}, clear=True):
                with patch.object(
                    attachment_sender, "_mirror_db_path", return_value=db_path
                ):
                    with patch.object(
                        attachment_sender, "ClientSession", return_value=fake_session
                    ):
                        result = await attachment_sender.send_discord_attachment(
                            attachment_args(
                                channel_id="778", files=[str(attachment_path)]
                            )
                        )

        self.assertEqual(result, response_body)
        self.assertEqual(len(fake_session.posts), 1)
