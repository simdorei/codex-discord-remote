from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import TypeAlias, cast

import codex_discord_store_schema as store_schema


MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]


class GptMigrationV2Tests(unittest.TestCase):
    def _insert_rows(
        self,
        conn: sqlite3.Connection,
        rows: list[MirrorRow],
    ) -> None:
        _ = conn.executemany(
            "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        _ = conn.executemany(
            "INSERT INTO codex_session_mirror_offsets "
            + "(codex_thread_id, rollout_path, cursor, updated_at) "
            + "VALUES (?, ?, ?, ?)",
            [
                (row[0], f"source-{index}", index, row[5])
                for index, row in enumerate(rows)
            ],
        )

    def test_version_one_repair_is_once_only_and_preserves_state(self) -> None:
        # Given: every lifecycle shape that can exist in a deployed version-1 DB.
        rows: list[MirrorRow] = [
            (
                "late-active",
                "codex:chats",
                "Active",
                11,
                21,
                11.125,
                "ordinary",
                "active",
            ),
            (
                "late-deactivating",
                "codex:chats",
                "Deactivating",
                12,
                22,
                22.25,
                "ordinary",
                "deactivating",
            ),
            (
                "late-inactive",
                "codex:chats",
                "Inactive",
                13,
                23,
                33.375,
                "ordinary",
                "inactive",
            ),
            (
                "late-reactivating",
                "codex:chats",
                "Reactivating",
                14,
                24,
                44.5,
                "ordinary",
                "reactivating",
            ),
            (
                "existing-inactive",
                "codex:chats",
                "Existing",
                15,
                25,
                55.625,
                "gpt_chat",
                "inactive",
            ),
            (
                "ordinary-inactive",
                "ordinary",
                "Ordinary",
                16,
                26,
                66.75,
                "ordinary",
                "inactive",
            ),
        ]
        with sqlite3.connect(":memory:") as conn:
            store_schema.init_store_schema(conn)
            schema_before = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            _ = conn.execute("PRAGMA user_version = 1")
            self._insert_rows(conn, rows)
            conn.commit()
            offsets_before = conn.execute(
                "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
            ).fetchall()

            # When: v1 is repaired and initialization repeats at v2.
            store_schema.init_store_schema(conn)
            rows_after_first = cast(
                list[MirrorRow],
                conn.execute(
                    "SELECT * FROM mirror_threads ORDER BY updated_at"
                ).fetchall(),
            )
            offsets_after_first = conn.execute(
                "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
            ).fetchall()
            store_schema.init_store_schema(conn)
            rows_after_second = cast(
                list[MirrorRow],
                conn.execute(
                    "SELECT * FROM mirror_threads ORDER BY updated_at"
                ).fetchall(),
            )
            schema_after = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            version = cast(tuple[int], conn.execute("PRAGMA user_version").fetchone())[
                0
            ]

            # When: a later fail-closed ownership change is deliberate.
            _ = conn.execute(
                "UPDATE mirror_threads SET managed_by = 'ordinary' "
                + "WHERE codex_thread_id = 'late-active'"
            )
            conn.commit()
            store_schema.init_store_schema(conn)
            deliberate_owner = cast(
                tuple[str],
                conn.execute(
                    "SELECT managed_by FROM mirror_threads "
                    + "WHERE codex_thread_id = 'late-active'"
                ).fetchone(),
            )[0]

        expected = [
            (*row[:6], "gpt_chat", row[7]) if row[1] == "codex:chats" else row
            for row in rows
        ]
        self.assertEqual(rows_after_first, expected)
        self.assertEqual(rows_after_second, expected)
        self.assertEqual(offsets_after_first, offsets_before)
        self.assertEqual(schema_after, schema_before)
        self.assertEqual(version, 2)
        self.assertEqual(deliberate_owner, "ordinary")

    def test_version_one_repair_failure_is_atomic(self) -> None:
        # Given: the second ownership update in a deployed v1 DB will fail.
        rows: list[MirrorRow] = [
            (
                "late-before",
                "codex:chats",
                "Before",
                21,
                31,
                1.25,
                "ordinary",
                "active",
            ),
            ("late-fail", "codex:chats", "Fail", 22, 32, 2.5, "ordinary", "inactive"),
            ("ordinary", "ordinary", "Ordinary", 23, 33, 3.75, "ordinary", "active"),
        ]
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-v1-repair-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            db_path = Path(temp_dir) / "version-one.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                store_schema.init_store_schema(conn)
                _ = conn.execute("PRAGMA user_version = 1")
                self._insert_rows(conn, rows)
                _ = conn.execute(
                    "CREATE TRIGGER inject_gpt_repair_failure "
                    + "BEFORE UPDATE OF managed_by ON mirror_threads "
                    + "WHEN NEW.codex_thread_id = 'late-fail' "
                    + "BEGIN SELECT RAISE(ABORT, "
                    + "'injected GPT ownership repair failure'); END"
                )
                conn.commit()
                rows_before = conn.execute(
                    "SELECT * FROM mirror_threads ORDER BY updated_at"
                ).fetchall()
                offsets_before = conn.execute(
                    "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
                ).fetchall()

            # When: the one-time repair aborts inside its transaction.
            with closing(sqlite3.connect(db_path)) as conn:
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "injected GPT ownership repair failure",
                ):
                    store_schema.init_store_schema(conn)

            # Then: neither partial rows nor a version advance are durable.
            with closing(sqlite3.connect(db_path)) as conn:
                rows_after = conn.execute(
                    "SELECT * FROM mirror_threads ORDER BY updated_at"
                ).fetchall()
                offsets_after = conn.execute(
                    "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
                ).fetchall()
                version = cast(
                    tuple[int], conn.execute("PRAGMA user_version").fetchone()
                )[0]
                integrity = cast(
                    tuple[str], conn.execute("PRAGMA integrity_check").fetchone()
                )[0]

        self.assertEqual(rows_after, rows_before)
        self.assertEqual(offsets_after, offsets_before)
        self.assertEqual(version, 1)
        self.assertEqual(integrity, "ok")


if __name__ == "__main__":
    _ = unittest.main()
