from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
import tempfile
import unittest

import codex_discord_bot as bot

from tests.test_codex_discord_bot import EnvPatch, FakeMessage


def _prefix_bot() -> bot.CodexDiscordBot:
    return cast(bot.CodexDiscordBot, cast(object, SimpleNamespace()))


def _discord_message(message: FakeMessage) -> bot.discord.Message:
    return cast(bot.discord.Message, cast(object, message))


class PrefixSkillPromptBotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_interview_wraps_request_for_deep_interview(self) -> None:
        original_get_mirrored = bot.get_mirrored_codex_thread_id
        original_handle_plain_ask = bot.handle_plain_ask
        calls: list[tuple[bot.discord.Message, str, str | None]] = []

        async def fake_handle_plain_ask(
            message: bot.discord.Message,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            calls.append((message, prompt, target_thread_id))

        try:
            bot.get_mirrored_codex_thread_id = lambda channel_id: "thread-1"
            bot.handle_plain_ask = fake_handle_plain_ask
            message = FakeMessage(content="!interview build a dashboard", channel_id=222)

            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "discord-smoke.log"
                with EnvPatch("CODEX_DISCORD_LOG_PATH", str(log_path)):
                    await bot.handle_prefix_command(
                        _prefix_bot(),
                        _discord_message(message),
                        "interview build a dashboard",
                    )
                log_text = log_path.read_text(encoding="utf-8")

            self.assertEqual(len(calls), 1)
            source_message, prompt, target_thread_id = calls[0]
            self.assertIs(source_message, message)
            self.assertEqual(target_thread_id, "thread-1")
            self.assertIn("Run a Gajae-style deep interview before implementation.", prompt)
            self.assertIn("Round 0: enumerate 1-6 top-level components", prompt)
            self.assertIn("Deep Interview threshold: 0.05 (source: default)", prompt)
            self.assertIn("ontology stability", prompt)
            self.assertIn("User request:\nbuild a dashboard", prompt)
            self.assertIn("prefix_interview channel=222", log_text)
            self.assertIn("target=thread-1", log_text)
        finally:
            bot.get_mirrored_codex_thread_id = original_get_mirrored
            bot.handle_plain_ask = original_handle_plain_ask

    async def test_prefix_github_triage_wraps_request_for_upstream_skill(self) -> None:
        original_get_mirrored = bot.get_mirrored_codex_thread_id
        original_handle_plain_ask = bot.handle_plain_ask
        calls: list[tuple[bot.discord.Message, str, str | None]] = []

        async def fake_handle_plain_ask(
            message: bot.discord.Message,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            calls.append((message, prompt, target_thread_id))

        try:
            bot.get_mirrored_codex_thread_id = lambda channel_id: "thread-1"
            bot.handle_plain_ask = fake_handle_plain_ask
            message = FakeMessage(content="!triage current repo", channel_id=222)

            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "discord-smoke.log"
                with EnvPatch("CODEX_DISCORD_LOG_PATH", str(log_path)):
                    await bot.handle_prefix_command(
                        _prefix_bot(),
                        _discord_message(message),
                        "triage current repo",
                    )
                log_text = log_path.read_text(encoding="utf-8")

            self.assertEqual(len(calls), 1)
            source_message, prompt, target_thread_id = calls[0]
            self.assertIs(source_message, message)
            self.assertEqual(target_thread_id, "thread-1")
            self.assertIn("$codex-discord-harness:github-project-triage", prompt)
            self.assertIn("Do not run deep-interview", prompt)
            self.assertIn("User request:\ncurrent repo", prompt)
            self.assertIn("prefix_github_triage channel=222", log_text)
        finally:
            bot.get_mirrored_codex_thread_id = original_get_mirrored
            bot.handle_plain_ask = original_handle_plain_ask

    async def test_prefix_maintainer_orchestrator_wraps_request_for_upstream_skill(self) -> None:
        original_get_mirrored = bot.get_mirrored_codex_thread_id
        original_handle_plain_ask = bot.handle_plain_ask
        calls: list[tuple[bot.discord.Message, str, str | None]] = []

        async def fake_handle_plain_ask(
            message: bot.discord.Message,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            calls.append((message, prompt, target_thread_id))

        try:
            bot.get_mirrored_codex_thread_id = lambda channel_id: "thread-1"
            bot.handle_plain_ask = fake_handle_plain_ask
            message = FakeMessage(content="!orchestrate inspect queue", channel_id=222)

            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "discord-smoke.log"
                with EnvPatch("CODEX_DISCORD_LOG_PATH", str(log_path)):
                    await bot.handle_prefix_command(
                        _prefix_bot(),
                        _discord_message(message),
                        "orchestrate inspect queue",
                    )
                log_text = log_path.read_text(encoding="utf-8")

            self.assertEqual(len(calls), 1)
            _source_message, prompt, target_thread_id = calls[0]
            self.assertEqual(target_thread_id, "thread-1")
            self.assertIn("$codex-discord-harness:maintainer-orchestrator", prompt)
            self.assertIn("Do not run deep-interview", prompt)
            self.assertIn("User request:\ninspect queue", prompt)
            self.assertIn("prefix_maintainer_orchestrator channel=222", log_text)
        finally:
            bot.get_mirrored_codex_thread_id = original_get_mirrored
            bot.handle_plain_ask = original_handle_plain_ask

    async def test_prefix_maintainer_orchestrator_requires_request(self) -> None:
        message = FakeMessage(content="!orchestrate", channel_id=222)

        await bot.handle_prefix_command(
            _prefix_bot(),
            _discord_message(message),
            "orchestrate",
        )

        self.assertEqual(message.channel.messages[-1][0], "Usage: !orchestrate <request>")


if __name__ == "__main__":
    unittest.main()
