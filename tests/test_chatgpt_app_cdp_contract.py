from __future__ import annotations

import unittest

from chatgpt_app_cdp import (
    RENDERER_SNAPSHOT_FUNCTION,
    CdpContractError,
    parse_renderer_snapshot,
    select_chatgpt_cdp_target,
)
from chatgpt_app_mirror_models import ChatGptRole


class ChatGptAppCdpContractTests(unittest.TestCase):
    def test_renderer_snapshot_keeps_first_five_unique_conversations_and_turns(self) -> None:
        snapshot = parse_renderer_snapshot(
            {
                "recentConversations": [
                    {"id": "c1", "title": "one"},
                    {"id": "c1", "title": "duplicate"},
                    {"id": "c2", "title": "two"},
                    {"id": "c3", "title": "three"},
                    {"id": "c4", "title": "four"},
                    {"id": "c5", "title": "five"},
                    {"id": "c6", "title": "six"},
                ],
                "activeConversationId": "c2",
                "isStreaming": True,
                "turns": [
                    {"id": "u1", "role": "user", "text": "question", "complete": True},
                    {"id": "a1", "role": "assistant", "text": "partial", "complete": False},
                    {"id": "tool1", "role": "tool", "text": "hidden"},
                ],
            }
        )

        self.assertEqual([item.conversation_id for item in snapshot.recent_conversations], ["c1", "c2", "c3", "c4", "c5"])
        self.assertEqual(snapshot.active_conversation_id, "c2")
        self.assertEqual([turn.role for turn in snapshot.turns], [ChatGptRole.USER, ChatGptRole.ASSISTANT])
        self.assertTrue(snapshot.turns[0].complete)
        self.assertFalse(snapshot.turns[1].complete)

    def test_cdp_target_must_be_loopback_page_for_codex_or_chatgpt(self) -> None:
        target = select_chatgpt_cdp_target(
            [
                {
                    "type": "page",
                    "title": "Codex",
                    "url": "file:///app/index.html",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/main",
                }
            ]
        )

        self.assertEqual(target.websocket_url, "ws://127.0.0.1:9222/devtools/page/main")

        with self.assertRaisesRegex(CdpContractError, "loopback"):
            _ = select_chatgpt_cdp_target(
                [
                    {
                        "type": "page",
                        "title": "Codex",
                        "url": "https://chatgpt.com/c/c1",
                        "webSocketDebuggerUrl": "ws://192.0.2.10/devtools/page/main",
                    }
                ]
            )

    def test_duplicate_selected_title_surfaces_ambiguity(self) -> None:
        with self.assertRaisesRegex(CdpContractError, "duplicated"):
            _ = parse_renderer_snapshot(
                {
                    "recentConversations": [],
                    "activeConversationId": None,
                    "activeConversationAmbiguous": True,
                    "turns": [],
                }
            )

    def test_renderer_function_is_read_only(self) -> None:
        lowered = RENDERER_SNAPSHOT_FUNCTION.lower()
        for forbidden in (".click(", "fetch(", "xmlhttprequest", "localstorage", "document.cookie"):
            self.assertNotIn(forbidden, lowered)
        self.assertIn("data-chatgpt-conversation-turn", RENDERER_SNAPSHOT_FUNCTION)
        self.assertIn("data-content-search-unit-key", RENDERER_SNAPSHOT_FUNCTION)
        self.assertIn("data-assistant-message-sent-time", RENDERER_SNAPSHOT_FUNCTION)
        self.assertNotIn("data-message-author-role", RENDERER_SNAPSHOT_FUNCTION)


if __name__ == "__main__":
    _ = unittest.main()
