from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast
import unittest
from unittest.mock import patch

import codex_discord_bot as bot


EventCallback = Callable[[object], Awaitable[None]]


class DiscordEventErrorLoggingTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self) -> bot.CodexDiscordBot:
        return bot.CodexDiscordBot(
            allowed_channel_ids={1},
            allowed_user_ids={1},
            startup_channel_id=1,
            guild_id=None,
            enable_prefix_commands=True,
            plain_ask_mention_user_ids=set(),
        )

    async def test_run_event_logs_only_event_and_exception_type(self) -> None:
        client = self.make_client()
        logs: list[str] = []
        secret_error = "SECRET_ERROR_TEXT"
        secret_payload = object()

        async def fail_event(payload: object) -> None:
            self.assertIs(payload, secret_payload)
            raise TypeError(secret_error)

        try:
            with patch.object(bot, "log_line", logs.append):
                await client._run_event(
                    cast(EventCallback, fail_event),
                    "on_message",
                    secret_payload,
                )
        finally:
            await client.close()

        self.assertEqual(
            logs,
            ["discord_event_error event=on_message error_type=TypeError"],
        )
        self.assertNotIn(secret_error, "\n".join(logs))

    async def test_direct_on_error_sanitizes_event_name_without_exception(self) -> None:
        client = self.make_client()
        logs: list[str] = []
        try:
            with patch.object(bot, "log_line", logs.append):
                await client.on_error("on_message\r\nunsafe-event")
        finally:
            await client.close()

        self.assertEqual(
            logs,
            ["discord_event_error event=on_message  unsafe-event error_type=-"],
        )


if __name__ == "__main__":
    _ = unittest.main()
