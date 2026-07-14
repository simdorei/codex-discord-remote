"""Read-only access to Codex thread IDs reserved by the GPT mirror feature."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def load_gpt_registered_thread_ids_read_only(db_path: Path) -> frozenset[str]:
    if not db_path.is_file():
        return frozenset()

    database_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(mirror_threads)").fetchall()
        }
        if not columns:
            return frozenset()
        if "managed_by" not in columns:
            return frozenset()
        rows: list[tuple[str]] = conn.execute(
            "SELECT codex_thread_id FROM mirror_threads "
            "WHERE managed_by = 'gpt_chat'"
        ).fetchall()
    return frozenset(str(row[0]) for row in rows)
