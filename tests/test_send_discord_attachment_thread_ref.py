from __future__ import annotations

from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import TracebackType
from unittest.mock import patch

import aiohttp

import send_discord_attachment as sender
from send_discord_attachment_types import AttachmentCliArgs

_TEMP_PREFIX = "app-gpt-discord-sync-todo-08-"


class FakeResponse:
    def __init__(self, *, status: int, text: str) -> None:
        self.status: int = status
        self._text: str = text

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        return False

    async def text(self) -> str:
        return self._text


class FakeClientSession:
    def __init__(
        self,
        response: FakeResponse,
        *,
        get_response: FakeResponse | None = None,
    ) -> None:
        self.response: FakeResponse = response
        self.get_response: FakeResponse = get_response or FakeResponse(
            status=200, text="{}"
        )
        self.posts: list[tuple[str, dict[str, str], aiohttp.FormData]] = []
        self.gets: list[tuple[str, dict[str, str]]] = []

    async def __aenter__(self) -> FakeClientSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        _ = exc_type, exc, traceback
        return False

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: aiohttp.FormData,
    ) -> FakeResponse:
        self.posts.append((url, headers, data))
        return self.response

    def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        self.gets.append((url, headers))
        return self.get_response


def attachment_args(
    *,
    channel_id: str | None = "123",
    thread_ref: str | None = None,
    work_thread: str | None = None,
    content: str = "",
    content_file: str | None = None,
    files: list[str] | None = None,
) -> AttachmentCliArgs:
    return AttachmentCliArgs(
        channel_id,
        thread_ref,
        work_thread,
        content,
        content_file,
        files or [],
    )


def todo08_temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX, ignore_cleanup_errors=True)


class SendDiscordAttachmentThreadRefTests(unittest.IsolatedAsyncioTestCase):
    def _active_db(self, root: Path, channel_id: int) -> Path:
        db_path = root / "race.sqlite"
        sender.discord_store.init_mirror_db(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            _ = conn.execute(
                "INSERT INTO mirror_threads VALUES (?, 'codex:chats', 'GPT', 100, ?, 1.0, 'gpt_chat', 'active')",
                ("gpt-thread", channel_id),
            )
            conn.commit()
        return db_path

    async def test_thread_ref_resolves_mirror_channel_and_validates_access(
        self,
    ) -> None:
        with todo08_temp_dir() as temp_dir:
            attachment_path = Path(temp_dir) / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            response_body = {
                "id": "m1",
                "channel_id": "555",
                "attachments": [{"filename": "note.txt"}],
            }
            fake_session = FakeClientSession(
                FakeResponse(status=200, text=json.dumps(response_body))
            )
            thread = sender.ThreadInfo(
                id="thread-1",
                title="Task",
                cwd=str(Path(temp_dir)),
                updated_at=1,
                rollout_path="rollout.jsonl",
                model="gpt",
                reasoning_effort="",
                tokens_used=0,
            )
            db_path = Path(temp_dir) / "mirror.sqlite"
            sender.discord_store.upsert_mirror_thread(
                db_path,
                "thread-1",
                "project",
                "Task",
                111,
                555,
                now=1.0,
            )

            with patch.dict(
                os.environ,
                {
                    "DISCORD_BOT_TOKEN": "token",
                    "CODEX_DISCORD_MIRROR_DB": str(db_path),
                },
                clear=True,
            ):
                with patch.object(sender, "ClientSession", return_value=fake_session):
                    with patch.object(
                        sender.thread_store, "resolve_thread_ref", return_value=thread
                    ):
                        result = await sender.send_discord_attachment(
                            attachment_args(
                                channel_id=None,
                                thread_ref="repo:2",
                                files=[str(attachment_path)],
                            ),
                        )

        self.assertEqual(result, response_body)
        self.assertEqual(
            fake_session.gets[0][0], "https://discord.com/api/v10/channels/555"
        )
        self.assertEqual(
            fake_session.posts[0][0],
            "https://discord.com/api/v10/channels/555/messages",
        )

    async def test_thread_ref_stale_mirror_mapping_reports_recovery_text(self) -> None:
        with todo08_temp_dir() as temp_dir:
            attachment_path = Path(temp_dir) / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            fake_session = FakeClientSession(
                FakeResponse(status=200, text="{}"),
                get_response=FakeResponse(
                    status=404, text='{"message":"Unknown Channel"}'
                ),
            )
            thread = sender.ThreadInfo(
                id="thread-1",
                title="Task",
                cwd=str(Path(temp_dir)),
                updated_at=1,
                rollout_path="rollout.jsonl",
                model="gpt",
                reasoning_effort="",
                tokens_used=0,
            )
            db_path = Path(temp_dir) / "mirror.sqlite"
            sender.discord_store.upsert_mirror_thread(
                db_path,
                "thread-1",
                "project",
                "Task",
                111,
                555,
                now=1.0,
            )

            with patch.dict(
                os.environ,
                {
                    "DISCORD_BOT_TOKEN": "token",
                    "CODEX_DISCORD_MIRROR_DB": str(db_path),
                },
                clear=True,
            ):
                with patch.object(sender, "ClientSession", return_value=fake_session):
                    with patch.object(
                        sender.thread_store, "resolve_thread_ref", return_value=thread
                    ):
                        with self.assertRaisesRegex(
                            sender.DiscordChannelAccessError,
                            "mirror mapping stale: run !mirror check, then !mirror sync",
                        ):
                            _ = await sender.send_discord_attachment(
                                attachment_args(
                                    channel_id=None,
                                    thread_ref="repo:2",
                                    files=[str(attachment_path)],
                                ),
                            )

        self.assertEqual(fake_session.posts, [])

    async def test_state_flip_before_post_reread_sends_zero_posts(self) -> None:
        with todo08_temp_dir() as temp_dir:
            root = Path(temp_dir)
            attachment_path = root / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            db_path = self._active_db(root, 777)
            fake_session = FakeClientSession(FakeResponse(status=200, text="{}"))

            async def deactivate_after_access(
                _session: FakeClientSession,
                *,
                channel_id: str,
                headers: dict[str, str],
                mirror_target: bool,
            ) -> None:
                _ = channel_id, headers, mirror_target
                with closing(sqlite3.connect(db_path)) as conn:
                    _ = conn.execute(
                        "UPDATE mirror_threads SET lifecycle_state = 'inactive' WHERE codex_thread_id = 'gpt-thread'"
                    )
                    conn.commit()

            args = attachment_args(channel_id="777", files=[str(attachment_path)])
            with patch.dict(
                os.environ,
                {
                    "DISCORD_BOT_TOKEN": "token",
                    "CODEX_DISCORD_MIRROR_DB": str(db_path),
                },
                clear=True,
            ):
                with patch.object(sender, "ClientSession", return_value=fake_session):
                    with patch.object(
                        sender,
                        "validate_channel_access",
                        new=deactivate_after_access,
                    ):
                        with self.assertRaisesRegex(
                            sender.AttachmentTargetError,
                            "gpt_inactive",
                        ):
                            _ = await sender.send_discord_attachment(args)

        self.assertEqual(fake_session.posts, [])
