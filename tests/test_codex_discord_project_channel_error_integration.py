# pyright: reportUnknownMemberType=false
from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import override
import unittest

import codex_discord_bot as bot


class FetchChannelError(RuntimeError):
    pass


class DiscordProjectChannelErrorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    _old_db_path: Path = Path()
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None

    @override
    def setUp(self) -> None:
        self._old_db_path = bot.MIRROR_DB_PATH
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._temp_dir = temp_dir
        bot.MIRROR_DB_PATH = Path(temp_dir.name) / "mirror.sqlite"
        bot.init_mirror_db()

    @override
    def tearDown(self) -> None:
        bot.MIRROR_DB_PATH = self._old_db_path
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    async def test_surfaces_missing_cached_project_channel(self) -> None:
        class FakeGuild:
            text_channels: list[str] = []

            def get_channel(self, _channel_id: int) -> None:
                return None

            async def fetch_channel(self, _channel_id: int) -> None:
                raise FetchChannelError("boom")

            async def create_text_channel(
                self,
                *_unused_args: str,
                **_unused_kwargs: str | int,
            ) -> None:
                raise AssertionError("missing cached channel should not create a replacement")

        canonical_key = bot.normalize_project_key(r"C:\taxlab")
        with sqlite3.connect(bot.MIRROR_DB_PATH) as conn:
            _ = conn.execute(
                "INSERT INTO mirror_projects ("
                + "project_key, project_name, discord_channel_id, updated_at"
                + ") VALUES (?, ?, ?, ?)",
                (canonical_key, "taxlab", 111, 1.0),
            )

        with self.assertRaisesRegex(
            RuntimeError,
            r"Stored mirror project channel 111 .*FetchChannelError: boom",
        ):
            await bot.get_or_create_project_channel(
                FakeGuild(),
                SimpleNamespace(id=999),
                r"C:\taxlab",
                "taxlab",
            )


if __name__ == "__main__":
    _ = unittest.main()
