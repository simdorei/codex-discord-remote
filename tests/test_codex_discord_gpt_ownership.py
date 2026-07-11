from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import TypeAlias

import codex_discord_store as store


MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]


class GptOwnershipTests(unittest.TestCase):
    def _db_path(self, temp_dir: str) -> Path:
        db_path = Path(temp_dir) / "mirror.sqlite"
        store.init_mirror_db(db_path)
        return db_path

    def _insert_thread(
        self,
        conn: sqlite3.Connection,
        row: MirrorRow,
    ) -> None:
        _ = conn.execute(
            "INSERT INTO mirror_threads ("
            + "codex_thread_id, project_key, thread_title, discord_channel_id, "
            + "discord_thread_id, updated_at, managed_by, lifecycle_state"
            + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    def _load_rows(self, db_path: Path) -> list[MirrorRow]:
        with closing(sqlite3.connect(db_path)) as conn:
            rows: list[MirrorRow] = conn.execute(
                "SELECT codex_thread_id, project_key, thread_title, "
                + "discord_channel_id, discord_thread_id, updated_at, managed_by, "
                + "lifecycle_state FROM mirror_threads ORDER BY codex_thread_id"
            ).fetchall()
        return rows

    def test_active_and_all_state_queries_are_explicit(self) -> None:
        # Given: ordinary and GPT mappings span every lifecycle state.
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-03-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            db_path = self._db_path(temp_dir)
            rows: list[MirrorRow] = [
                ("ordinary", "project", "Ordinary", 100, 200, 1.0, "ordinary", "active"),
                ("gpt-active", "codex:chats", "Active", 101, 201, 2.0, "gpt_chat", "active"),
                (
                    "gpt-deactivating",
                    "codex:chats",
                    "Deactivating",
                    102,
                    202,
                    3.0,
                    "gpt_chat",
                    "deactivating",
                ),
                ("gpt-inactive", "codex:chats", "Inactive", 103, 203, 4.0, "gpt_chat", "inactive"),
                (
                    "gpt-reactivating",
                    "codex:chats",
                    "Reactivating",
                    104,
                    204,
                    5.0,
                    "gpt_chat",
                    "reactivating",
                ),
            ]
            with closing(sqlite3.connect(db_path)) as conn:
                for row in rows:
                    self._insert_thread(conn, row)
                conn.commit()

            # When: each public query is evaluated against exact identities.
            active = store.get_active_gpt_mirror_thread_by_discord_thread_id(db_path, 201)
            transitional = store.get_mirror_thread_owner_by_discord_thread_id(db_path, 202)
            inactive = store.get_mirror_thread_owner_by_codex_thread_id(db_path, "gpt-inactive")
            ordinary_rows = store.list_ordinary_mirror_threads(db_path)

            # Then: only active GPT is routable while every state remains visible.
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.codex_thread_id, "gpt-active")
            self.assertEqual(active.managed_by, store.MirrorThreadManagedBy.GPT_CHAT)
            self.assertEqual(active.lifecycle_state, store.MirrorThreadLifecycleState.ACTIVE)
            self.assertIsNotNone(transitional)
            assert transitional is not None
            self.assertEqual(
                transitional.lifecycle_state,
                store.MirrorThreadLifecycleState.DEACTIVATING,
            )
            self.assertIsNotNone(inactive)
            assert inactive is not None
            self.assertEqual(inactive.lifecycle_state, store.MirrorThreadLifecycleState.INACTIVE)
            self.assertEqual(tuple(row.codex_thread_id for row in ordinary_rows), ("ordinary",))
            self.assertIsNone(store.get_active_gpt_mirror_thread_by_discord_thread_id(db_path, 200))
            self.assertIsNone(store.get_active_gpt_mirror_thread_by_discord_thread_id(db_path, 202))
            self.assertIsNone(store.get_active_gpt_mirror_thread_by_discord_thread_id(db_path, 101))
            self.assertIsNone(store.get_mirror_thread_owner_by_discord_thread_id(db_path, 102))
            self.assertEqual(store.get_ordinary_mirrored_codex_thread_id(db_path, 200), "ordinary")
            self.assertEqual(store.get_ordinary_mirrored_codex_thread_id(db_path, 100), "ordinary")
            self.assertIsNone(store.get_ordinary_mirrored_codex_thread_id(db_path, 201))
            self.assertIsNone(store.get_ordinary_mirrored_codex_thread_id(db_path, 101))
            self.assertEqual(
                store.get_ordinary_mirror_thread_row_by_codex_thread_id(db_path, "ordinary"),
                (100, 200),
            )
            self.assertIsNone(
                store.get_ordinary_mirror_thread_row_by_codex_thread_id(db_path, "gpt-active")
            )

    def test_duplicate_or_ordinary_overwrite_fails_without_change(self) -> None:
        # Given: a GPT row and an ordinary row claim the same historical Discord ID.
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-03-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            db_path = self._db_path(temp_dir)
            with closing(sqlite3.connect(db_path)) as conn:
                self._insert_thread(
                    conn,
                    ("gpt-owner", "codex:chats", "GPT", 110, 210, 1.0, "gpt_chat", "inactive"),
                )
                self._insert_thread(
                    conn,
                    ("ordinary-owner", "project", "Ordinary", 111, 210, 2.0, "ordinary", "active"),
                )
                conn.commit()
            before = self._load_rows(db_path)

            # When: lookup and ordinary writes encounter ambiguous or GPT ownership.
            with self.assertRaises(store.DiscordOwnershipConflictError) as duplicate_context:
                _ = store.get_mirror_thread_owner_by_discord_thread_id(db_path, 210)
            with self.assertRaises(store.GptOwnershipOverwriteError):
                store.upsert_mirror_thread(
                    db_path,
                    "gpt-owner",
                    "project",
                    "Replacement",
                    120,
                    220,
                    now=3.0,
                )
            with self.assertRaises(store.GptOwnershipOverwriteError):
                store.upsert_mirror_thread(
                    db_path,
                    "new-ordinary",
                    "project",
                    "Takeover",
                    121,
                    210,
                    now=4.0,
                )
            with self.assertRaises(store.GptOwnershipOverwriteError):
                _ = store.update_mirror_thread_discord_thread_id(
                    db_path,
                    "ordinary-owner",
                    210,
                    now=5.0,
                )

            # Then: ambiguity is typed and no stored value changes.
            self.assertEqual(duplicate_context.exception.discord_thread_id, 210)
            self.assertEqual(duplicate_context.exception.owner_count, 2)
            self.assertEqual(self._load_rows(db_path), before)

    def test_ordinary_upsert_updates_in_place_without_erasing_new_fields(self) -> None:
        # Given: an ordinary row carries a non-default lifecycle value.
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-03-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            db_path = self._db_path(temp_dir)
            with closing(sqlite3.connect(db_path)) as conn:
                self._insert_thread(
                    conn,
                    ("ordinary", "old", "Old", 130, 230, 1.0, "ordinary", "inactive"),
                )
                rowid_rows: list[tuple[int]] = conn.execute(
                    "SELECT rowid FROM mirror_threads WHERE codex_thread_id = ?",
                    ("ordinary",),
                ).fetchall()
                rowid_before = rowid_rows[0]
                conn.commit()

            # When: the compatibility writer updates that ordinary mapping.
            store.upsert_mirror_thread(
                db_path,
                "ordinary",
                "new",
                "New",
                131,
                231,
                now=2.0,
            )

            # Then: SQLite updates the same row and leaves migrated fields intact.
            with closing(sqlite3.connect(db_path)) as conn:
                stored_rows: list[tuple[int, str, str, int, int, float, str, str]] = conn.execute(
                    "SELECT rowid, project_key, thread_title, discord_channel_id, "
                    + "discord_thread_id, updated_at, managed_by, lifecycle_state "
                    + "FROM mirror_threads WHERE codex_thread_id = ?",
                    ("ordinary",),
                ).fetchall()
                stored = stored_rows[0]
            self.assertEqual(stored, (rowid_before[0], "new", "New", 131, 231, 2.0, "ordinary", "inactive"))


if __name__ == "__main__":
    _ = unittest.main()
