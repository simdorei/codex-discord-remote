from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from typing import final, override

import codex_discord_mirror_orphans as mirror_orphans
import codex_discord_store as store
from codex_discord_store_schema import init_store_schema


@final
class _Thread:
    def __init__(self, thread_id: int, name: str, lock: asyncio.Lock) -> None:
        self.id = thread_id
        self.name = name
        self.owner_id: int | None = 7
        self.deleted = False
        self._lock = lock

    async def delete(self, *, reason: str) -> None:
        _ = reason
        if not self._lock.locked():
            raise AssertionError("orphan deletion escaped the configured-channel lock")
        self.deleted = True


@final
class _Channel:
    def __init__(
        self,
        active: list[_Thread],
        archived_pages: list[list[_Thread]],
        lock: asyncio.Lock,
    ) -> None:
        self.name = "configured"
        self.threads = active
        self._archived_pages = archived_pages
        self._lock = lock
        self.requested_limits: list[int | None] = []

    async def _archived(self) -> AsyncIterator[_Thread]:
        for page in self._archived_pages:
            if not self._lock.locked():
                raise AssertionError(
                    "archived scan escaped the configured-channel lock"
                )
            for thread in page:
                yield thread
            await asyncio.sleep(0)

    def archived_threads(self, *, limit: int | None) -> AsyncIterator[_Thread]:
        self.requested_limits.append(limit)
        return self._archived()


