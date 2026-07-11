from __future__ import annotations

from dataclasses import dataclass
import unittest

import codex_discord_project_runtime as project_runtime
import codex_discord_slash_skill_prompts as skill_prompts


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
    channel_id: int | None = 201
    channel: FakeChannel | None = FakeChannel()
    user: FakeUser = FakeUser()


@dataclass(frozen=True, slots=True)
class FakeSourceMessage:
    channel: skill_prompts.PromptChannel
    author: skill_prompts.PromptUser


_SPEC = skill_prompts.SkillSlashPromptSpec(
    title="Interview",
    log_name="slash_interview",
    ack_message="Interview handling posted in this channel.",
    ack_context="interview_posted",
    build_prompt=lambda prompt: f"wrapped:{prompt}",
)


class SkillSlashPromptTests(unittest.IsolatedAsyncioTestCase):
    def _deps(
        self,
        decision: project_runtime.ExactChannelDecision,
    ) -> tuple[
        skill_prompts.SkillSlashPromptDeps[FakeSourceMessage],
        list[tuple[str, str]],
        list[tuple[str, str | None]],
        list[str],
    ]:
        sent: list[tuple[str, str]] = []
        handled: list[tuple[str, str | None]] = []
        logs: list[str] = []

        async def send_chunks(
            interaction: skill_prompts.SkillSlashInteraction,
            text: str,
            *,
            title: str,
        ) -> None:
            _ = interaction
            sent.append((title, text))

        async def send_followup(
            interaction: skill_prompts.SkillSlashInteraction,
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
            target_thread_id: str | None,
        ) -> None:
            _ = source_message
            handled.append((prompt, target_thread_id))

        def fail_fallback(_channel_id: int | None) -> str:
            self.fail("exact GPT decision reached skill fallback")

        deps = skill_prompts.SkillSlashPromptDeps(
            send_interaction_chunks=send_chunks,
            send_direct_followup=send_followup,
            handle_plain_ask=handle_plain_ask,
            get_mirrored_codex_thread_id=fail_fallback,
            describe_mirrored_project_channel=fail_fallback,
            get_interaction_command_name=lambda _interaction: "interview",
            format_log_text_len=lambda text: str(len(text)),
            make_source_message=lambda channel, user: FakeSourceMessage(channel, user),
            log_line=logs.append,
            resolve_exact_channel_decision=lambda _channel_id, _channel_name: decision,
        )
        return deps, sent, handled, logs

    async def test_exact_active_owner_routes_directly(self) -> None:
        deps, sent, handled, logs = self._deps(
            project_runtime.ExactChannelActive("gpt-active")
        )

        await skill_prompts.handle_skill_slash_prompt(
            FakeInteraction(),
            "inspect",
            spec=_SPEC,
            deps=deps,
        )

        self.assertEqual(handled, [("wrapped:inspect", "gpt-active")])
        self.assertEqual(
            sent,
            [("interview_posted", "Interview handling posted in this channel.")],
        )
        self.assertTrue(any("target_source=gpt" in line for line in logs))

    async def test_blocked_owner_stops_before_every_fallback_and_ack(self) -> None:
        deps, sent, handled, logs = self._deps(
            project_runtime.ExactChannelBlocked("gpt_creation_marker"),
        )

        await skill_prompts.handle_skill_slash_prompt(
            FakeInteraction(),
            "inspect",
            spec=_SPEC,
            deps=deps,
        )

        self.assertEqual(handled, [])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "Interview")
        self.assertTrue(any("reason=gpt_creation_marker" in line for line in logs))


if __name__ == "__main__":
    _ = unittest.main()
