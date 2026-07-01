from __future__ import annotations

from collections.abc import Mapping
from typing import cast
import unittest

from codex_session_events import JsonEvent, JsonValue
import codex_discord_session_mirror as session_mirror
import codex_discord_session_mirror_item_append as item_append
import codex_discord_session_mirror_item_builders as item_builders
import codex_discord_session_mirror_item_collection as item_collection


def _extract_message_text(payload: Mapping[str, JsonValue]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return str(payload.get("message") or "").strip()
    texts: list[str] = []
    for part in content:
        if isinstance(part, Mapping):
            part_mapping = cast(Mapping[str, JsonValue], part)
            texts.append(str(part_mapping.get("text") or ""))
    return "".join(texts).strip()


def _build_interactive_notice(payload: Mapping[str, JsonValue]) -> str | None:
    _ = payload
    return None


def _collect_items(
    events: list[JsonEvent],
    *,
    skip_texts: set[str] | None = None,
    seen_agent_messages: dict[str, float] | None = None,
    seen_user_messages: dict[str, float] | None = None,
) -> list[dict[str, str]]:
    def should_skip_discord_origin_prompt(_codex_thread_id: str | None, text: str) -> bool:
        return text in (skip_texts or set())

    return item_collection.collect_session_mirror_items(
        "thread-1",
        events,
        seen_agent_messages=seen_agent_messages or {},
        seen_user_messages=seen_user_messages or {},
        should_skip_discord_origin_prompt_func=should_skip_discord_origin_prompt,
        build_interactive_notice_func=_build_interactive_notice,
        extract_message_text_func=_extract_message_text,
        recent_text_ttl_seconds=600.0,
    )


class SessionMirrorTargetTests(unittest.TestCase):
    def test_parse_session_mirror_target_accepts_valid_mapping(self) -> None:
        target = session_mirror.parse_session_mirror_target(
            {
                "codex_thread_id": 123,
                "discord_thread_id": "456",
            }
        )

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.codex_thread_id, "123")
        self.assertEqual(target.discord_thread_id, 456)

    def test_parse_session_mirror_target_rejects_missing_or_malformed_mapping(self) -> None:
        cases = [
            {},
            {"codex_thread_id": "", "discord_thread_id": "456"},
            {"codex_thread_id": "thread-1"},
            {"codex_thread_id": "thread-1", "discord_thread_id": "0"},
            {"codex_thread_id": "thread-1", "discord_thread_id": "not-int"},
        ]

        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertIsNone(session_mirror.parse_session_mirror_target(candidate))


