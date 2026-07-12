from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, override
from unittest.mock import patch

import codex_discord_store as store
import codex_discord_store_schema as store_schema


class CloseTrackingConnection(sqlite3.Connection):
    close_calls: ClassVar[int] = 0

    @override
    def close(self) -> None:
        type(self).close_calls += 1
        super().close()


class StoreSchemaTests(unittest.TestCase):
    def assert_exact_names(
        self,
        conn: sqlite3.Connection,
        source_sql: str,
        expected: tuple[str, ...],
    ) -> None:
        placeholders = ", ".join("?" for _ in expected)
        _ = conn.execute("CREATE TEMP TABLE exact_name_assertion (valid INTEGER)")
        statement = " ".join(
            (
                "INSERT INTO exact_name_assertion",
                "SELECT 1 FROM (",
                f"SELECT COUNT(*) AS total, SUM(name IN ({placeholders})) AS matched",
                f"FROM ({source_sql}))",
                "WHERE total = ? AND matched = ?",
            )
        )
        inserted = conn.execute(
            statement,
            (*expected, len(expected), len(expected)),
        ).rowcount
        _ = conn.execute("DROP TABLE exact_name_assertion")
        self.assertEqual(inserted, 1)

    def test_public_init_mirror_db_closes_connection_before_return(self) -> None:
        # Given: a real SQLite connection whose explicit close calls are observable.
        real_connect = sqlite3.connect
        CloseTrackingConnection.close_calls = 0

        def connect_tracked(database: Path) -> sqlite3.Connection:
            return real_connect(database, factory=CloseTrackingConnection)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"

            # When: the public initializer returns.
            with patch("sqlite3.connect", side_effect=connect_tracked):
                store.init_mirror_db(db_path)

            # Then: it has explicitly released its SQLite handle.
            self.assertEqual(CloseTrackingConnection.close_calls, 1)

    def test_public_init_mirror_db_allows_immediate_delete_and_reopen(self) -> None:
        # Given: a newly initialized on-disk database.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.init_mirror_db(db_path)

            # When: the same process deletes and recreates the path immediately.
            db_path.unlink()
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute("CREATE TABLE reopened (value INTEGER NOT NULL)")

            # Then: the recreated database exists without requiring a retry.
            self.assertTrue(db_path.is_file())

    def test_store_schema_helper_creates_expected_tables(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            store_schema.init_store_schema(conn)
            self.assert_exact_names(
                conn,
                "SELECT name FROM sqlite_master WHERE type = 'table'",
                store_schema.STORE_SCHEMA_TABLES,
            )
            self.assertIn("gpt_chat_creation_ops", store_schema.STORE_SCHEMA_TABLES)

    def test_public_init_mirror_db_preserves_representative_columns(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.init_mirror_db(db_path)
            with sqlite3.connect(db_path) as conn:
                self.assert_exact_names(
                    conn,
                    "SELECT name FROM pragma_table_info('mirror_projects')",
                    (
                        "project_key",
                        "project_name",
                        "discord_channel_id",
                        "updated_at",
                    ),
                )
                self.assert_exact_names(
                    conn,
                    "SELECT name FROM pragma_table_info('busy_choices')",
                    (
                        "choice_id",
                        "owner_user_id",
                        "channel_id",
                        "target_thread_id",
                        "prompt",
                        "allow_steer",
                        "created_at",
                        "expires_at",
                        "claimed_at",
                    ),
                )
                self.assert_exact_names(
                    conn,
                    "SELECT name FROM pragma_table_info('codex_session_mirror_events')",
                    ("event_digest", "codex_thread_id", "created_at"),
                )
                self.assert_exact_names(
                    conn,
                    "SELECT name FROM pragma_table_info('mirror_threads')",
                    (
                        "codex_thread_id",
                        "project_key",
                        "thread_title",
                        "discord_channel_id",
                        "discord_thread_id",
                        "updated_at",
                        "managed_by",
                        "lifecycle_state",
                    ),
                )
                self.assert_exact_names(
                    conn,
                    "SELECT name FROM pragma_table_info('gpt_chat_creation_ops')",
                    (
                        "codex_thread_id",
                        "project_key",
                        "thread_title",
                        "discord_parent_channel_id",
                        "nonce",
                        "status",
                        "discord_thread_id",
                        "created_at",
                        "updated_at",
                    ),
                )


if __name__ == "__main__":
    _ = unittest.main()
