"""Atomic SQLite migration for GPT-managed Discord chat ownership."""

from __future__ import annotations

import sqlite3
from typing import Final, TypeAlias, cast


GPT_SCHEMA_VERSION: Final = 1
GPT_PROJECT_KEY: Final = "codex:chats"

ColumnInfoRow: TypeAlias = tuple[int, str, str, int, str | None, int]
VersionRow: TypeAlias = tuple[int]


def migrate_gpt_schema(conn: sqlite3.Connection) -> None:
    """Apply the GPT ownership schema once as one immediate transaction."""
    with conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        version_row = cast(VersionRow, conn.execute("PRAGMA user_version").fetchone())
        if version_row[0] >= GPT_SCHEMA_VERSION:
            return

        column_rows = cast(
            list[ColumnInfoRow],
            conn.execute("PRAGMA table_info(mirror_threads)").fetchall(),
        )
        column_names = {row[1] for row in column_rows}
        if "managed_by" not in column_names:
            _ = conn.execute(
                "ALTER TABLE mirror_threads ADD COLUMN managed_by TEXT NOT NULL "
                + "DEFAULT 'ordinary' CHECK (managed_by IN ('ordinary', 'gpt_chat'))"
            )
        if "lifecycle_state" not in column_names:
            _ = conn.execute(
                "ALTER TABLE mirror_threads ADD COLUMN lifecycle_state TEXT NOT NULL "
                + "DEFAULT 'active' CHECK (lifecycle_state IN "
                + "('active', 'deactivating', 'inactive', 'reactivating'))"
            )

        _ = conn.execute(
            "CREATE TABLE gpt_chat_creation_ops ("
            + "codex_thread_id TEXT PRIMARY KEY NOT NULL, "
            + "project_key TEXT NOT NULL CHECK (project_key = 'codex:chats'), "
            + "thread_title TEXT NOT NULL, "
            + "discord_parent_channel_id INTEGER NOT NULL, "
            + "nonce TEXT NOT NULL UNIQUE CHECK ("
            + "length(nonce) = 32 AND nonce NOT GLOB '*[^0-9a-f]*'), "
            + "status TEXT NOT NULL CHECK (status IN "
            + "('prepared', 'create_started', 'discord_identified')), "
            + "discord_thread_id INTEGER, "
            + "created_at REAL NOT NULL, "
            + "updated_at REAL NOT NULL)"
        )
        _ = conn.execute(
            "UPDATE mirror_threads SET managed_by = 'gpt_chat' WHERE project_key = ?",
            (GPT_PROJECT_KEY,),
        )
        _ = conn.execute(f"PRAGMA user_version = {GPT_SCHEMA_VERSION}")
