from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TypeAlias, cast
import os
import tempfile
import unittest

import codex_discord_bot as bot


class FollowupUnavailableError(RuntimeError):
    pass


class UnexpectedViewKeywordError(TypeError):
    pass


class SendUnavailableError(RuntimeError):
    pass


class FailingFollowup:
    def __init__(self, fail_after: int = 0) -> None:
        self.messages: list[str] = []
        self.fail_after = fail_after

    async def send(self, content: str, view=None, **kwargs) -> None:
        _ = (view, kwargs)
        if len(self.messages) >= self.fail_after:
            raise FollowupUnavailableError("followup unavailable")
        self.messages.append(content)


class NoViewKeywordFollowup:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.kwargs: list[dict[str, bool]] = []

    async def send(self, content: str, **kwargs: bool) -> None:
        if "view" in kwargs:
            raise UnexpectedViewKeywordError("send() got an unexpected keyword argument 'view'")
        self.messages.append(content)
        self.kwargs.append(kwargs)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.send_message_kwargs: list[dict[str, bool]] = []
        self.done = False

    async def send_message(self, content: str, ephemeral: bool = False) -> None:
        self.messages.append(content)
        self.send_message_kwargs.append({"ephemeral": ephemeral})
        self.done = True

    def is_done(self) -> bool:
        return self.done


DeliveryFollowup: TypeAlias = FailingFollowup | NoViewKeywordFollowup


class FakeInteraction:
    def __init__(self, command_name: str = "help", channel_id: int = 12345) -> None:
        self.command = SimpleNamespace(name=command_name)
        self.channel_id = channel_id
        self.followup: DeliveryFollowup = FailingFollowup(fail_after=1)
        self.response = FakeResponse()


class AlwaysFailingTarget:
    async def send(self, content: str, view=None) -> None:
        _ = (content, view)
        raise SendUnavailableError("send unavailable")


def _interaction(value: FakeInteraction) -> bot.discord.Interaction:
    return cast(bot.discord.Interaction, cast(object, value))


def _messageable(value: AlwaysFailingTarget) -> bot.discord.abc.Messageable:
    return cast(bot.discord.abc.Messageable, cast(object, value))


class DiscordInteractionDeliveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    _old_discord_log_path: str | None = None
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def setUp(self) -> None:
        self._old_discord_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._temp_dir = temp_dir
        os.environ["CODEX_DISCORD_LOG_PATH"] = str(Path(temp_dir.name) / "discord-smoke.log")
        bot.ACTIVE_DISCORD_DELIVERIES.clear()
        bot.clear_discord_delivery_stopping()

    def tearDown(self) -> None:
        if self._old_discord_log_path is None:
            os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
        else:
            os.environ["CODEX_DISCORD_LOG_PATH"] = self._old_discord_log_path
        bot.ACTIVE_DISCORD_DELIVERIES.clear()
        bot.clear_discord_delivery_stopping()
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def _log_text(self) -> str:
        return Path(os.environ["CODEX_DISCORD_LOG_PATH"]).read_text(encoding="utf-8")

    async def test_send_interaction_response_tracked_preserves_response_delivery(self) -> None:
        interaction = FakeInteraction(command_name="ask", channel_id=222)

        await bot.send_interaction_response_tracked(
            _interaction(interaction),
            "hello",
            ephemeral=True,
            context="adapter-test",
        )

        self.assertEqual(interaction.response.messages, ["hello"])
        self.assertEqual(interaction.response.send_message_kwargs, [{"ephemeral": True}])
        self.assertEqual(bot.ACTIVE_DISCORD_DELIVERIES, set())
        self.assertIn(
            "interaction_response_sent command=ask context=adapter-test "
            "channel=222 ephemeral=True text_len=5",
            self._log_text(),
        )

    async def test_send_interaction_not_allowed_preserves_denial_response(self) -> None:
        interaction = FakeInteraction(command_name="ask", channel_id=333)

        await bot.send_interaction_not_allowed(_interaction(interaction))

        self.assertEqual(interaction.response.messages, ["This channel/user is not allowed."])
        self.assertEqual(interaction.response.send_message_kwargs, [{"ephemeral": True}])
        self.assertEqual(bot.ACTIVE_DISCORD_DELIVERIES, set())
        self.assertIn(
            "interaction_response_sent command=ask context=interaction_not_allowed "
            "channel=333 ephemeral=True",
            self._log_text(),
        )

    async def test_send_followup_chunks_runtime_failure_logs_and_reraises(self) -> None:
        interaction = FakeInteraction(command_name="archive", channel_id=222)
        interaction.followup = FailingFollowup()

        with self.assertRaisesRegex(RuntimeError, "followup unavailable"):
            await bot.send_followup_chunks(
                _interaction(interaction),
                "archive output",
                title="Archive",
                log_prefix="archive_followup",
            )

        log_text = self._log_text()
        self.assertIn("archive_followup_failed command=archive title='Archive'", log_text)
        self.assertIn("error_type=FollowupUnavailableError error=followup unavailable", log_text)
        self.assertEqual(bot.ACTIVE_DISCORD_DELIVERIES, set())

    async def test_send_direct_followup_type_error_is_not_delivery_failure(self) -> None:
        interaction = FakeInteraction(command_name="ask", channel_id=222)
        interaction.followup = NoViewKeywordFollowup()

        with self.assertRaisesRegex(TypeError, "unexpected keyword argument 'view'"):
            await bot.send_direct_followup(
                _interaction(interaction),
                "hello",
                view=object(),
                context="bad-view",
            )

        log_path = Path(os.environ["CODEX_DISCORD_LOG_PATH"])
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        self.assertNotIn("direct_followup_failed", log_text)
        self.assertEqual(bot.ACTIVE_DISCORD_DELIVERIES, set())

    async def test_send_steering_start_ack_runtime_failure_logs_and_returns_false(self) -> None:
        sent = await bot.send_steering_start_ack(
            _messageable(AlwaysFailingTarget()),
            "please steer",
            "thread-1",
        )

        log_text = self._log_text()
        self.assertFalse(sent)
        self.assertIn("steering_start_ack_failed target=thread-1", log_text)
        self.assertIn("SendUnavailableError: send unavailable", log_text)


if __name__ == "__main__":
    _ = unittest.main()