@final
class GptCleanupIsolationTests(unittest.IsolatedAsyncioTestCase):
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None
    db_path = Path()

    @override
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self._temp_dir = temp_dir
        self.addCleanup(temp_dir.cleanup)
        self.db_path = Path(temp_dir.name) / "mirror.sqlite"
        with closing(sqlite3.connect(self.db_path)) as conn:
            init_store_schema(conn)

    def _insert_project(self, key: str, channel_id: int) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            _ = conn.execute(
                "INSERT INTO mirror_projects VALUES (?, ?, ?, 1.0)",
                (key, key, channel_id),
            )

    def _insert_mapping(
        self,
        codex_id: str,
        discord_id: int,
        *,
        state: str,
        managed_by: str = "gpt_chat",
        project_key: str = "codex:chats",
        parent_id: int = 900,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            _ = conn.execute(
                "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, 1.0, ?, ?)",
                (
                    codex_id,
                    project_key,
                    codex_id,
                    parent_id,
                    discord_id,
                    managed_by,
                    state,
                ),
            )
            _ = conn.execute(
                "INSERT INTO codex_session_mirror_offsets VALUES (?, ?, ?, 1.0)",
                (codex_id, f"{codex_id}.jsonl", 12),
            )

    def _insert_journal(
        self,
        codex_id: str,
        nonce: str,
        status: str,
        discord_id: int | None,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            _ = conn.execute(
                "INSERT INTO gpt_chat_creation_ops VALUES "
                + "(?, 'codex:chats', ?, 900, ?, ?, ?, 1.0, 1.0)",
                (codex_id, codex_id, nonce, status, discord_id),
            )

    def _mapping_rows(self) -> list[tuple[str, str, str]]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows: list[tuple[str, str, str]] = conn.execute(
                "SELECT codex_thread_id, managed_by, lifecycle_state "
                + "FROM mirror_threads ORDER BY codex_thread_id"
            ).fetchall()
        return rows

    async def test_ordinary_cleanup_remains_destructive_under_shared_lock(self) -> None:
        self._insert_project("ordinary-project", 100)
        self._insert_project("codex:chats", 900)
        self._insert_mapping(
            "ordinary",
            101,
            state="active",
            managed_by="ordinary",
            project_key="ordinary-project",
            parent_id=100,
        )
        for index, state in enumerate(
            ("active", "deactivating", "inactive", "reactivating")
        ):
            self._insert_mapping(f"gpt-{state}", 200 + index, state=state)

        store.delete_stale_mirror_rows(self.db_path, set(), set())
        self.assertEqual(
            self._mapping_rows(),
            [
                ("gpt-active", "gpt_chat", "active"),
                ("gpt-deactivating", "gpt_chat", "deactivating"),
                ("gpt-inactive", "gpt_chat", "inactive"),
                ("gpt-reactivating", "gpt_chat", "reactivating"),
            ],
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            projects = conn.execute(
                "SELECT project_key FROM mirror_projects"
            ).fetchall()
            cursor_count_rows: list[tuple[int]] = conn.execute(
                "SELECT COUNT(*) FROM codex_session_mirror_offsets"
            ).fetchall()
        self.assertEqual(projects, [("codex:chats",)])
        self.assertEqual(cursor_count_rows, [(5,)])
        self.assertEqual(
            store.get_remaining_mirror_discord_ids(self.db_path)[1],
            [900],
        )

        lock = asyncio.Lock()
        orphan = _Thread(777, "ordinary orphan", lock)
        channel = _Channel([orphan], [], lock)
        result = await mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
            [channel],
            store.get_remaining_mirror_discord_ids(self.db_path)[0],
            7,
            db_path=self.db_path,
            configured_channel_lock=lock,
            delivery_exceptions=(RuntimeError,),
        )
        self.assertTrue(orphan.deleted)
        self.assertEqual(result["deleted"], 1)

    async def test_null_id_exact_marker_and_nonnull_id_survive_active_archived_cleanup(
        self,
    ) -> None:
        prepared_nonce = "a" * 32
        started_nonce = "b" * 32
        identified_nonce = "c" * 32
        lock = asyncio.Lock()
        exact_prepared = _Thread(301, f"[gpt-sync:{prepared_nonce}] prepared", lock)
        exact_started = _Thread(302, f"[gpt-sync:{started_nonce}]", lock)
        identified = _Thread(303, "renamed GPT thread", lock)
        loose = _Thread(304, f"prefix [gpt-sync:{started_nonce}]", lock)
        malformed = _Thread(305, f"[gpt-sync:{started_nonce.upper()}]", lock)
        mapping_and_journal = _Thread(306, "mapping and journal", lock)
        renamed_before_journal_delete = _Thread(307, "normal renamed title", lock)
        journal_removed = _Thread(308, "journal already removed", lock)
        channel = _Channel(
            [exact_started, loose, journal_removed],
            [
                [identified, mapping_and_journal],
                [exact_prepared, malformed, renamed_before_journal_delete],
            ],
            lock,
        )

        _ = await lock.acquire()
        cleanup_task = asyncio.create_task(
            mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
                [channel],
                set(),
                7,
                db_path=self.db_path,
                configured_channel_lock=lock,
                delivery_exceptions=(RuntimeError,),
            )
        )
        await asyncio.sleep(0)
        self._insert_journal("prepared", prepared_nonce, "prepared", None)
        self._insert_journal("started", started_nonce, "create_started", None)
        self._insert_journal("identified", identified_nonce, "discord_identified", 303)
        self._insert_mapping("mapping-journal", 306, state="reactivating")
        self._insert_journal("mapping-journal", "d" * 32, "discord_identified", 306)
        self._insert_mapping("renamed", 307, state="active")
        self._insert_journal("renamed", "e" * 32, "discord_identified", 307)
        self._insert_mapping("journal-removed", 308, state="active")
        lock.release()
        result = await cleanup_task

        self.assertFalse(exact_prepared.deleted)
        self.assertFalse(exact_started.deleted)
        self.assertFalse(identified.deleted)
        self.assertFalse(mapping_and_journal.deleted)
        self.assertFalse(renamed_before_journal_delete.deleted)
        self.assertFalse(journal_removed.deleted)
        self.assertTrue(loose.deleted)
        self.assertTrue(malformed.deleted)
        self.assertEqual(channel.requested_limits, [None])
        self.assertEqual(result["deleted"], 2)

    async def test_every_gpt_lifecycle_mapping_and_cursor_survives_generic_archive_cleanup(
        self,
    ) -> None:
        states = ("active", "deactivating", "inactive", "reactivating")
        for index, state in enumerate(states):
            self._insert_mapping(f"gpt-{state}", 400 + index, state=state)

        for state in states:
            self.assertEqual(
                store.delete_archived_mirror_state(self.db_path, f"gpt-{state}"),
                {
                    "mirror_threads": 0,
                    "session_mirror_offsets": 0,
                    "destructive_cleanup_allowed": 0,
                },
            )

        self.assertEqual(len(self._mapping_rows()), 4)
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor_count_rows: list[tuple[int]] = conn.execute(
                "SELECT COUNT(*) FROM codex_session_mirror_offsets"
            ).fetchall()
        self.assertEqual(cursor_count_rows, [(4,)])


if __name__ == "__main__":
    _ = unittest.main()
