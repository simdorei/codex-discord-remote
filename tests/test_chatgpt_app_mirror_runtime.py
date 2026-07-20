from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass
import unittest

from chatgpt_app_mirror_models import (
    ChatGptConversation,
    ChatGptMirrorCyclePlan,
    ChatGptMirrorDelivery,
    ChatGptRole,
    ChatGptSnapshot,
    ChatGptTurn,
)
from chatgpt_app_mirror_runtime import (
    ChatGptAppMirrorRuntime,
    ChatGptAppMirrorRuntimeDeps,
    MirrorTask,
    format_chatgpt_mirror_delivery,
)


@dataclass(slots=True)
class FakeTask:
    finished: bool = False

    def done(self) -> bool:
        return self.finished


class FakeOwner:
    def __init__(self) -> None:
        self.chatgpt_app_mirror_task: MirrorTask | None = None
        self.chatgpt_app_mirror_last_failure: str | None = None
        self.sent: list[ChatGptMirrorDelivery] = []

    def is_closed(self) -> bool:
        return False

    async def send_chatgpt_mirror_delivery(self, delivery: ChatGptMirrorDelivery) -> None:
        self.sent.append(delivery)

    @property
    def mirror_task(self) -> MirrorTask | None:
        return self.chatgpt_app_mirror_task


def _snapshot() -> ChatGptSnapshot:
    return ChatGptSnapshot(
        recent_conversations=tuple(
            ChatGptConversation(f"c{index}", f"title {index}")
            for index in range(1, 6)
        ),
        active_conversation_id="c1",
        turns=(),
    )


class ChatGptAppMirrorRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_sends_then_marks_each_delivery(self) -> None:
        owner = FakeOwner()
        snapshot = _snapshot()
        deliveries = (
            ChatGptMirrorDelivery("c1", 101, ChatGptTurn("u1", ChatGptRole.USER, "hello")),
            ChatGptMirrorDelivery("c1", 101, ChatGptTurn("a1", ChatGptRole.ASSISTANT, "hi")),
        )
        marked: list[str] = []

        async def read_snapshot() -> ChatGptSnapshot:
            return snapshot

        async def prepare_cycle(value: ChatGptSnapshot) -> ChatGptMirrorCyclePlan:
            self.assertIs(value, snapshot)
            return ChatGptMirrorCyclePlan(deliveries, active_mapped=True, primed=False)

        async def mark_delivery(delivery: ChatGptMirrorDelivery) -> bool:
            marked.append(delivery.turn.message_id)
            return True

        runtime = ChatGptAppMirrorRuntime(
            ChatGptAppMirrorRuntimeDeps(
                enabled=True,
                poll_seconds=2.0,
                read_snapshot=read_snapshot,
                prepare_cycle=prepare_cycle,
                mark_delivery=mark_delivery,
                create_task=lambda action: FakeTask(),
                sleep=_unexpected_sleep,
                expected_exceptions=(RuntimeError,),
                log=lambda message: None,
            )
        )

        await runtime.run_cycle(owner)

        self.assertEqual(owner.sent, list(deliveries))
        self.assertEqual(marked, ["u1", "a1"])

    async def test_start_is_disabled_without_creating_task(self) -> None:
        owner = FakeOwner()
        logs: list[str] = []
        runtime = ChatGptAppMirrorRuntime(
            ChatGptAppMirrorRuntimeDeps(
                enabled=False,
                poll_seconds=2.0,
                read_snapshot=_unexpected_snapshot,
                prepare_cycle=_unexpected_prepare,
                mark_delivery=_unexpected_mark,
                create_task=_unexpected_task,
                sleep=_unexpected_sleep,
                expected_exceptions=(RuntimeError,),
                log=logs.append,
            )
        )

        await runtime.start(owner)

        self.assertIsNone(owner.mirror_task)
        self.assertEqual(logs, ["chatgpt_app_mirror_disabled"])

    def test_discord_message_labels_both_roles(self) -> None:
        user = ChatGptMirrorDelivery("c1", 101, ChatGptTurn("u1", ChatGptRole.USER, "question"))
        assistant = ChatGptMirrorDelivery("c1", 101, ChatGptTurn("a1", ChatGptRole.ASSISTANT, "answer"))

        self.assertEqual(format_chatgpt_mirror_delivery(user), "**GPT chat · User**\nquestion")
        self.assertEqual(format_chatgpt_mirror_delivery(assistant), "**GPT chat · ChatGPT**\nanswer")

    def test_discord_mentions_are_rendered_without_triggering_notifications(self) -> None:
        delivery = ChatGptMirrorDelivery(
            "c1",
            101,
            ChatGptTurn(
                "a1",
                ChatGptRole.ASSISTANT,
                "@everyone @here <@123> <@!234> <@&345>",
            ),
        )

        self.assertEqual(
            format_chatgpt_mirror_delivery(delivery),
            "**GPT chat · ChatGPT**\n"
            + "@\u200beveryone @\u200bhere <@\u200b123> <@\u200b!234> <@\u200b&345>",
        )


async def _unexpected_snapshot() -> ChatGptSnapshot:
    raise AssertionError("unexpected snapshot read")


async def _unexpected_prepare(snapshot: ChatGptSnapshot) -> ChatGptMirrorCyclePlan:
    raise AssertionError(f"unexpected prepare: {snapshot}")


async def _unexpected_mark(delivery: ChatGptMirrorDelivery) -> bool:
    raise AssertionError(f"unexpected mark: {delivery}")


def _unexpected_task(action: Coroutine[object, object, None]) -> MirrorTask:
    action.close()
    raise AssertionError("unexpected task creation")


async def _unexpected_sleep(seconds: float) -> None:
    raise AssertionError(f"unexpected sleep: {seconds}")


if __name__ == "__main__":
    _ = unittest.main()
