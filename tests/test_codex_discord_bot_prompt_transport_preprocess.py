from __future__ import annotations

from types import ModuleType
import unittest

import codex_discord_bot_prompt_transport_preprocess as preprocess


class PromptTransportPreprocessTests(unittest.TestCase):
    def test_preprocessor_keeps_dollar_skill_command_for_codex_app_hooks(self) -> None:
        preprocessor = preprocess.make_prompt_preprocessor(ModuleType("fake_bot_module"))

        result = preprocessor("$kor \uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574")

        self.assertEqual(result.prompt, "$kor \uc870\uc0ac\uae4c\uc9c0\ub9cc\ud574")
        self.assertEqual(result.visible_line, "")


if __name__ == "__main__":
    _ = unittest.main()
