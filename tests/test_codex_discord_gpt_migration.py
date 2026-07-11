from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import TypeAlias, cast

import codex_discord_store_schema as store_schema


LegacyMirrorRow: TypeAlias = tuple[str, str, str, int, int, float]
MigratedMirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]


class GptMigrationTests(unittest.TestCase):
    def _create_legacy_schema(self, conn: sqlite3.Connection) -> None:
        for statement in store_schema.STORE_SCHEMA_STATEMENTS:
            _ = conn.execute(statement)

    def _insert_legacy_rows(
        self,
        conn: sqlite3.Connection,
        rows: list[LegacyMirrorRow],
    ) -> None:
        _ = conn.executemany(
            "INSERT INTO mirror_threads ("
            + "codex_thread_id, project_key, thread_title, discord_channel_id, "
            + "discord_thread_id, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        _ = conn.executemany(
            "INSERT INTO codex_session_mirror_offsets ("
            + "codex_thread_id, rollout_path, cursor, updated_at) VALUES (?, ?, ?, ?)",
            [(row[0], f"source-{index}", index, row[5]) for index, row in enumerate(rows)],
        )

    def test_direct_project_key_backfill_preserves_legacy_rows(self) -> None:
        # Given: legacy rows include duplicate Discord IDs and unavailable/archived sources.
        rows: list[LegacyMirrorRow] = [
            ("active-source", "codex:chats", "Active", 101, 201, 1.25),
            ("missing-source", "codex:chats", "Missing", 102, 202, 2.5),
            ("archived-source", "codex:chats", "Archived", 103, 202, 3.75),
            ("ordinary-source", "ordinary", "Ordinary", 104, 204, 4.0),
        ]
        with sqlite3.connect(":memory:") as conn:
            self._create_legacy_schema(conn)
            self._insert_legacy_rows(conn, rows)
            before_offsets = conn.execute(
                "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
            ).fetchall()
            conn.commit()

            # When: the store initializes the GPT ownership migration.
            store_schema.init_store_schema(conn)

            # Then: only the exact project key owns rows and all legacy values survive.
            migrated = cast(
                list[MigratedMirrorRow],
                conn.execute(
                    "SELECT codex_thread_id, project_key, thread_title, "
                    + "discord_channel_id, discord_thread_id, updated_at, managed_by, "
                    + "lifecycle_state FROM mirror_threads ORDER BY updated_at"
                ).fetchall(),
            )
            after_offsets = conn.execute(
                "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
            ).fetchall()

        self.assertEqual([row[:6] for row in migrated], rows)
        self.assertEqual(
            [(row[0], row[6], row[7]) for row in migrated],
            [
                ("active-source", "gpt_chat", "active"),
                ("missing-source", "gpt_chat", "active"),
                ("archived-source", "gpt_chat", "active"),
                ("ordinary-source", "ordinary", "active"),
            ],
        )
        self.assertEqual(after_offsets, before_offsets)

    def test_join_mismatch_overfive_and_injected_failure_preserve_everything(self) -> None:
        # Given: seven direct GPT rows, a project-join trap, and a disk-backed legacy DB.
        gpt_rows: list[LegacyMirrorRow] = [
            (f"gpt-{index}", "codex:chats", f"GPT {index}", 300 + index, 400 + index, float(index))
            for index in range(7)
        ]
        join_trap: LegacyMirrorRow = (
            "ordinary-join-trap",
            "ordinary",
            "Ordinary",
            999,
            499,
            9.0,
        )
        with sqlite3.connect(":memory:") as conn:
            self._create_legacy_schema(conn)
            _ = conn.execute(
                "INSERT INTO mirror_projects VALUES (?, ?, ?, ?)",
                ("codex:chats", "Join trap", 999, 1.0),
            )
            self._insert_legacy_rows(conn, [*gpt_rows, join_trap])
            conn.commit()

            # When: migration classifies rows whose parent/project join disagrees.
            store_schema.init_store_schema(conn)

            # Then: all seven direct rows remain active and the join trap stays ordinary.
            ownership = cast(
                list[tuple[str, str, str]],
                conn.execute(
                    "SELECT codex_thread_id, managed_by, lifecycle_state "
                    + "FROM mirror_threads ORDER BY updated_at"
                ).fetchall(),
            )
        self.assertEqual(sum(row[1] == "gpt_chat" for row in ownership), 7)
        self.assertEqual(ownership[-1], ("ordinary-join-trap", "ordinary", "active"))

        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-02-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            db_path = Path(temp_dir) / "legacy.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                self._create_legacy_schema(conn)
                self._insert_legacy_rows(conn, gpt_rows)
                _ = conn.execute(
                    "CREATE TRIGGER inject_migration_failure BEFORE UPDATE ON mirror_threads "
                    + "BEGIN SELECT RAISE(ABORT, 'injected migration failure'); END"
                )
                conn.commit()
                columns_before = conn.execute("PRAGMA table_info(mirror_threads)").fetchall()
                rows_before = conn.execute("SELECT * FROM mirror_threads ORDER BY updated_at").fetchall()
                offsets_before = conn.execute(
                    "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
                ).fetchall()

            # When: SQLite aborts after transactional schema changes have begun.
            with closing(sqlite3.connect(db_path)) as conn:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected migration failure"):
                    store_schema.init_store_schema(conn)

            # Then: reopening the DB proves no migration artifact became durable.
            with closing(sqlite3.connect(db_path)) as conn:
                columns_after = conn.execute("PRAGMA table_info(mirror_threads)").fetchall()
                rows_after = conn.execute("SELECT * FROM mirror_threads ORDER BY updated_at").fetchall()
                offsets_after = conn.execute(
                    "SELECT * FROM codex_session_mirror_offsets ORDER BY codex_thread_id"
                ).fetchall()
                journal_count = cast(
                    tuple[int],
                    conn.execute(
                        "SELECT COUNT(*) FROM sqlite_master "
                        + "WHERE type = 'table' AND name = 'gpt_chat_creation_ops'"
                    ).fetchone(),
                )[0]
                version = cast(tuple[int], conn.execute("PRAGMA user_version").fetchone())[0]

        self.assertEqual(columns_after, columns_before)
        self.assertEqual(rows_after, rows_before)
        self.assertEqual(offsets_after, offsets_before)
        self.assertEqual(journal_count, 0)
        self.assertEqual(version, 0)

    def test_fresh_and_repeated_initialization_are_idempotent(self) -> None:
        # Given: an empty SQLite database.
        with sqlite3.connect(":memory:") as conn:
            # When: initialization runs twice and a legacy-shaped insert uses defaults.
            store_schema.init_store_schema(conn)
            schema_before = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            store_schema.init_store_schema(conn)
            schema_after = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            _ = conn.execute(
                "INSERT INTO mirror_threads (codex_thread_id, project_key, thread_title, "
                + "discord_channel_id, discord_thread_id, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("fresh", "ordinary", "Fresh", 1, 2, 3.0),
            )
            defaults = cast(
                tuple[str, str],
                conn.execute(
                    "SELECT managed_by, lifecycle_state FROM mirror_threads WHERE codex_thread_id = ?",
                    ("fresh",),
                ).fetchone(),
            )
            version = cast(tuple[int], conn.execute("PRAGMA user_version").fetchone())[0]

        # Then: the schema is stable and fresh compatibility defaults are exact.
        self.assertEqual(schema_after, schema_before)
        self.assertEqual(defaults, ("ordinary", "active"))
        self.assertEqual(version, 1)

    def test_exact_journal_and_lifecycle_constraints(self) -> None:
        # Given: a freshly initialized schema and one valid nullable-ID operation.
        with sqlite3.connect(":memory:") as conn:
            store_schema.init_store_schema(conn)
            _ = conn.execute(
                "INSERT INTO gpt_chat_creation_ops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("owner", "codex:chats", "Original", 10, "a" * 32, "prepared", None, 1.0, 1.0),
            )
            conn.commit()

            # When/Then: exact allowed values work and malformed values are rejected.
            invalid_ops = [
                ("bad-project", "other", "b" * 32, "prepared"),
                ("bad-length", "codex:chats", "c" * 31, "prepared"),
                ("bad-uppercase", "codex:chats", "D" * 32, "prepared"),
                ("bad-hex", "codex:chats", "z" * 32, "prepared"),
                ("bad-status", "codex:chats", "e" * 32, "done"),
                ("bad-nonce-owner", "codex:chats", "a" * 32, "create_started"),
            ]
            for owner, project_key, nonce, status in invalid_ops:
                with self.subTest(owner=owner):
                    with self.assertRaises(sqlite3.IntegrityError):
                        _ = conn.execute(
                            "INSERT INTO gpt_chat_creation_ops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (owner, project_key, "Title", 11, nonce, status, None, 2.0, 2.0),
                        )
                    conn.rollback()

            for managed_by, lifecycle_state in (("invalid", "active"), ("ordinary", "invalid")):
                with self.subTest(managed_by=managed_by, lifecycle_state=lifecycle_state):
                    with self.assertRaises(sqlite3.IntegrityError):
                        _ = conn.execute(
                            "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            ("invalid", "ordinary", "Invalid", 1, 2, 3.0, managed_by, lifecycle_state),
                        )
                    conn.rollback()


if __name__ == "__main__":
    _ = unittest.main()