class SessionMirrorItemCollectionTests(unittest.TestCase):
    def test_collect_session_mirror_items_preserves_commentary_user_and_final_flow(self) -> None:
        events: list[JsonEvent] = [
            {
                "timestamp": "1",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "working"},
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
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            },
        ]

        items = _collect_items(events)

        self.assertEqual([item["kind"] for item in items], ["commentary", "user", "final"])
        self.assertEqual([item["text"] for item in items], ["working", "from app", "done"])

    def test_collect_session_mirror_items_preserves_edge_skips_through_public_surface(self) -> None:
        events: list[JsonEvent] = [
            {"timestamp": "1", "type": "event_msg", "payload": "not-a-dict"},
            {
                "timestamp": "2",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "   "},
            },
            {
                "timestamp": "3",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "from discord"},
            },
            {
                "timestamp": "4",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "repeat"},
            },
            {
                "timestamp": "5",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "repeat"}],
                },
            },
            {
                "timestamp": "6",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "# AGENTS.md instructions for C:\\repos\\simdorei\\codex-discord-remote\n\n<INSTRUCTIONS>",
                        }
                    ],
                },
            },
            {
                "timestamp": "7",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "kept"}],
                },
            },
            {"timestamp": "8", "type": "unsupported", "payload": {"type": "message"}},
        ]

        items = _collect_items(events, skip_texts={"from discord"})

        self.assertEqual([item["kind"] for item in items], ["commentary", "user"])
        self.assertEqual([item["text"] for item in items], ["repeat", "kept"])

    def test_collect_session_mirror_items_preserves_aborted_and_rejected_items(self) -> None:
        events: list[JsonEvent] = [
            {
                "timestamp": "1",
                "type": "event_msg",
                "payload": {
                    "type": "task_aborted",
                    "task_id": "task-1",
                    "reason": "operator",
                },
            },
            {
                "timestamp": "2",
                "type": "event_msg",
                "payload": {"type": "task_cancelled"},
            },
            {
                "timestamp": "3",
                "type": "response_item",
                "payload": {"type": "function_call_output", "output": "Command rejected by user"},
            },
        ]

        items = _collect_items(events)

        self.assertEqual([item["kind"] for item in items], ["aborted", "aborted", "commentary"])
        self.assertEqual(items[0]["phase"], "task_aborted")
        self.assertIn("Codex task aborted.", items[0]["text"])
        self.assertIn("task_id=task-1", items[0]["text"])
        self.assertEqual(items[1]["text"], "Codex task cancelled.")
        self.assertEqual(items[2]["phase"], "approval_rejected")
        self.assertIn("[approval_rejected]", items[2]["text"])

    def test_collect_session_mirror_items_preserves_tool_image_outputs(self) -> None:
        events: list[JsonEvent] = [
            {
                "timestamp": "1",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,aGVsbG8=",
                        }
                    ],
                },
            }
        ]

        items = _collect_items(events)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "image")
        self.assertEqual(items[0]["role"], "assistant")
        self.assertEqual(items[0]["phase"], "tool_image")
        self.assertEqual(items[0]["attachment_url"], "data:image/png;base64,aGVsbG8=")
        self.assertEqual(items[0]["attachment_filename"], "codex-image-output.png")


class SessionMirrorItemAppendTests(unittest.TestCase):
    def test_append_user_if_new_skips_discord_echo_and_remembers_text(self) -> None:
        ctx = _append_context(skip_texts={"from discord"})
        items: list[dict[str, str]] = []

        item_append.append_user_if_new(ctx, items, _event("1"), "from discord", "input")

        self.assertEqual(items, [])
        self.assertTrue(
            item_builders.has_recent_session_text(
                ctx.seen_user_messages,
                "from discord",
                ttl_seconds=600.0,
                make_text_digest_func=item_builders.make_text_digest,
            )
        )

    def test_append_agent_if_new_suppresses_duplicate_text(self) -> None:
        ctx = _append_context()
        items: list[dict[str, str]] = []

        item_append.append_agent_if_new(ctx, items, _event("1"), "working", kind="commentary", phase="commentary")
        item_append.append_agent_if_new(ctx, items, _event("2"), "working", kind="commentary", phase="commentary")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "commentary")
        self.assertEqual(items[0]["role"], "assistant")
        self.assertEqual(items[0]["text"], "working")

    def test_has_terminal_assistant_item_only_matches_final_or_aborted_assistant(self) -> None:
        self.assertFalse(item_append.has_terminal_assistant_item([{"role": "assistant", "kind": "commentary"}]))
        self.assertTrue(item_append.has_terminal_assistant_item([{"role": "assistant", "kind": "final"}]))
        self.assertTrue(item_append.has_terminal_assistant_item([{"role": "assistant", "kind": "aborted"}]))


def _append_context(skip_texts: set[str] | None = None) -> item_append.CollectionContext:
    def should_skip_discord_origin_prompt(_codex_thread_id: str | None, text: str) -> bool:
        return text in (skip_texts or set())

    return item_append.CollectionContext(
        codex_thread_id="thread-1",
        seen_agent_messages={},
        seen_user_messages={},
        should_skip_discord_origin_prompt=should_skip_discord_origin_prompt,
        build_interactive_notice=_build_interactive_notice,
        extract_message_text=_extract_message_text,
        recent_text_ttl_seconds=600.0,
        make_text_digest=item_builders.make_text_digest,
    )


def _event(timestamp: str) -> JsonEvent:
    return {"timestamp": timestamp, "type": "event_msg", "payload": {}}


if __name__ == "__main__":
    _ = unittest.main()
