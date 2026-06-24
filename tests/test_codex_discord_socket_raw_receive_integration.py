from __future__ import annotations

import json
import os
import tempfile
import unittest
from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol, cast
from unittest import mock

import codex_discord_bot as bot
import codex_discord_bot_client_adapter_runtime as client_adapter_runtime
import codex_discord_socket_event_log as socket_event_log


SocketEventData = socket_event_log.SocketEventData


class BadSocketParserDependencyError(TypeError):
    pass


class OnSocketRawReceiveFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, message: str | bytes) -> Awaitable[None]: ...


class OnSocketResponseFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, payload: SocketEventData) -> Awaitable[None]: ...


class LogSocketPayloadFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, payload: SocketEventData) -> Awaitable[None]: ...


class FormatSocketInteractionUserFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, data: SocketEventData) -> str: ...


class IsTrackedSocketMessageChannelFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, channel_id: int | None) -> tuple[bool, str]: ...


class SocketMessageChannel(Protocol):
    id: int


class FakeCachedSocketChannel:
    def __init__(self, channel_id: int) -> None:
        self.id: int = channel_id


ON_SOCKET_RAW_RECEIVE = cast(OnSocketRawReceiveFunc, bot.CodexDiscordBot.on_socket_raw_receive)
ON_SOCKET_RESPONSE = cast(OnSocketResponseFunc, bot.CodexDiscordBot.on_socket_response)
LOG_SOCKET_PAYLOAD = cast(LogSocketPayloadFunc, bot.CodexDiscordBot.log_socket_payload)
FORMAT_SOCKET_INTERACTION_USER = cast(
    FormatSocketInteractionUserFunc,
    bot.CodexDiscordBot.format_socket_interaction_user,
)
IS_TRACKED_SOCKET_MESSAGE_CHANNEL = cast(
    IsTrackedSocketMessageChannelFunc,
    bot.CodexDiscordBot.is_tracked_socket_message_channel,
)


class FakeSocketClient:
    def __init__(
        self,
        *,
        delegate_log: bool,
        allowed_channel_id: int | None = 222,
        cached_channel_id: int | None = None,
    ) -> None:
        self._delegate_log: bool = delegate_log
        self._allowed_channel_id: int | None = allowed_channel_id
        self._cached_channel_id: int | None = cached_channel_id
        self.payloads: list[SocketEventData] = []

    def is_allowed_channel(self, channel_id: int | None) -> bool:
        return self._allowed_channel_id is not None and channel_id == self._allowed_channel_id

    def is_allowed_message_channel(self, channel: SocketMessageChannel) -> bool:
        return self._allowed_channel_id is not None and channel.id == self._allowed_channel_id

    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[FakeCachedSocketChannel | None, str]:
        if self._cached_channel_id == channel_id:
            return FakeCachedSocketChannel(channel_id), "test_cache"
        return None, "-"

    def format_socket_interaction_user(self, data: SocketEventData) -> str:
        return FORMAT_SOCKET_INTERACTION_USER(cast(bot.CodexDiscordBot, self), data)

    def is_tracked_socket_message_channel(self, channel_id: int | None) -> tuple[bool, str]:
        return IS_TRACKED_SOCKET_MESSAGE_CHANNEL(cast(bot.CodexDiscordBot, self), channel_id)

    async def log_socket_payload(self, payload: SocketEventData) -> None:
        if self._delegate_log:
            await LOG_SOCKET_PAYLOAD(cast(bot.CodexDiscordBot, self), payload)
            return
        self.payloads.append(payload)


class DiscordSocketRawReceiveIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_socket_raw_receive_dispatches_gateway_payload(self) -> None:
        fake_client = FakeSocketClient(delegate_log=True)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            log_path = Path(temp_dir) / "discord-smoke.log"
            payload = {
                "t": "MESSAGE_CREATE",
                "d": {
                    "channel_id": "222",
                    "guild_id": "111",
                    "content": "raw message",
                    "author": {"id": "999", "bot": False},
                },
            }
            with mock.patch.dict(os.environ, {"CODEX_DISCORD_LOG_PATH": str(log_path)}):
                await ON_SOCKET_RAW_RECEIVE(cast(bot.CodexDiscordBot, fake_client), json.dumps(payload))
            log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("socket_message_create channel=222 tracked=True", log_text)
        self.assertNotIn("raw message", log_text)

    async def test_socket_message_create_logs_tracked_without_content(self) -> None:
        fake_client = FakeSocketClient(delegate_log=True, cached_channel_id=222)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            log_path = Path(temp_dir) / "discord-smoke.log"
            payload = cast(
                SocketEventData,
                {
                    "t": "MESSAGE_CREATE",
                    "d": {
                        "channel_id": "222",
                        "guild_id": "111",
                        "content": "sensitive prompt",
                        "author": {"id": "999", "bot": False},
                    },
                },
            )
            with mock.patch.dict(os.environ, {"CODEX_DISCORD_LOG_PATH": str(log_path)}):
                await ON_SOCKET_RESPONSE(cast(bot.CodexDiscordBot, fake_client), payload)
            log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("socket_message_create channel=222 tracked=True", log_text)
        self.assertIn("source=test_cache", log_text)
        self.assertIn("content_len=16", log_text)
        self.assertNotIn("sensitive prompt", log_text)

    async def test_socket_raw_receive_malformed_payload_is_ignored(self) -> None:
        fake_client = FakeSocketClient(delegate_log=False)

        await ON_SOCKET_RAW_RECEIVE(cast(bot.CodexDiscordBot, fake_client), "{")

        self.assertEqual(fake_client.payloads, [])

    async def test_socket_raw_receive_type_error_is_not_ignored(self) -> None:
        fake_client = FakeSocketClient(delegate_log=False)

        def loads(raw_text: str) -> SocketEventData:
            _ = raw_text
            raise BadSocketParserDependencyError("bad socket parser dependency")

        with mock.patch("codex_discord_socket_event_log.json.loads", loads):
            with self.assertRaisesRegex(BadSocketParserDependencyError, "bad socket parser dependency"):
                await ON_SOCKET_RAW_RECEIVE(cast(bot.CodexDiscordBot, fake_client), "{}")

    async def test_socket_event_logging_dedupes_raw_and_response_hooks(self) -> None:
        fake_client = FakeSocketClient(delegate_log=True)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            log_path = Path(temp_dir) / "discord-smoke.log"
            payload = cast(
                SocketEventData,
                {
                    "t": "MESSAGE_CREATE",
                    "s": 123,
                    "d": {
                        "id": "555",
                        "channel_id": "222",
                        "guild_id": "111",
                        "content": "single log",
                        "author": {"id": "999", "bot": False},
                    },
                },
            )
            with mock.patch.dict(os.environ, {"CODEX_DISCORD_LOG_PATH": str(log_path)}):
                await ON_SOCKET_RAW_RECEIVE(cast(bot.CodexDiscordBot, fake_client), json.dumps(payload))
                await ON_SOCKET_RESPONSE(cast(bot.CodexDiscordBot, fake_client), payload)
            log_text = log_path.read_text(encoding="utf-8")

        self.assertEqual(log_text.count("socket_message_create channel=222 tracked=True"), 1)

    def test_discord_client_enables_debug_events_for_raw_socket_diagnostics(self) -> None:
        source = Path(client_adapter_runtime.__file__).read_text(encoding="utf-8")
        self.assertIn("enable_debug_events=True", source)

    async def test_socket_message_create_untracked_omits_author_and_content_len(self) -> None:
        fake_client = FakeSocketClient(delegate_log=True, allowed_channel_id=None)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            log_path = Path(temp_dir) / "discord-smoke.log"
            payload = cast(
                SocketEventData,
                {
                    "t": "MESSAGE_CREATE",
                    "d": {
                        "channel_id": "333",
                        "guild_id": "111",
                        "content": "sensitive prompt",
                        "author": {"id": "999", "bot": False},
                    },
                },
            )
            with (
                mock.patch.object(bot, "MIRROR_DB_PATH", Path(temp_dir) / "mirror.sqlite"),
                mock.patch.dict(os.environ, {"CODEX_DISCORD_LOG_PATH": str(log_path)}),
            ):
                await ON_SOCKET_RESPONSE(cast(bot.CodexDiscordBot, fake_client), payload)
            log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("socket_message_create_untracked channel=333", log_text)
        self.assertNotIn("author=999", log_text)
        self.assertNotIn("content_len", log_text)
        self.assertNotIn("sensitive prompt", log_text)

    async def test_socket_interaction_create_logs_sanitized_command(self) -> None:
        fake_client = FakeSocketClient(delegate_log=True, allowed_channel_id=None)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            log_path = Path(temp_dir) / "discord-smoke.log"
            payload = cast(
                SocketEventData,
                {
                    "t": "INTERACTION_CREATE",
                    "d": {
                        "channel_id": "222",
                        "guild_id": "111",
                        "type": 3,
                        "member": {"user": {"id": "999"}},
                        "data": {"custom_id": "codex_busy:abcdabcdabcdabcdabcdabcd:queue"},
                    },
                },
            )
            with mock.patch.dict(os.environ, {"CODEX_DISCORD_LOG_PATH": str(log_path)}):
                await ON_SOCKET_RESPONSE(cast(bot.CodexDiscordBot, fake_client), payload)
            log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("socket_interaction_create channel=222", log_text)
        self.assertIn("user=999", log_text)
        self.assertIn("command=codex_busy:abcdabcdabcdabcdabcdabcd:queue", log_text)


if __name__ == "__main__":
    _ = unittest.main()
