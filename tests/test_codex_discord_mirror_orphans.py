from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
import unittest
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from typing import final, override

import codex_discord_mirror_sync_result as mirror_sync_result
import codex_discord_mirror_orphans as mirror_orphans
from codex_discord_store_schema import init_store_schema


class _DeleteFailure(Exception):
    pass


class _FakeThread:
    id: int
    owner_id: int | None
    name: str
    deleted: bool
    _delete_error: BaseException | None

    def __init__(
        self,
        thread_id: int,
        *,
        owner_id: int | None = None,
        name: str = "thread",
        delete_error: BaseException | None = None,
    ) -> None:
        self.id = thread_id
        self.owner_id = owner_id
        self.name = name
        self.deleted = False
        self._delete_error = delete_error

    async def delete(self, *, reason: str) -> None:
        if self._delete_error is not None:
            raise self._delete_error
        self.deleted = bool(reason)


async def _iter_threads(threads: list[_FakeThread]) -> AsyncIterator[_FakeThread]:
    for thread in threads:
        yield thread


class _FakeChannel:
    threads: list[_FakeThread]
    _archived_threads: list[_FakeThread]
    _archived_error: BaseException | None
    name: str

    def __init__(
        self,
        *,
        active_threads: list[_FakeThread] | None = None,
        archived_threads: list[_FakeThread] | None = None,
        archived_error: BaseException | None = None,
        name: str = "project",
    ) -> None:
        self.threads = active_threads or []
        self._archived_threads = archived_threads or []
        self._archived_error = archived_error
        self.name = name

    def archived_threads(self, *, limit: int | None) -> AsyncIterator[_FakeThread]:
        if self._archived_error is not None:
            raise self._archived_error
        return _iter_threads(self._archived_threads[:limit])


class _FailingActiveChannel:
    name: str = "failing-active"

    @property
    def threads(self) -> list[_FakeThread]:
        raise _DeleteFailure("active")

    def archived_threads(self, *, limit: int | None) -> AsyncIterator[_FakeThread]:
        _ = limit
        return _iter_threads([])


class _SlowArchivedChannel(_FakeChannel):
    async def _slow_archived(self) -> AsyncIterator[_FakeThread]:
        await asyncio.sleep(60.0)
        async for thread in _iter_threads([]):
            yield thread

    @override
    def archived_threads(self, *, limit: int | None) -> AsyncIterator[_FakeThread]:
        _ = limit
        return self._slow_archived()


class _WritingArchivedChannel(_FakeChannel):
    _db_path: Path

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self._db_path = db_path

    async def _write_then_finish(self) -> AsyncIterator[_FakeThread]:
        with closing(sqlite3.connect(self._db_path, timeout=0.05)) as conn, conn:
            _ = conn.execute(
                "INSERT INTO discord_processed_messages VALUES (9001, 1.0)"
            )
        async for thread in _iter_threads([]):
            yield thread

    @override
    def archived_threads(self, *, limit: int | None) -> AsyncIterator[_FakeThread]:
        _ = limit
        return self._write_then_finish()


@final
class MirrorOrphanCleanupTests(unittest.IsolatedAsyncioTestCase):
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None
    db_path = Path()
    lock = asyncio.Lock()

    @override
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self._temp_dir = temp_dir
        self.addCleanup(temp_dir.cleanup)
        self.db_path = Path(temp_dir.name) / "mirror.sqlite"
        with closing(sqlite3.connect(self.db_path)) as conn:
            init_store_schema(conn)
        self.lock = asyncio.Lock()

    async def test_cleanup_deletes_active_and_archived_orphan_threads(self) -> None:
        owned_active = _FakeThread(10, owner_id=123)
        owned_archived = _FakeThread(11, owner_id=None)
        known = _FakeThread(12, owner_id=123)
        foreign = _FakeThread(13, owner_id=999)
        channel = _FakeChannel(
            active_threads=[owned_active, known, foreign],
            archived_threads=[owned_archived],
        )

        result = await mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
            [channel],
            {12},
            123,
            db_path=self.db_path,
            configured_channel_lock=self.lock,
            delivery_exceptions=(_DeleteFailure,),
        )

        self.assertEqual(result["deleted"], 2)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(owned_active.deleted)
        self.assertTrue(owned_archived.deleted)
        self.assertFalse(known.deleted)
        self.assertFalse(foreign.deleted)

    async def test_cleanup_dedupes_thread_seen_in_active_and_archived_lists(
        self,
    ) -> None:
        duplicate = _FakeThread(10, owner_id=123)
        channel = _FakeChannel(active_threads=[duplicate], archived_threads=[duplicate])

        result = await mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
            [channel],
            set(),
            123,
            db_path=self.db_path,
            configured_channel_lock=self.lock,
            delivery_exceptions=(_DeleteFailure,),
        )

        self.assertEqual(result["deleted"], 1)
        self.assertTrue(duplicate.deleted)

    async def test_cleanup_records_delete_and_archive_failures(self) -> None:
        failing_thread = _FakeThread(
            10, owner_id=123, delete_error=_DeleteFailure("delete")
        )
        channel = _FakeChannel(
            active_threads=[failing_thread],
            archived_error=_DeleteFailure("archive"),
        )

        result = await mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
            [channel],
            set(),
            123,
            db_path=self.db_path,
            configured_channel_lock=self.lock,
            delivery_exceptions=(_DeleteFailure,),
        )

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(mirror_sync_result.cleanup_errors(result)), 1)
        self.assertFalse(failing_thread.deleted)

    async def test_active_scan_failure_prevents_all_orphan_deletions(self) -> None:
        orphan = _FakeThread(20, owner_id=123)
        result = await mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
            [_FakeChannel(active_threads=[orphan]), _FailingActiveChannel()],
            set(),
            123,
            db_path=self.db_path,
            configured_channel_lock=self.lock,
            delivery_exceptions=(_DeleteFailure,),
        )

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertFalse(orphan.deleted)

    async def test_archived_timeout_prevents_active_orphan_deletion(self) -> None:
        orphan = _FakeThread(21, owner_id=123)
        channel = _SlowArchivedChannel(active_threads=[orphan])
        result = await mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
            [channel],
            set(),
            123,
            db_path=self.db_path,
            configured_channel_lock=self.lock,
            delivery_exceptions=(_DeleteFailure,),
            archived_timeout_seconds=0.001,
        )

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertFalse(orphan.deleted)

    async def test_no_sqlite_transaction_spans_archived_scan_await(self) -> None:
        result = await mirror_orphans.cleanup_configured_channel_orphan_discord_threads(
            [_WritingArchivedChannel(self.db_path)],
            set(),
            123,
            db_path=self.db_path,
            configured_channel_lock=self.lock,
            delivery_exceptions=(_DeleteFailure,),
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT message_id FROM discord_processed_messages"
            ).fetchall()
        self.assertEqual(result["failed"], 0)
        self.assertEqual(rows, [(9001,)])


if __name__ == "__main__":
    _ = unittest.main()
