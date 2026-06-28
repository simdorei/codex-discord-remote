from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import codex_discord_prompt_mapped_delivery as mapped_delivery


LogFunc = Callable[[str], None]


def rewrite_prompt(prompt: str, *, cwd: Path, log: LogFunc) -> mapped_delivery.PromptPreprocessResult:
    _ = cwd, log
    return mapped_delivery.keep_prompt(prompt)
