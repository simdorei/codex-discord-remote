from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest.mock import patch

import send_discord_attachment as sender
import send_discord_attachment_target as attachment_target
from send_discord_attachment_types import (
    DiscordSendFailedError,
    MissingDiscordBotTokenError,
)
from tests.test_send_discord_attachment_thread_ref import (
    FakeClientSession,
    FakeResponse,
    attachment_args,
)


def todo08_temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(
        prefix="app-gpt-discord-sync-todo-08-", ignore_cleanup_errors=True
    )


class SendDiscordAttachmentTests(unittest.IsolatedAsyncioTestCase):
    _db_path: Path = Path()

    @override
    def setUp(self) -> None:
        mirror_temp = todo08_temp_dir()
        self.addCleanup(mirror_temp.cleanup)
        db_path = Path(mirror_temp.name) / "mirror.sqlite"
        self._db_path = db_path
        sender.discord_store.init_mirror_db(db_path)
        db_patch = patch.object(sender, "_mirror_db_path", return_value=db_path)
        _ = db_patch.start()
        self.addCleanup(db_patch.stop)

    def test_database_fixture_never_resolves_default_path(self) -> None:
        self.assertTrue(
            self._db_path.parent.name.startswith("app-gpt-discord-sync-todo-08-")
        )
        self.assertNotEqual(self._db_path.name, sender.DEFAULT_MIRROR_DB_PATH.name)

    def test_raw_unknown_and_ordinary_channel_ids_are_preserved(self) -> None:
        db_path = self._db_path
        sender.discord_store.upsert_mirror_thread(
            db_path, "ordinary", "project", "Ordinary", 100, 501, now=1.0
        )

        ordinary = attachment_target.resolve_attachment_target(
            attachment_args(channel_id="501"), db_path=db_path
        )
        unknown = attachment_target.resolve_attachment_target(
            attachment_args(channel_id="999"), db_path=db_path
        )

        self.assertEqual((ordinary.channel_id, ordinary.codex_thread_id), ("501", None))
        self.assertEqual((unknown.channel_id, unknown.codex_thread_id), ("999", None))

    def test_load_env_strips_quotes_and_ignores_non_assignments(self) -> None:
        with todo08_temp_dir() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            _ = env_path.write_text(
                "\n".join(
                    [
                        "# ignored",
                        'DISCORD_BOT_TOKEN = " token "',
                        "NO_EQUALS",
                        "OTHER=value",
                    ],
                ),
                encoding="utf-8",
            )

            values = sender.load_env(env_path)

        self.assertEqual(values["DISCORD_BOT_TOKEN"], " token ")
        self.assertEqual(values["OTHER"], "value")
        self.assertNotIn("NO_EQUALS", values)

    def test_get_message_content_prefers_content_file(self) -> None:
        with todo08_temp_dir() as temp_dir:
            content_path = Path(temp_dir) / "message.txt"
            _ = content_path.write_text(" file content \n", encoding="utf-8")

            content = sender.get_message_content(
                attachment_args(content="inline", content_file=str(content_path)),
            )

        self.assertEqual(content, "file content")

    def test_parse_args_accepts_thread_ref_target(self) -> None:
        with patch(
            "sys.argv",
            ["send_discord_attachment.py", "--thread-ref", "repo:2", "note.txt"],
        ):
            args = sender.parse_args()

        self.assertIsNone(args.channel_id)
        self.assertEqual(args.thread_ref, "repo:2")
        self.assertIsNone(args.work_thread)
        self.assertEqual(args.files, ["note.txt"])

    def test_parse_args_rejects_multiple_targets(self) -> None:
        with patch(
            "sys.argv",
            [
                "send_discord_attachment.py",
                "--channel-id",
                "123",
                "--work-thread",
                "thread-1",
                "note.txt",
            ],
        ):
            with self.assertRaises(SystemExit):
                _ = sender.parse_args()

    async def test_missing_token_raises_typed_error_before_file_io(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sender, "load_env", return_value={}):
                with self.assertRaises(MissingDiscordBotTokenError):
                    _ = await sender.send_discord_attachment(
                        attachment_args(files=["missing.txt"])
                    )

    async def test_missing_files_raise_file_not_found_before_network(self) -> None:
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token"}, clear=True):
            with self.assertRaisesRegex(FileNotFoundError, "missing.txt"):
                _ = await sender.send_discord_attachment(
                    attachment_args(files=["missing.txt"])
                )

    async def test_http_failure_raises_typed_error_with_truncated_response_text(
        self,
    ) -> None:
        with todo08_temp_dir() as temp_dir:
            attachment_path = Path(temp_dir) / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            response = FakeResponse(status=500, text="x" * 1200)
            fake_session = FakeClientSession(response)

            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token"}, clear=True):
                with patch.object(sender, "ClientSession", return_value=fake_session):
                    with self.assertRaises(DiscordSendFailedError) as caught:
                        _ = await sender.send_discord_attachment(
                            attachment_args(files=[str(attachment_path)])
                        )

        self.assertEqual(caught.exception.status, 500)
        self.assertEqual(caught.exception.response_text, "x" * 1000)
        self.assertEqual(
            fake_session.gets[0][0], "https://discord.com/api/v10/channels/123"
        )
        self.assertEqual(fake_session.posts[0][1], {"Authorization": "Bot token"})

    async def test_unknown_direct_channel_fails_before_upload(self) -> None:
        with todo08_temp_dir() as temp_dir:
            attachment_path = Path(temp_dir) / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            fake_session = FakeClientSession(
                FakeResponse(status=200, text="{}"),
                get_response=FakeResponse(
                    status=404, text='{"message":"Unknown Channel"}'
                ),
            )

            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token"}, clear=True):
                with patch.object(sender, "ClientSession", return_value=fake_session):
                    with self.assertRaisesRegex(
                        sender.DiscordChannelAccessError, "Unknown Channel"
                    ):
                        _ = await sender.send_discord_attachment(
                            attachment_args(files=[str(attachment_path)])
                        )

        self.assertEqual(fake_session.posts, [])

    async def test_forbidden_direct_channel_fails_before_upload(self) -> None:
        with todo08_temp_dir() as temp_dir:
            attachment_path = Path(temp_dir) / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            fake_session = FakeClientSession(
                FakeResponse(status=200, text="{}"),
                get_response=FakeResponse(status=403, text='{"message":"Forbidden"}'),
            )

            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token"}, clear=True):
                with patch.object(sender, "ClientSession", return_value=fake_session):
                    with self.assertRaisesRegex(
                        sender.DiscordChannelAccessError, "Forbidden"
                    ):
                        _ = await sender.send_discord_attachment(
                            attachment_args(files=[str(attachment_path)])
                        )

        self.assertEqual(fake_session.posts, [])

    async def test_unknown_work_thread_reports_active_archived_text(self) -> None:
        with todo08_temp_dir() as temp_dir:
            attachment_path = Path(temp_dir) / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")

            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token"}, clear=True):
                with patch.object(
                    sender.thread_store,
                    "resolve_thread_ref",
                    side_effect=sender.thread_store.ThreadStoreError("missing"),
                ):
                    with patch.object(
                        sender.thread_store,
                        "resolve_archived_thread_ref",
                        side_effect=sender.thread_store.ThreadStoreError("missing"),
                    ):
                        with self.assertRaisesRegex(
                            sender.AttachmentTargetError,
                            "not in active/archived threads",
                        ):
                            _ = await sender.send_discord_attachment(
                                attachment_args(
                                    channel_id=None,
                                    work_thread="missing",
                                    files=[str(attachment_path)],
                                ),
                            )

    async def test_success_returns_decoded_json_without_live_network(self) -> None:
        with todo08_temp_dir() as temp_dir:
            attachment_path = Path(temp_dir) / "note.txt"
            _ = attachment_path.write_text("payload", encoding="utf-8")
            response_body = {
                "id": "m1",
                "channel_id": "123",
                "attachments": [{"filename": "note.txt"}],
            }
            response = FakeResponse(status=200, text=json.dumps(response_body))
            fake_session = FakeClientSession(response)

            with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token"}, clear=True):
                with patch.object(sender, "ClientSession", return_value=fake_session):
                    result = await sender.send_discord_attachment(
                        attachment_args(
                            channel_id="123",
                            content="hello",
                            files=[str(attachment_path)],
                        ),
                    )

        self.assertEqual(result, response_body)
        self.assertEqual(
            fake_session.gets[0][0], "https://discord.com/api/v10/channels/123"
        )
        self.assertEqual(
            fake_session.posts[0][0],
            "https://discord.com/api/v10/channels/123/messages",
        )


if __name__ == "__main__":
    _ = unittest.main()
