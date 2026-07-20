from __future__ import annotations

from types import ModuleType
import unittest

from chatgpt_app_mirror_models import (
    ChatGptMirrorDelivery,
    ChatGptRole,
    ChatGptTurn,
)
from chatgpt_app_cdp import CdpContractError
from chatgpt_app_mirror_store import ChatGptMirrorStoreConfigError
from codex_discord_bot_chatgpt_mirror_adapter_runtime import (
    BotChatGptMirrorAdapterRuntime,
    ChatGptMirrorChannelUnavailable,
)


class FakeChannel:
    pass


class FakeOwner:
    def __init__(self, channel: FakeChannel | None) -> None:
        self.channel: FakeChannel | None = channel
        self.resolved_ids: list[int] = []

    def is_closed(self) -> bool:
        return False

    async def resolve_session_mirror_channel(
        self,
        discord_thread_id: int,
    ) -> FakeChannel | None:
        self.resolved_ids.append(discord_thread_id)
        return self.channel

    async def send_chatgpt_mirror_delivery(
        self,
        delivery: ChatGptMirrorDelivery,
    ) -> None:
        raise AssertionError(f"unexpected recursive delivery: {delivery}")


class ChatGptMirrorAdapterRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_poll_failures_remain_retryable(self) -> None:
        module = ModuleType("fake_chatgpt_mirror_bot")
        setattr(module, "DISCORD_DELIVERY_EXCEPTIONS", ())
        runtime = BotChatGptMirrorAdapterRuntime(module).make_runtime()

        self.assertIn(CdpContractError, runtime.deps.expected_exceptions)
        self.assertIn(ChatGptMirrorStoreConfigError, runtime.deps.expected_exceptions)
        self.assertIn(ChatGptMirrorChannelUnavailable, runtime.deps.expected_exceptions)

    async def test_sends_formatted_message_to_configured_discord_thread(self) -> None:
        module = ModuleType("fake_chatgpt_mirror_bot")
        sent: list[tuple[FakeChannel, str, str]] = []

        async def send_prompt_chunks(
            channel: FakeChannel,
            text: str,
            *,
            context: str,
        ) -> None:
            sent.append((channel, text, context))

        setattr(module, "send_prompt_chunks", send_prompt_chunks)
        channel = FakeChannel()
        owner = FakeOwner(channel)
        runtime = BotChatGptMirrorAdapterRuntime(module)
        delivery = ChatGptMirrorDelivery(
            "c1",
            987,
            ChatGptTurn("a1", ChatGptRole.ASSISTANT, "answer"),
        )

        await runtime.send_delivery(owner, delivery)

        self.assertEqual(owner.resolved_ids, [987])
        self.assertEqual(
            sent,
            [(channel, "**GPT chat · ChatGPT**\nanswer", "chatgpt_app_mirror")],
        )

    async def test_missing_discord_thread_surfaces_failure(self) -> None:
        runtime = BotChatGptMirrorAdapterRuntime(ModuleType("fake_chatgpt_mirror_bot"))
        owner = FakeOwner(None)
        delivery = ChatGptMirrorDelivery(
            "c1",
            987,
            ChatGptTurn("u1", ChatGptRole.USER, "question"),
        )

        with self.assertRaisesRegex(ChatGptMirrorChannelUnavailable, "987"):
            await runtime.send_delivery(owner, delivery)


if __name__ == "__main__":
    _ = unittest.main()
