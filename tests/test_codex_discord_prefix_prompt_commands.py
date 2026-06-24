import unittest
from dataclasses import dataclass

import codex_discord_prefix_prompt_commands as prefix_prompt


@dataclass(frozen=True)
class FakeChannel:
    id: int = 222


@dataclass(frozen=True)
class FakeAuthor:
    id: int = 333


@dataclass(frozen=True)
class FakeMessage:
    channel: FakeChannel
    author: FakeAuthor

    @classmethod
    def make(cls, channel_id: int = 222, user_id: int = 333) -> "FakeMessage":
        return cls(channel=FakeChannel(channel_id), author=FakeAuthor(user_id))


class PrefixPromptCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_deps(
        self,
        *,
        mirrored_thread_id: str | None = "thread-1",
        project_message: str = "",
    ) -> tuple[prefix_prompt.PrefixPromptCommandDeps, list[str], list[tuple[object, str, str | None]], list[str]]:
        sent: list[str] = []
        asks: list[tuple[object, str, str | None]] = []
        logs: list[str] = []

        async def send_chunks(target: object, text: str, *, context: str = "send_chunks") -> object:
            _ = target
            sent.append(f"{context}:{text}")
            return len(text)

        async def handle_plain_ask(message: object, prompt: str, *, target_thread_id: str | None = None) -> None:
            asks.append((message, prompt, target_thread_id))

        deps = prefix_prompt.PrefixPromptCommandDeps(
            send_chunks=send_chunks,
            handle_plain_ask=handle_plain_ask,
            get_mirrored_codex_thread_id=lambda channel_id: mirrored_thread_id,
            describe_mirrored_project_channel=lambda channel_id: project_message,
            log_line=logs.append,
            format_log_text_len=lambda text: str(len(str(text or ""))),
            format_discord_command_label=lambda command: command.replace("_", "-"),
        )
        return deps, sent, asks, logs

    async def test_dispatches_prompt_command_aliases_to_plain_ask(self) -> None:
        deps, sent, asks, logs = self.make_deps()
        message = FakeMessage.make(channel_id=222, user_id=444)

        for command, arg in [
            ("interview", "build a dashboard"),
            ("github-triage", "current repo"),
            ("maintainer", "inspect queue"),
            ("ask_ipc", "hello"),
        ]:
            handled = await prefix_prompt.handle_prefix_prompt_command(command, arg, message, deps=deps)
            self.assertTrue(handled)

        self.assertEqual(sent, [])
        self.assertEqual([call[2] for call in asks], ["thread-1", "thread-1", "thread-1", "thread-1"])
        self.assertIn("Run a Gajae-style deep interview before implementation.", asks[0][1])
        self.assertIn("$codex-discord-harness:github-project-triage", asks[1][1])
        self.assertIn("$codex-discord-harness:maintainer-orchestrator", asks[2][1])
        self.assertEqual(asks[3][1], "hello")
        self.assertTrue(any("prefix_interview channel=222 user=444 target=thread-1" in line for line in logs))
        self.assertTrue(any("prefix_github_triage channel=222 user=444 target=thread-1" in line for line in logs))
        self.assertTrue(any("prefix_maintainer_orchestrator channel=222 user=444 target=thread-1" in line for line in logs))

    async def test_preserves_missing_args_project_guidance_default_and_unhandled(self) -> None:
        deps, sent, asks, _logs = self.make_deps(mirrored_thread_id=None, project_message="Use a mapped thread.")
        message = FakeMessage.make()

        self.assertTrue(await prefix_prompt.handle_prefix_prompt_command("interview", "", message, deps=deps))
        self.assertEqual(sent[-1], "prefix_interview_usage:Usage: !interview <request>")

        self.assertTrue(await prefix_prompt.handle_prefix_prompt_command("maintainer", "", message, deps=deps))
        self.assertEqual(sent[-1], "prefix_maintainer_orchestrator_usage:Usage: !maintainer <request>")

        self.assertTrue(await prefix_prompt.handle_prefix_prompt_command("ask", "hello", message, deps=deps))
        self.assertEqual(sent[-1], "send_chunks:Use a mapped thread.")
        self.assertEqual(asks, [])

        deps, sent, asks, _logs = self.make_deps()
        self.assertTrue(await prefix_prompt.handle_prefix_prompt_command("triage", "", message, deps=deps))
        self.assertIn("triage the current GitHub project", asks[-1][1])

        self.assertFalse(await prefix_prompt.handle_prefix_prompt_command("where", "", message, deps=deps))
