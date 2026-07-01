from __future__ import annotations

from pathlib import Path
import unittest

import codex_discord_prompt_rewrite as prompt_rewrite


class PromptRewriteTests(unittest.TestCase):
    def test_rewrite_prompt_keeps_dollar_prefixed_prompt(self) -> None:
        for prompt in ["$custom \uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574", "$mirror-check hello"]:
            with self.subTest(prompt=prompt):
                logs: list[str] = []

                result = prompt_rewrite.rewrite_prompt(
                    prompt,
                    cwd=Path.cwd(),
                    log=logs.append,
                )

                self.assertEqual(result.prompt, prompt)
                self.assertEqual(result.visible_line, "")
                self.assertEqual(logs, [])

    def test_rewrite_prompt_keeps_plain_korean_prompt(self) -> None:
        logs: list[str] = []

        result = prompt_rewrite.rewrite_prompt(
            "\uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574",
            cwd=Path.cwd(),
            log=logs.append,
        )

        self.assertEqual(result.prompt, "\uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574")
        self.assertEqual(result.visible_line, "")
        self.assertEqual(logs, [])


if __name__ == "__main__":
    _ = unittest.main()
