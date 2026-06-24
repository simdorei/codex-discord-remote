from __future__ import annotations

import unittest

import codex_discord_bot as bot
import codex_discord_session_mirror as session_mirror
from codex_session_events import JsonEvent


class SessionMirrorItemCollectionIntegrationTests(unittest.TestCase):
    def test_collect_session_mirror_items_skips_discord_echo_and_duplicate_commentary(self) -> None:
        old_prompts = dict(bot.get_runtime_state().recent_discord_origin_prompts)
        try:
            bot.get_runtime_state().recent_discord_origin_prompts.clear()
            bot.mark_recent_discord_origin_prompt("thread-1", "from discord")
            events: list[JsonEvent] = [
                {
                    "timestamp": "1",
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "from discord"},
                },
                {
                    "timestamp": "2",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "from app"}],
                    },
                },
                {
                    "timestamp": "3",
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "phase": "commentary", "message": "working"},
                },
                {
                    "timestamp": "4",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "commentary",
                        "content": [{"type": "output_text", "text": "working"}],
                    },
                },
                {
                    "timestamp": "5",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                },
            ]

            items = bot.collect_session_mirror_items(
                "thread-1",
                events,
                seen_agent_messages={},
                seen_user_messages={},
            )

            self.assertEqual([item["kind"] for item in items], ["user", "commentary", "final"])
            self.assertEqual([item["text"] for item in items], ["from app", "working", "done"])
        finally:
            bot.get_runtime_state().recent_discord_origin_prompts.clear()
            bot.get_runtime_state().recent_discord_origin_prompts.update(old_prompts)

    def test_collect_session_mirror_items_skips_internal_user_context(self) -> None:
        user_text = "AGENTS.md \ub0b4\uc6a9 \uc124\uba85\ud574\uc918"
        events: list[JsonEvent] = [
            {
                "timestamp": "1",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "# AGENTS.md instructions for C:\\repos\\simdorei\\codex-discord-harness\n\n<INSTRUCTIONS>",
                        }
                    ],
                },
            },
            {
                "timestamp": "2",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": '<codex_internal_context source="goal">\nContinue working toward the active thread goal.',
                        }
                    ],
                },
            },
            {
                "timestamp": "3",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": user_text},
            },
            {
                "timestamp": "4",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "from app"}],
                },
            },
        ]

        items = bot.collect_session_mirror_items(
            "thread-1",
            events,
            seen_agent_messages={},
            seen_user_messages={},
        )

        self.assertEqual([item["kind"] for item in items], ["user", "user"])
        self.assertEqual([item["text"] for item in items], [user_text, "from app"])

    def test_collect_session_mirror_items_keeps_event_msg_final_without_duplicate(self) -> None:
        events: list[JsonEvent] = [
            {
                "timestamp": "1",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "final_answer", "message": "done"},
            },
            {
                "timestamp": "2",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            },
        ]

        items = bot.collect_session_mirror_items(
            "thread-1",
            events,
            seen_agent_messages={},
            seen_user_messages={},
        )

        self.assertEqual([item["kind"] for item in items], ["final"])
        self.assertEqual([item["text"] for item in items], ["done"])

    def test_collect_session_mirror_items_surfaces_aborted_event_details(self) -> None:
        events: list[JsonEvent] = [
            {
                "timestamp": "1",
                "type": "event_msg",
                "payload": {
                    "type": "turn_aborted",
                    "turn_id": "turn-1",
                    "reason": "interrupted",
                    "duration_ms": 123,
                },
            },
        ]

        items = bot.collect_session_mirror_items(
            "thread-1",
            events,
            seen_agent_messages={},
            seen_user_messages={},
        )

        self.assertEqual([item["kind"] for item in items], ["aborted"])
        self.assertEqual(items[0]["phase"], "turn_aborted")
        self.assertIn("Codex turn aborted.", items[0]["text"])
        self.assertIn("reason=interrupted", items[0]["text"])
        self.assertIn("turn_id=turn-1", items[0]["text"])
        self.assertIn("duration_ms=123", items[0]["text"])
        self.assertEqual(session_mirror.format_session_mirror_text(items[0]), items[0]["text"])

    def test_collect_session_mirror_items_surfaces_completed_turn_without_visible_reply(self) -> None:
        events: list[JsonEvent] = [
            {
                "timestamp": "1",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "ping"},
            },
            {
                "timestamp": "2",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "ping"}],
                },
            },
            {
                "timestamp": "3",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-1",
                    "last_agent_message": None,
                    "duration_ms": 2500,
                },
            },
        ]

        items = bot.collect_session_mirror_items(
            "thread-1",
            events,
            seen_agent_messages={},
            seen_user_messages={},
        )

        self.assertEqual([item["kind"] for item in items], ["user", "final"])
        self.assertEqual(items[-1]["phase"], "no_visible_reply")
        self.assertIn("Codex turn completed without a visible reply.", items[-1]["text"])
        self.assertIn("turn_id=turn-1", items[-1]["text"])
        self.assertIn("duration_ms=2500", items[-1]["text"])


if __name__ == "__main__":
    _ = unittest.main()
