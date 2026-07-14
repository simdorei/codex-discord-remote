"""Minimal read-only access to GPT-owned Codex thread registrations."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def load_gpt_registered_thread_ids_read_only(db_path: Path) -> frozenset[str]:
    database_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as conn:
        rows: list[tuple[str]] = conn.execute(
            "SELECT codex_thread_id FROM mirror_threads "
            "WHERE managed_by = 'gpt_chat'"
        ).fetchall()
    return frozenset(str(row[0]) for row in rows)
