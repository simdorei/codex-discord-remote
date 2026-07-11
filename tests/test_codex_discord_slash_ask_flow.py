from __future__ import annotations

from dataclasses import dataclass
import unittest

import codex_discord_project_runtime as project_runtime
import codex_discord_slash_ask_flow as slash_ask


@dataclass(frozen=True, slots=True)
class FakeUser:
    id: int = 7


@dataclass(frozen=True, slots=True)
class FakeChannel:
    id: int = 201
    name: str = "GPT channel"

    async def send(self, _content: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class FakeInteraction:
    channel: FakeChannel | None = FakeChannel()
    channel_id: int | None = 201
    user: FakeUser = FakeUser()


@dataclass(frozen=True, slots=True)
class FakeSourceMessage:
    channel: FakeChannel
    author: FakeUser


class SlashAskFlowTests(unittest.IsolatedAsyncioTestCase):
    def _deps(
        self,
        decision: project_runtime.ExactChannelDecision,
    ) -> tuple[
        slash_ask.SlashAskFlowDeps[FakeChannel, FakeUser, FakeSourceMessage],
        list[tuple[str, str]],
        list[tuple[str, str | None]],
        list[str],
    ]:
        sent: list[tuple[str, str]] = []
        handled: list[tuple[str, str | None]] = []
        logs: list[str] = []

        async def send_chunks(
            interaction: slash_ask.SlashAskInteraction[FakeChannel, FakeUser],
            text: str,
            *,
            title: str,
        ) -> None:
            _ = interaction
            sent.append((title, text))

        async def send_followup(
            interaction: slash_ask.SlashAskInteraction[FakeChannel, FakeUser],
            text: str,
            *,
            ephemeral: bool,
            log_prefix: str,
            context: str,
        ) -> None:
            _ = interaction, ephemeral, log_prefix
            sent.append((context, text))

        async def handle_plain_ask(
            source_message: FakeSourceMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = source_message
            handled.append((prompt, target_thread_id))

        def fail_fallback(_channel_id: int | None) -> str | None:
            self.fail("exact GPT decision reached slash fallback")

        deps = slash_ask.SlashAskFlowDeps(
            send_interaction_chunks=send_chunks,
            send_direct_followup=send_followup,
            handle_plain_ask=handle_plain_ask,
            get_mirrored_thread_id=fail_fallback,
            describe_project_channel=fail_fallback,
            get_command_name=lambda _interaction: "ask",
            format_text_len=len,
            is_messageable_channel=lambda _channel: True,
            make_source_message=lambda channel, user: FakeSourceMessage(channel, user),
            log=logs.append,
            resolve_exact_channel_decision=lambda _channel_id, _channel_name: decision,
        )
        return deps, sent, handled, logs

    async def test_exact_active_owner_routes_directly(self) -> None:
        deps, sent, handled, logs = self._deps(
            project_runtime.ExactChannelActive("gpt-active")
        )

        await slash_ask.handle_slash_ask(FakeInteraction(), "inspect", deps=deps)

        self.assertEqual(handled, [("inspect", "gpt-active")])
        self.assertEqual(sent, [("ask_posted", "Ask handling posted in this channel.")])
        self.assertTrue(any("target_source=gpt" in line for line in logs))

    async def test_blocked_owner_stops_before_every_fallback_and_ack(self) -> None:
        deps, sent, handled, logs = self._deps(
            project_runtime.ExactChannelBlocked("gpt_owner_inactive"),
        )

        await slash_ask.handle_slash_ask(FakeInteraction(), "inspect", deps=deps)

        self.assertEqual(handled, [])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "Ask")
        self.assertTrue(any("reason=gpt_owner_inactive" in line for line in logs))


if __name__ == "__main__":
    _ = unittest.main()
