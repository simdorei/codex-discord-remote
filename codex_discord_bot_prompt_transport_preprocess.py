from __future__ import annotations

from types import ModuleType
from typing import cast

import codex_discord_prompt_mapped_delivery as mapped_delivery


def make_prompt_preprocessor(module: ModuleType) -> mapped_delivery.PromptPreprocessor:
    _ = module
    return mapped_delivery.keep_prompt


def make_discord_origin_prompt_marker(module: ModuleType) -> mapped_delivery.DiscordOriginPromptMarker:
    return cast(
        mapped_delivery.DiscordOriginPromptMarker,
        getattr(module, "mark_recent_discord_origin_prompt"),
    )
