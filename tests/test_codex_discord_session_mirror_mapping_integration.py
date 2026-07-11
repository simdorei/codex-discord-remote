from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import override
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import codex_discord_bot as bot
from codex_discord_store_schema import init_store_schema
import codex_discord_bot_session_mirror_delegation_runtime as delegation_runtime


class MirrorLookupUnavailableError(RuntimeError):
    pass


class BadMirrorLookupError(TypeError):
    pass


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id: int = channel_id


def _delegation_runtime() -> delegation_runtime.BotSessionMirrorDelegationRuntime:
    return delegation_runtime.BotSessionMirrorDelegationRuntime(module=bot)


class DiscordSessionMirrorMappingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    _old_discord_log_path: str | None = None
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None

    @override
    def setUp(self) -> None:
        self._old_discord_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._temp_dir = temp_dir
        os.environ["CODEX_DISCORD_LOG_PATH"] = str(
            Path(temp_dir.name) / "discord-smoke.log"
        )

    @override
    def tearDown(self) -> None:
        if self._old_discord_log_path is None:
            _ = os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
        else:
            os.environ["CODEX_DISCORD_LOG_PATH"] = self._old_discord_log_path
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def _log_text(self) -> str:
        log_path = Path(os.environ["CODEX_DISCORD_LOG_PATH"])
        return log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    def test_should_delegate_output_to_session_mirror_mapping_runtime_failure_logs(
        self,
    ) -> None:
        channel = FakeChannel(channel_id=444)

        with mock.patch.object(
            bot, "discord_session_mirror_enabled", return_value=True
        ):
            with mock.patch.object(
                bot,
                "get_mirrored_codex_thread_id",
                side_effect=MirrorLookupUnavailableError("mirror lookup unavailable"),
            ):
                result = _delegation_runtime().should_delegate_output_to_session_mirror(
                    channel,
                    "thread-1",
                )

        self.assertFalse(result)
        self.assertIn(
            (
                "session_mirror_delegate_disabled target=thread-1 "
                "reason=mapping_unavailable channel=444 error_type=MirrorLookupUnavailableError"
            ),
            self._log_text(),
        )

    def test_should_delegate_output_to_session_mirror_mapping_type_error_is_not_disabled(
        self,
    ) -> None:
        channel = FakeChannel(channel_id=444)

        with mock.patch.object(
            bot, "discord_session_mirror_enabled", return_value=True
        ):
            with mock.patch.object(
                bot,
                "get_mirrored_codex_thread_id",
                side_effect=BadMirrorLookupError("bad mirror lookup"),
            ):
                with self.assertRaisesRegex(TypeError, "bad mirror lookup"):
                    _ = _delegation_runtime().should_delegate_output_to_session_mirror(
                        channel,
                        "thread-1",
                    )

        self.assertNotIn("session_mirror_delegate_disabled", self._log_text())

    async def test_prepare_mapped_session_mirror_output_mapping_runtime_failure_logs(
        self,
    ) -> None:
        channel = FakeChannel(channel_id=555)

        with mock.patch.object(
            bot, "discord_session_mirror_enabled", return_value=True
        ):
            with mock.patch.object(
                bot,
                "get_mirrored_codex_thread_id",
                side_effect=MirrorLookupUnavailableError("mirror lookup unavailable"),
            ):
                result = (
                    await _delegation_runtime().prepare_mapped_session_mirror_output(
                        channel,
                        "thread-1",
                    )
                )

        self.assertFalse(result)
        self.assertIn(
            (
                "session_mirror_output_prepare_failed target=thread-1 "
                "reason=mapping_unavailable channel=555 error_type=MirrorLookupUnavailableError"
            ),
            self._log_text(),
        )

    async def test_prepare_mapped_session_mirror_output_mapping_type_error_is_not_prepare_failure(
        self,
    ) -> None:
        channel = FakeChannel(channel_id=555)

        with mock.patch.object(
            bot, "discord_session_mirror_enabled", return_value=True
        ):
            with mock.patch.object(
                bot,
                "get_mirrored_codex_thread_id",
                side_effect=BadMirrorLookupError("bad mirror lookup"),
            ):
                with self.assertRaisesRegex(TypeError, "bad mirror lookup"):
                    _ = await _delegation_runtime().prepare_mapped_session_mirror_output(
                        channel,
                        "thread-1",
                    )

        self.assertNotIn("session_mirror_output_prepare_failed", self._log_text())

    async def test_inactive_gpt_mapping_is_not_prepared_for_output(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-09-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            db_path = Path(temp_dir) / "mapping.sqlite"
            with mock.patch.object(bot, "MIRROR_DB_PATH", db_path):
                with closing(sqlite3.connect(db_path)) as conn, conn:
                    init_store_schema(conn)
                    _ = conn.execute(
                        "INSERT INTO mirror_threads VALUES "
                        + "(?, 'codex:chats', 'Inactive', 100, 777, 1.0, 'gpt_chat', 'inactive')",
                        ("gpt-inactive",),
                    )
                channel = FakeChannel(channel_id=777)
                with mock.patch.object(
                    bot,
                    "prime_session_mirror_cursor_for_target",
                    side_effect=AssertionError(
                        "inactive target reached cursor preparation"
                    ),
                ):
                    prepared = await _delegation_runtime().prepare_mapped_session_mirror_output(
                        channel,
                        "gpt-inactive",
                    )

        self.assertFalse(prepared)


if __name__ == "__main__":
    _ = unittest.main()
