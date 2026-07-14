from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Protocol, cast, override
from unittest import mock

import codex_discord_bot as bot


class FakeAuthor:
    id: int = 242286902982606848
    bot: bool = False


class FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        channel_id: int = 333,
        message_id: int | None = None,
        created_at: dt.datetime | None = None,
    ) -> None:
        self.id: int | None = message_id
        self.author: FakeAuthor = FakeAuthor()
        self.content: str = content
        self.raw_mentions: list[int] = []
        self.mentions: list[FakeAuthor] = []
        self.attachments: list[str] = []
        self.embeds: list[str] = []
        self.stickers: list[str] = []
        self.created_at: dt.datetime = created_at or dt.datetime.now(dt.timezone.utc)
        self.channel: FakeHistoryChannel = FakeHistoryChannel(channel_id)


class FakeHistoryChannel:
    def __init__(self, channel_id: int = 333) -> None:
        self.id: int = channel_id
        self.history_messages: list[FakeMessage] = []
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)

    def history(self, *, limit: int) -> AsyncIterator[FakeMessage]:
        async def iterator() -> AsyncIterator[FakeMessage]:
            for message in self.history_messages[:limit]:
                yield message

        return iterator()


class ProcessDiscordMessageFunc(Protocol):
    def __call__(
        self,
        client: bot.CodexDiscordBot,
        message: FakeMessage,
        *,
        source: str,
    ) -> Awaitable[None]: ...


class PollHistoryChannelFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, label: str, channel_id: int) -> Awaitable[None]: ...


class OnMessageFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, message: FakeMessage) -> Awaitable[None]: ...


PROCESS_DISCORD_MESSAGE = cast(ProcessDiscordMessageFunc, bot.CodexDiscordBot.process_discord_message)
POLL_HISTORY_CHANNEL = cast(PollHistoryChannelFunc, bot.CodexDiscordBot.poll_history_channel)
ON_MESSAGE = cast(OnMessageFunc, bot.CodexDiscordBot.on_message)


class FakePollClient:
    def __init__(self, channel: FakeHistoryChannel, *, bootstrap_after: dt.datetime | None = None) -> None:
        self._processed_message_ids: dict[int, set[int]] = {}
        self._history_poll_primed_channels: set[int] = set()
        self._history_poll_bootstrap_after: dt.datetime | None = bootstrap_after
        self.enable_prefix_commands: bool = True
        self.channel: FakeHistoryChannel = channel

    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[FakeHistoryChannel, str]:
        _ = channel_id
        return self.channel, "test_cache"

    async def fetch_channel(self, channel_id: int) -> FakeHistoryChannel:
        _ = channel_id
        raise AssertionError("fetch not expected")

    def is_allowed_message_channel(self, message_channel: FakeHistoryChannel) -> bool:
        _ = message_channel
        return True

    def is_allowed_user(self, user_id: int) -> bool:
        _ = user_id
        return True

    async def process_discord_message(self, message: FakeMessage, *, source: str) -> None:
        await PROCESS_DISCORD_MESSAGE(cast(bot.CodexDiscordBot, self), message, source=source)


class FailingPollClient(FakePollClient):
    def __init__(self, channel: FakeHistoryChannel, *, fail_message_id: int) -> None:
        super().__init__(channel)
        self.fail_message_id = fail_message_id
        self.processed_attempts: list[int | None] = []

    async def process_discord_message(self, message: FakeMessage, *, source: str) -> None:
        self.assert_history_source(source)
        self.processed_attempts.append(message.id)
        if message.id == self.fail_message_id:
            raise TypeError("injected history processing failure")

    @staticmethod
    def assert_history_source(source: str) -> None:
        if source != "history_poll":
            raise AssertionError(f"unexpected source: {source}")


LogAction = Callable[[Path], Awaitable[None]]


class DiscordHistoryPollPrimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        old_mirror_db_path = bot.MIRROR_DB_PATH
        mirror_db_temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(mirror_db_temp_dir.cleanup)
        self.addCleanup(setattr, bot, "MIRROR_DB_PATH", old_mirror_db_path)
        bot.MIRROR_DB_PATH = Path(mirror_db_temp_dir.name) / "mirror.sqlite"
        bot.init_mirror_db()

    async def _run_with_log(self, action: LogAction) -> str:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            log_path = Path(temp_dir) / "discord-smoke.log"
            with mock.patch.dict(os.environ, {"CODEX_DISCORD_LOG_PATH": str(log_path)}):
                await action(log_path)
            return log_path.read_text(encoding="utf-8")

    async def test_history_poll_primes_then_processes_new_user_message_once(self) -> None:
        handled: list[tuple[str, str | None]] = []
        channel = FakeHistoryChannel()
        old_message = FakeMessage("old", message_id=100)
        new_message = FakeMessage("please hook", message_id=101)
        old_message.channel = channel
        new_message.channel = channel
        client = FakePollClient(channel)

        async def runner_idle(target_thread_id: str | None) -> bool:
            _ = target_thread_id
            return False

        async def fake_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = message
            handled.append((prompt, target_thread_id))

        def mirror_thread_id(channel_id: int) -> str:
            _ = channel_id
            return "thread-1"

        def busy_state(target_thread_id: str | None) -> tuple[str, None, str]:
            _ = target_thread_id
            return "idle", None, ""

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            channel.history_messages = [old_message]
            bot_client = cast(bot.CodexDiscordBot, client)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            channel.history_messages = [new_message, old_message]
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            await ON_MESSAGE(bot_client, new_message)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", mirror_thread_id),
            mock.patch.object(bot, "get_busy_state_for_thread", busy_state),
            mock.patch.object(bot, "is_thread_runner_busy", runner_idle),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            log_text = await self._run_with_log(run_poll)

        self.assertEqual(handled, [("please hook", "thread-1")])
        self.assertIn("history_poll_primed label=allowed channel=333", log_text)
        self.assertIn("history_poll_message channel=333", log_text)
        self.assertIn("message_received chat=333", log_text)
        self.assertIn("source=history_poll", log_text)
        self.assertIn("duplicate_message_skipped source=gateway chat=333 message=101", log_text)

    async def test_history_poll_first_prime_processes_bootstrap_user_messages(self) -> None:
        handled: list[tuple[str, str | None]] = []
        channel = FakeHistoryChannel()
        cutoff = dt.datetime(2026, 6, 3, 15, 0, tzinfo=dt.timezone.utc)
        old_message = FakeMessage("old", message_id=100, created_at=cutoff - dt.timedelta(seconds=1))
        fresh_message = FakeMessage("bootstrap hook", message_id=101, created_at=cutoff + dt.timedelta(seconds=1))
        old_message.channel = channel
        fresh_message.channel = channel
        channel.history_messages = [fresh_message, old_message]
        client = FakePollClient(channel, bootstrap_after=cutoff)

        async def runner_idle(target_thread_id: str | None) -> bool:
            _ = target_thread_id
            return False

        async def fake_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = message
            handled.append((prompt, target_thread_id))

        def mirror_thread_id(channel_id: int) -> str:
            _ = channel_id
            return "thread-1"

        def busy_state(target_thread_id: str | None) -> tuple[str, None, str]:
            _ = target_thread_id
            return "idle", None, ""

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await POLL_HISTORY_CHANNEL(cast(bot.CodexDiscordBot, client), "allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", mirror_thread_id),
            mock.patch.object(bot, "get_busy_state_for_thread", busy_state),
            mock.patch.object(bot, "is_thread_runner_busy", runner_idle),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            log_text = await self._run_with_log(run_poll)

        self.assertEqual(handled, [("bootstrap hook", "thread-1")])
        self.assertIn("history_poll_primed label=allowed channel=333", log_text)
        self.assertIn("bootstrap_user_messages=1", log_text)
        self.assertIn("history_poll_message channel=333", log_text)
        self.assertIn("source=history_poll", log_text)
        self.assertNotIn("old", log_text)

    async def test_history_processing_failure_releases_current_and_later_claims(
        self,
    ) -> None:
        channel = FakeHistoryChannel()
        first = FakeMessage("first", message_id=101)
        failing = FakeMessage("failing", message_id=102)
        later = FakeMessage("later", message_id=103)
        for message in (first, failing, later):
            message.channel = channel
        client = FailingPollClient(channel, fail_message_id=102)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await POLL_HISTORY_CHANNEL(cast(bot.CodexDiscordBot, client), "allowed", 333)
            channel.history_messages = [later, failing, first]
            with self.assertRaisesRegex(
                TypeError, "injected history processing failure"
            ):
                await POLL_HISTORY_CHANNEL(
                    cast(bot.CodexDiscordBot, client), "allowed", 333
                )

        log_text = await self._run_with_log(run_poll)
        restarted_owner = cast(
            bot.SeenCacheOwner,
            cast(object, type("RestartedOwner", (), {"_processed_message_ids": {}})()),
        )

        self.assertEqual(client.processed_attempts, [101, 102])
        self.assertFalse(bot.claim_discord_message(restarted_owner, first))
        self.assertTrue(bot.claim_discord_message(restarted_owner, failing))
        self.assertTrue(bot.claim_discord_message(restarted_owner, later))
        self.assertIn(
            "history_message_process_failed channel=333 error_type=TypeError released=2",
            log_text,
        )


if __name__ == "__main__":
    _ = unittest.main()
