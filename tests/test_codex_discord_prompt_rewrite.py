from __future__ import annotations

import unittest

from codex_app_server_transport_reply_types import JsonObject
import codex_discord_prompt_rewrite as prompt_rewrite


class PromptRewriteTests(unittest.TestCase):
    def test_prompt_may_need_rewrite_for_manual_commands_and_hangul(self) -> None:
        self.assertTrue(prompt_rewrite.prompt_may_need_rewrite("$kor \ud55c\uae00"))
        self.assertTrue(prompt_rewrite.prompt_may_need_rewrite("$gram fix this"))
        self.assertTrue(prompt_rewrite.prompt_may_need_rewrite("\ud55c\uae00 \uc9c8\ubb38"))
        self.assertFalse(prompt_rewrite.prompt_may_need_rewrite("plain English request"))

    def test_hook_success_becomes_visible_line_and_rewritten_prompt(self) -> None:
        output: JsonObject = {
            "systemMessage": "\ubc88\uc5ed: Check Discord QA (gpt-test/medium)",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Treat the rewritten English prompt as the primary user request.",
                "lazyEngStudyCodex": {
                    "status": "success",
                    "visibleLine": "\ubc88\uc5ed: Check Discord QA",
                    "assistantUnderstoodRequest": "Check Discord QA",
                },
            },
        }

        result = prompt_rewrite.prompt_result_from_hook_output(
            "$kor \ub514\uc2a4\ucf54\ub4dc QA \ud655\uc778",
            output,
        )

        self.assertEqual(result.visible_line, "\ubc88\uc5ed: Check Discord QA")
        self.assertEqual(result.prompt, "\ubc88\uc5ed: Check Discord QA")
        self.assertNotIn("Treat the rewritten English prompt", result.prompt)
        self.assertNotIn("Discord prompt:", result.prompt)
        self.assertNotIn("$kor \ub514\uc2a4\ucf54\ub4dc QA \ud655\uc778", result.prompt)

    def test_legacy_hook_output_uses_system_message_without_engine_suffix(self) -> None:
        output: JsonObject = {
            "systemMessage": "\ubc88\uc5ed: Discord QA check (gpt-5.4-mini/medium)",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "",
            },
        }

        result = prompt_rewrite.prompt_result_from_hook_output("$kor \ud655\uc778", output)

        self.assertEqual(result.visible_line, "\ubc88\uc5ed: Discord QA check")
        self.assertEqual(result.prompt, "\ubc88\uc5ed: Discord QA check")
        self.assertNotIn("Discord prompt:", result.prompt)

    def test_hook_failure_keeps_failure_context_and_original_prompt(self) -> None:
        output: JsonObject = {
            "systemMessage": "Lazy Eng Study Codex translation failed: translator timed out",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Failure: translator timed out",
            },
        }

        result = prompt_rewrite.prompt_result_from_hook_output("$kor \ud655\uc778", output)

        self.assertEqual(
            result.visible_line,
            "Lazy Eng Study Codex translation failed: translator timed out",
        )
        self.assertIn("Failure: translator timed out", result.prompt)
        self.assertIn("Discord prompt:", result.prompt)
        self.assertIn("$kor \ud655\uc778", result.prompt)


if __name__ == "__main__":
    _ = unittest.main()
