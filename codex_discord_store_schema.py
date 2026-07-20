from __future__ import annotations

import sqlite3

STORE_SCHEMA_TABLES: tuple[str, ...] = (
    "mirror_projects",
    "mirror_threads",
    "busy_choices",
    "persistent_component_claims",
    "discord_processed_messages",
    "codex_session_mirror_offsets",
    "codex_session_mirror_events",
)

STORE_SCHEMA_STATEMENTS: tuple[str, ...] = (
    (
        "CREATE TABLE IF NOT EXISTS mirror_projects ("
        "project_key TEXT PRIMARY KEY, "
        "project_name TEXT NOT NULL, "
        "discord_channel_id INTEGER NOT NULL, "
        "updated_at REAL NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS mirror_threads ("
        "codex_thread_id TEXT PRIMARY KEY, "
        "project_key TEXT NOT NULL, "
        "thread_title TEXT NOT NULL, "
        "discord_channel_id INTEGER NOT NULL, "
        "discord_thread_id INTEGER NOT NULL, "
        "updated_at REAL NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS busy_choices ("
        "choice_id TEXT PRIMARY KEY, "
        "owner_user_id INTEGER NOT NULL, "
        "channel_id INTEGER NOT NULL, "
        "target_thread_id TEXT, "
        "prompt TEXT NOT NULL, "
        "allow_steer INTEGER NOT NULL, "
        "created_at REAL NOT NULL, "
        "expires_at REAL NOT NULL, "
        "claimed_at REAL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS persistent_component_claims ("
        "claim_key TEXT PRIMARY KEY, "
        "created_at REAL NOT NULL, "
        "expires_at REAL NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS discord_processed_messages ("
        "message_id INTEGER PRIMARY KEY, "
        "seen_at REAL NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS codex_session_mirror_offsets ("
        "codex_thread_id TEXT PRIMARY KEY, "
        "rollout_path TEXT NOT NULL, "
        "cursor INTEGER NOT NULL, "
        "updated_at REAL NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS codex_session_mirror_events ("
        "event_digest TEXT PRIMARY KEY, "
        "codex_thread_id TEXT NOT NULL, "
        "created_at REAL NOT NULL)"
    ),
)


def init_store_schema(conn: sqlite3.Connection) -> None:
    for statement in STORE_SCHEMA_STATEMENTS:
        _ = conn.execute(statement)
