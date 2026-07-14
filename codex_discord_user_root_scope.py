"""Shared ordinary user-root scope for Discord command surfaces."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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


def exclude_gpt_registered_threads(
    threads: Sequence[ThreadInfo],
    *,
    db_path: Path,
    load_ids: LoadGptThreadIds = gpt_registration_store.load_gpt_registered_thread_ids_read_only,
) -> list[ThreadInfo]:
    gpt_thread_ids = load_gpt_registered_thread_ids(db_path, load_ids=load_ids)
    return [thread for thread in threads if thread.id not in gpt_thread_ids]


def load_ordinary_user_root_threads(
    load_user_root_threads: LoadUserRootThreads,
    *,
    db_path: Path = DEFAULT_MIRROR_DB_PATH,
    limit: int = 0,
    load_ids: LoadGptThreadIds = gpt_registration_store.load_gpt_registered_thread_ids_read_only,
) -> list[ThreadInfo]:
    threads = exclude_gpt_registered_threads(
        load_user_root_threads(0),
        db_path=db_path,
        load_ids=load_ids,
    )
    if limit > 0:
        return threads[:limit]
    return threads
