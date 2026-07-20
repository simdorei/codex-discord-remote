from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from typing import cast

from chatgpt_app_mirror_models import (
    ChatGptConversation,
    ChatGptMirrorSlot,
    ChatGptTurn,
)
from codex_discord_store_schema import init_store_schema


class ChatGptMirrorStoreConfigError(RuntimeError):
    pass


@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def ensure_chatgpt_mirror_slots(
    db_path: Path,
    conversations: tuple[ChatGptConversation, ...],
    discord_thread_ids: tuple[int, ...],
    *,
    now: float | None = None,
) -> tuple[ChatGptMirrorSlot, ...]:
    if len(conversations) < 5:
        raise ChatGptMirrorStoreConfigError("five recent ChatGPT conversations are required")
    if len(discord_thread_ids) != 5:
        raise ChatGptMirrorStoreConfigError("exactly five Discord thread IDs are required")
    current = time.time() if now is None else now
    with _connect(db_path) as conn:
        init_store_schema(conn)
        rows = cast(
            list[tuple[int, str, int]],
            conn.execute(
                "SELECT slot_index, conversation_id, discord_thread_id "
                + "FROM chatgpt_app_mirror_slots ORDER BY slot_index"
            ).fetchall(),
        )
        if not rows:
            for slot_index, (conversation, discord_thread_id) in enumerate(
                zip(conversations[:5], discord_thread_ids, strict=True),
                start=1,
            ):
                _ = conn.execute(
                    "INSERT INTO chatgpt_app_mirror_slots "
                    + "(slot_index, conversation_id, discord_thread_id, created_at) "
                    + "VALUES (?, ?, ?, ?)",
                    (
                        slot_index,
                        conversation.conversation_id,
                        discord_thread_id,
                        current,
                    ),
                )
            rows = [
                (slot_index, conversation.conversation_id, discord_thread_id)
                for slot_index, (conversation, discord_thread_id) in enumerate(
                    zip(conversations[:5], discord_thread_ids, strict=True),
                    start=1,
                )
            ]
    stored_thread_ids = tuple(row[2] for row in rows)
    if stored_thread_ids != discord_thread_ids:
        raise ChatGptMirrorStoreConfigError(
            "persisted ChatGPT mirror slots disagree with configured Discord threads"
        )
    return tuple(ChatGptMirrorSlot(*row) for row in rows)


def is_chatgpt_conversation_primed(db_path: Path, conversation_id: str) -> bool:
    with _connect(db_path) as conn:
        init_store_schema(conn)
        return conn.execute(
            "SELECT 1 FROM chatgpt_app_mirror_conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone() is not None


def prime_chatgpt_conversation(
    db_path: Path,
    conversation_id: str,
    turns: tuple[ChatGptTurn, ...],
    *,
    now: float | None = None,
) -> None:
    current = time.time() if now is None else now
    with _connect(db_path) as conn:
        init_store_schema(conn)
        for turn in turns:
            if turn.complete:
                _ = conn.execute(
                    "INSERT OR IGNORE INTO chatgpt_app_mirror_events "
                    + "(conversation_id, message_id, role, seen_at) VALUES (?, ?, ?, ?)",
                    (conversation_id, turn.message_id, turn.role.value, current),
                )
        _ = conn.execute(
            "INSERT OR IGNORE INTO chatgpt_app_mirror_conversations "
            + "(conversation_id, primed_at) VALUES (?, ?)",
            (conversation_id, current),
        )


def has_seen_chatgpt_turn(
    db_path: Path,
    conversation_id: str,
    message_id: str,
) -> bool:
    with _connect(db_path) as conn:
        init_store_schema(conn)
        return conn.execute(
            "SELECT 1 FROM chatgpt_app_mirror_events "
            + "WHERE conversation_id = ? AND message_id = ?",
            (conversation_id, message_id),
        ).fetchone() is not None


def claim_seen_chatgpt_turn(
    db_path: Path,
    conversation_id: str,
    turn: ChatGptTurn,
    *,
    now: float | None = None,
) -> bool:
    current = time.time() if now is None else now
    with _connect(db_path) as conn:
        init_store_schema(conn)
        result = conn.execute(
            "INSERT OR IGNORE INTO chatgpt_app_mirror_events "
            + "(conversation_id, message_id, role, seen_at) VALUES (?, ?, ?, ?)",
            (conversation_id, turn.message_id, turn.role.value, current),
        )
        return result.rowcount == 1
