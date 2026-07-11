from __future__ import annotations

import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import codex_discord_store as store
import codex_discord_store_session_mirror as mirror_store


class StoreSessionMirrorTests(unittest.TestCase):
    def _db_path(self, temp_dir: str) -> Path:
        db_path = Path(temp_dir) / "mirror.sqlite"
        store.init_mirror_db(db_path)
        return db_path

    def test_session_mirror_targets_order_and_filter_empty_rows(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = self._db_path(temp_dir)
            with sqlite3.connect(db_path) as conn:
                _ = conn.executemany(
                    "INSERT INTO mirror_threads ("
                    + "codex_thread_id, project_key, thread_title, "
                    + "discord_channel_id, discord_thread_id, updated_at"
                    + ") VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("thread-old", "project", "Old", 10, 100, 1.0),
                        ("", "project", "Missing Codex", 20, 200, 3.0),
                        ("thread-zero-discord", "project", "Zero Discord", 30, 0, 4.0),
                        ("thread-new", "project", "New", 40, 400, 5.0),
                        ("gpt-inactive", "codex:chats", "Inactive", 50, 500, 6.0),
                        ("gpt-active", "codex:chats", "Active GPT", 60, 600, 7.0),
                    ],
                )
                _ = conn.execute(
                    "UPDATE mirror_threads SET lifecycle_state = 'inactive' "
                    + "WHERE codex_thread_id = 'gpt-inactive'"
                )

            self.assertEqual(
                store.get_session_mirror_targets(db_path, limit=10),
                [
                    {
                        "codex_thread_id": "gpt-active",
                        "thread_title": "Active GPT",
                        "discord_channel_id": 60,
                        "discord_thread_id": 600,
                    },
                    {
                        "codex_thread_id": "thread-new",
                        "thread_title": "New",
                        "discord_channel_id": 40,
                        "discord_thread_id": 400,
                    },
                    {
                        "codex_thread_id": "thread-old",
                        "thread_title": "Old",
                        "discord_channel_id": 10,
                        "discord_thread_id": 100,
                    },
                ],
            )

    def test_ordinary_active_mapping_remains_a_delivery_target(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = self._db_path(temp_dir)
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "INSERT INTO mirror_threads ("
                    + "codex_thread_id, project_key, thread_title, "
                    + "discord_channel_id, discord_thread_id, updated_at, "
                    + "managed_by, lifecycle_state"
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "ordinary-active",
                        "project",
                        "Active",
                        10,
                        100,
                        1.0,
                        "ordinary",
                        "active",
                    ),
                )

            self.assertEqual(
                store.get_session_mirror_targets(db_path),
                [
                    {
                        "codex_thread_id": "ordinary-active",
                        "thread_title": "Active",
                        "discord_channel_id": 10,
                        "discord_thread_id": 100,
                    }
                ],
            )

    def test_delivery_identity_tracks_exact_owner_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = self._db_path(temp_dir)
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "INSERT INTO mirror_threads VALUES "
                    + "(?, 'codex:chats', 'GPT', 10, 100, 1.0, 'gpt_chat', 'active')",
                    ("gpt-active",),
                )

            original = mirror_store.get_session_mirror_delivery_identity(
                db_path,
                "gpt-active",
            )
            self.assertIsNotNone(original)

            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "UPDATE mirror_threads SET discord_thread_id = 101, updated_at = 2.0, "
                    + "managed_by = 'ordinary', lifecycle_state = 'inactive' "
                    + "WHERE codex_thread_id = 'gpt-active'"
                )

            changed = mirror_store.get_session_mirror_delivery_identity(
                db_path,
                "gpt-active",
            )
            self.assertNotEqual(changed, original)
            self.assertIsNone(
                mirror_store.get_session_mirror_delivery_identity(
                    db_path,
                    "different-codex-id",
                )
            )

    def test_session_mirror_cursor_init_update_and_rollout_reset(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = self._db_path(temp_dir)

            self.assertEqual(
                store.get_or_init_session_mirror_cursor(
                    db_path,
                    "thread-1",
                    "rollout-a.jsonl",
                    5,
                    now=10.0,
                ),
                5,
            )
            self.assertEqual(
                store.get_or_init_session_mirror_cursor(
                    db_path,
                    "thread-1",
                    "rollout-a.jsonl",
                    0,
                    now=11.0,
                ),
                5,
            )

            store.update_session_mirror_cursor(
                db_path,
                "thread-1",
                "rollout-a.jsonl",
                8,
                now=20.0,
            )
            self.assertEqual(
                store.get_session_mirror_offset(db_path, "thread-1"),
                ("rollout-a.jsonl", 8, 20.0),
            )
            self.assertEqual(
                store.get_or_init_session_mirror_cursor(
                    db_path,
                    "thread-1",
                    "rollout-b.jsonl",
                    2,
                    now=30.0,
                ),
                2,
            )
            self.assertEqual(
                store.get_session_mirror_offset(db_path, "thread-1"),
                ("rollout-b.jsonl", 2, 30.0),
            )

    def test_session_mirror_event_claim_lookup_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = self._db_path(temp_dir)

            self.assertTrue(
                store.claim_session_mirror_event(
                    db_path,
                    "digest-old",
                    "thread-1",
                    now=100.0,
                )
            )
            self.assertFalse(
                store.claim_session_mirror_event(
                    db_path,
                    "digest-old",
                    "thread-1",
                    now=101.0,
                )
            )
            self.assertTrue(
                store.claim_session_mirror_event(
                    db_path,
                    "digest-boundary",
                    "thread-1",
                    now=190.0,
                )
            )
            self.assertTrue(
                store.claim_session_mirror_event(
                    db_path,
                    "digest-new",
                    "thread-1",
                    now=200.0,
                )
            )

            self.assertTrue(
                store.has_session_mirror_event(db_path, "digest-old", "thread-1")
            )
            self.assertEqual(
                store.cleanup_session_mirror_events(
                    db_path,
                    retention_seconds=50.0,
                    now=240.0,
                ),
                1,
            )
            self.assertFalse(
                store.has_session_mirror_event(db_path, "digest-old", "thread-1")
            )
            self.assertTrue(
                store.has_session_mirror_event(db_path, "digest-boundary", "thread-1")
            )
            self.assertTrue(
                store.has_session_mirror_event(db_path, "digest-new", "thread-1")
            )

    def test_session_mirror_store_calls_close_every_connection(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = self._db_path(temp_dir)
            real_connect = sqlite3.connect
            opened: list[sqlite3.Connection] = []

            def tracked_connect(database: Path) -> sqlite3.Connection:
                connection = real_connect(database)
                opened.append(connection)
                return connection

            with mock.patch.object(sqlite3, "connect", side_effect=tracked_connect):
                _ = mirror_store.claim_session_mirror_event(db_path, "d", "t")
                _ = mirror_store.has_session_mirror_event(db_path, "d", "t")
                _ = mirror_store.get_or_init_session_mirror_cursor(db_path, "t", "r", 0)
                mirror_store.update_session_mirror_cursor(db_path, "t", "r", 1)
                _ = mirror_store.get_session_mirror_offset(db_path, "t")

            leaked = 0
            for connection in opened:
                try:
                    _ = connection.execute("SELECT 1")
                except sqlite3.ProgrammingError:
                    continue
                leaked += 1
                connection.close()
            self.assertEqual(leaked, 0)


if __name__ == "__main__":
    _ = unittest.main()
