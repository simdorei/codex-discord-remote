from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from codex_discord_store_schema import init_store_schema

_StaleMirrorThreadRow = tuple[str, int, str]
_StaleMirrorProjectRow = tuple[str, str, int]


def _init_mirror_db(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        init_store_schema(conn)


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def get_stale_mirror_thread_rows(
    db_path: Path,
    valid_thread_ids: set[str],
) -> list[_StaleMirrorThreadRow]:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        if valid_thread_ids:
            ordered_ids = tuple(
                sorted(str(thread_id) for thread_id in valid_thread_ids)
            )
            rows: list[_StaleMirrorThreadRow] = conn.execute(
                "SELECT codex_thread_id, discord_thread_id, thread_title "
                + "FROM mirror_threads WHERE managed_by = 'ordinary' "
                + "AND project_key <> 'codex:chats' AND discord_thread_id IN ("
                + "SELECT discord_thread_id FROM mirror_threads GROUP BY discord_thread_id "
                + "HAVING COUNT(*) = 1) AND codex_thread_id NOT IN ("
                + _placeholders(len(ordered_ids))
                + ")",
                ordered_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT codex_thread_id, discord_thread_id, thread_title "
                + "FROM mirror_threads WHERE managed_by = 'ordinary' "
                + "AND project_key <> 'codex:chats' AND discord_thread_id IN ("
                + "SELECT discord_thread_id FROM mirror_threads GROUP BY discord_thread_id "
                + "HAVING COUNT(*) = 1)"
            ).fetchall()
    return list(rows)


def get_stale_mirror_project_rows(
    db_path: Path,
    valid_project_keys: set[str],
) -> list[_StaleMirrorProjectRow]:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        if valid_project_keys:
            ordered_keys = tuple(
                sorted(str(project_key) for project_key in valid_project_keys)
            )
            rows: list[_StaleMirrorProjectRow] = conn.execute(
                "SELECT project_key, project_name, discord_channel_id "
                + "FROM mirror_projects WHERE project_key <> 'codex:chats' "
                + "AND project_key NOT IN ("
                + _placeholders(len(ordered_keys))
                + ")",
                ordered_keys,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT project_key, project_name, discord_channel_id "
                + "FROM mirror_projects WHERE project_key <> 'codex:chats'"
            ).fetchall()
    return list(rows)


def delete_stale_mirror_rows(
    db_path: Path,
    valid_thread_ids: set[str],
    valid_project_keys: set[str],
) -> None:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        if valid_thread_ids:
            ordered_ids = tuple(
                sorted(str(thread_id) for thread_id in valid_thread_ids)
            )
            _ = conn.execute(
                "DELETE FROM mirror_threads WHERE managed_by = 'ordinary' "
                + "AND project_key <> 'codex:chats' AND discord_thread_id IN ("
                + "SELECT discord_thread_id FROM mirror_threads GROUP BY discord_thread_id "
                + "HAVING COUNT(*) = 1) AND codex_thread_id NOT IN ("
                + _placeholders(len(ordered_ids))
                + ")",
                ordered_ids,
            )
        else:
            _ = conn.execute(
                "DELETE FROM mirror_threads WHERE managed_by = 'ordinary' "
                + "AND project_key <> 'codex:chats' AND discord_thread_id IN ("
                + "SELECT discord_thread_id FROM mirror_threads GROUP BY discord_thread_id "
                + "HAVING COUNT(*) = 1)"
            )
        if valid_project_keys:
            ordered_keys = tuple(
                sorted(str(project_key) for project_key in valid_project_keys)
            )
            _ = conn.execute(
                "DELETE FROM mirror_projects WHERE project_key <> 'codex:chats' "
                + "AND project_key NOT IN ("
                + _placeholders(len(ordered_keys))
                + ")",
                ordered_keys,
            )
        else:
            _ = conn.execute(
                "DELETE FROM mirror_projects WHERE project_key <> 'codex:chats'"
            )


def delete_archived_mirror_state(db_path: Path, codex_thread_id: str) -> dict[str, int]:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        owner_rows: list[tuple[str, str, int]] = conn.execute(
            "SELECT managed_by, project_key, discord_thread_id "
            + "FROM mirror_threads WHERE codex_thread_id = ?",
            (str(codex_thread_id),),
        ).fetchall()
        if owner_rows:
            managed_by, project_key, discord_thread_id = owner_rows[0]
            if managed_by != "ordinary" or project_key == "codex:chats":
                return {
                    "mirror_threads": 0,
                    "session_mirror_offsets": 0,
                    "destructive_cleanup_allowed": 0,
                }
            duplicate_count_rows: list[tuple[int]] = conn.execute(
                "SELECT COUNT(*) FROM mirror_threads WHERE discord_thread_id = ?",
                (discord_thread_id,),
            ).fetchall()
            if duplicate_count_rows[0][0] != 1:
                return {
                    "mirror_threads": 0,
                    "session_mirror_offsets": 0,
                    "destructive_cleanup_allowed": 0,
                }
        mirror_threads = conn.execute(
            "DELETE FROM mirror_threads WHERE codex_thread_id = ?",
            (str(codex_thread_id),),
        ).rowcount
        session_mirror_offsets = conn.execute(
            "DELETE FROM codex_session_mirror_offsets WHERE codex_thread_id = ?",
            (str(codex_thread_id),),
        ).rowcount
    return {
        "mirror_threads": int(mirror_threads or 0),
        "session_mirror_offsets": int(session_mirror_offsets or 0),
        "destructive_cleanup_allowed": 1,
    }


def get_remaining_mirror_discord_ids(db_path: Path) -> tuple[set[int], list[int]]:
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        thread_rows: list[tuple[int | None]] = conn.execute(
            "SELECT discord_thread_id FROM mirror_threads"
        ).fetchall()
        project_rows: list[tuple[int | None]] = conn.execute(
            "SELECT discord_channel_id FROM mirror_projects "
            + "UNION SELECT discord_channel_id FROM mirror_threads"
        ).fetchall()
    known_thread_ids = {int(row[0]) for row in thread_rows if row[0]}
    project_channel_ids = [int(row[0]) for row in project_rows if row[0]]
    return known_thread_ids, project_channel_ids


def is_mirrored_channel_id(db_path: Path, discord_channel_id: int | None) -> bool:
    if discord_channel_id is None:
        return False
    channel_id = int(discord_channel_id)
    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows: list[tuple[int]] = conn.execute(
            "SELECT 1 FROM mirror_threads "
            + "WHERE discord_thread_id = ? OR discord_channel_id = ? "
            + "UNION ALL "
            + "SELECT 1 FROM mirror_projects WHERE discord_channel_id = ? "
            + "LIMIT 1",
            (channel_id, channel_id, channel_id),
        ).fetchall()
    return bool(rows)
