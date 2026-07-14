from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, TypeVar, cast

import codex_discord_bot as bot
import codex_discord_gpt_runtime as gpt_runtime
from codex_thread_models import ThreadInfo


ValueT = TypeVar("ValueT")


class MirrorSyncBridge(Protocol):
    CODEX_HOME: Path
    STATE_DB_PATH: Path
    load_recent_threads: Callable[[int], list[ThreadInfo]]
    load_user_root_threads: Callable[[int], list[ThreadInfo]]
    is_codex_desktop_window_title: Callable[[str], bool]


def bridge_module() -> MirrorSyncBridge:
    return cast(MirrorSyncBridge, vars(bot)["bridge"])


def codex_discord_bot(value: ValueT) -> ValueT:
    return value


@contextmanager
def isolated_mirror_store(
    db_path: Path,
    *,
    runtime_factory: Callable[[Path], gpt_runtime.GptRuntime] = gpt_runtime.GptRuntime,
) -> Generator[None, None, None]:
    """Bind both legacy and GPT runtimes to one temporary mirror store."""
    old_db_path = bot.MIRROR_DB_PATH
    old_gpt_runtime = cast(
        gpt_runtime.GptRuntime,
        getattr(bot, "GPT_RUNTIME"),
    )
    new_gpt_runtime = runtime_factory(db_path)
    try:
        bot.MIRROR_DB_PATH = db_path
        setattr(bot, "GPT_RUNTIME", new_gpt_runtime)
        yield
    finally:
        try:
            setattr(bot, "GPT_RUNTIME", old_gpt_runtime)
        finally:
            bot.MIRROR_DB_PATH = old_db_path
