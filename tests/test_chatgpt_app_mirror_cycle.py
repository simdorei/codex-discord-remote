from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from chatgpt_app_mirror_cycle import mark_mirror_delivery, prepare_mirror_cycle
from chatgpt_app_mirror_models import (
    ChatGptConversation,
    ChatGptRole,
    ChatGptSnapshot,
    ChatGptTurn,
)


THREAD_IDS = (101, 102, 103, 104, 105)


def _snapshot(*turns: ChatGptTurn, active: str = "c2", reverse: bool = False) -> ChatGptSnapshot:
    conversations = tuple(ChatGptConversation(f"c{index}", f"title {index}") for index in range(1, 6))
    if reverse:
        conversations = tuple(reversed(conversations))
    return ChatGptSnapshot(
        recent_conversations=conversations,
        active_conversation_id=active,
        turns=turns,
    )


class ChatGptAppMirrorCycleTests(unittest.TestCase):
    def test_first_selected_snapshot_is_baselined_then_only_new_turns_are_mirrored(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "mirror.sqlite"
            existing_user = ChatGptTurn("u1", ChatGptRole.USER, "old question")
            existing_assistant = ChatGptTurn("a1", ChatGptRole.ASSISTANT, "old answer")

            baseline = prepare_mirror_cycle(db_path, _snapshot(existing_user, existing_assistant), THREAD_IDS)
            self.assertTrue(baseline.primed)
            self.assertEqual(baseline.deliveries, ())

            new_user = ChatGptTurn("u2", ChatGptRole.USER, "new question")
            new_assistant = ChatGptTurn("a2", ChatGptRole.ASSISTANT, "new answer")
            pending = prepare_mirror_cycle(
                db_path,
                _snapshot(existing_user, existing_assistant, new_user, new_assistant),
                THREAD_IDS,
            )

            self.assertFalse(pending.primed)
            self.assertEqual([item.discord_thread_id for item in pending.deliveries], [102, 102])
            self.assertEqual([item.turn.message_id for item in pending.deliveries], ["u2", "a2"])

            _ = mark_mirror_delivery(db_path, pending.deliveries[0])
            remaining = prepare_mirror_cycle(
                db_path,
                _snapshot(existing_user, existing_assistant, new_user, new_assistant),
                THREAD_IDS,
            )
            self.assertEqual([item.turn.message_id for item in remaining.deliveries], ["a2"])

    def test_incomplete_streaming_assistant_waits_until_complete(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "mirror.sqlite"
            _ = prepare_mirror_cycle(db_path, _snapshot(), THREAD_IDS)
            partial = ChatGptTurn("a1", ChatGptRole.ASSISTANT, "partial", complete=False)

            streaming = prepare_mirror_cycle(db_path, _snapshot(partial), THREAD_IDS)
            self.assertEqual(streaming.deliveries, ())

            completed = prepare_mirror_cycle(
                db_path,
                _snapshot(ChatGptTurn("a1", ChatGptRole.ASSISTANT, "complete")),
                THREAD_IDS,
            )
            self.assertEqual([item.turn.text for item in completed.deliveries], ["complete"])

    def test_slot_mapping_stays_frozen_when_recent_order_changes(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "mirror.sqlite"
            _ = prepare_mirror_cycle(db_path, _snapshot(), THREAD_IDS)
            _ = prepare_mirror_cycle(db_path, _snapshot(active="c5", reverse=True), THREAD_IDS)

            pending = prepare_mirror_cycle(
                db_path,
                _snapshot(ChatGptTurn("u1", ChatGptRole.USER, "hello"), active="c5", reverse=True),
                THREAD_IDS,
            )

            self.assertEqual([item.discord_thread_id for item in pending.deliveries], [105])

    def test_active_conversation_outside_frozen_five_is_not_mirrored(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "mirror.sqlite"
            _ = prepare_mirror_cycle(db_path, _snapshot(), THREAD_IDS)

            plan = prepare_mirror_cycle(
                db_path,
                _snapshot(ChatGptTurn("u1", ChatGptRole.USER, "new chat"), active="c6"),
                THREAD_IDS,
            )

            self.assertFalse(plan.active_mapped)
            self.assertEqual(plan.deliveries, ())


if __name__ == "__main__":
    _ = unittest.main()
