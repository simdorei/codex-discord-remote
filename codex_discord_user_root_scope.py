"""Shared ordinary user-root scope for Discord command surfaces."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final, TypeAlias

import codex_discord_gpt_registration_store as gpt_registration_store
from codex_thread_models import ThreadInfo


DEFAULT_MIRROR_DB_PATH: Final = Path(__file__).resolve().parent / "discord_mirror.sqlite"
GPT_PROJECT_KEY: Final = "codex:chats"

LoadUserRootThreads: TypeAlias = Callable[[int], list[ThreadInfo]]
LoadGptThreadIds: TypeAlias = Callable[[Path], frozenset[str]]


def load_gpt_registered_thread_ids(
    db_path: Path,
    *,
    load_ids: LoadGptThreadIds = gpt_registration_store.load_gpt_registered_thread_ids_read_only,
) -> frozenset[str]:
    return load_ids(db_path)


def load_ordinary_user_root_threads(
    load_user_root_threads: LoadUserRootThreads,
    *,
    db_path: Path = DEFAULT_MIRROR_DB_PATH,
    limit: int = 0,
    load_ids: LoadGptThreadIds = gpt_registration_store.load_gpt_registered_thread_ids_read_only,
) -> list[ThreadInfo]:
    _ = (db_path, load_ids)
    threads = load_user_root_threads(0)
    if limit > 0:
        return threads[:limit]
    return threads
