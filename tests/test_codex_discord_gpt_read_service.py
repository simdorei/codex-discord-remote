from __future__ import annotations

import sqlite3
import tempfile
import unittest
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TypeAlias

import codex_discord_gpt_candidates as candidates
import codex_discord_gpt_read_service as read_service
import codex_discord_gpt_snapshots as snapshots
import codex_discord_store as store
from codex_discord_store_schema import init_store_schema
from codex_thread_models import ThreadInfo

_TEMP_PREFIX = "app-gpt-discord-sync-todo-11-"
MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]
LoadCandidates: TypeAlias = Callable[[int], tuple[ThreadInfo, ...]]


def _thread(thread_id: str, updated_at: int) -> ThreadInfo:
    return ThreadInfo(thread_id, thread_id, "", updated_at, "", "gpt", "high", 1)


def _row(
    thread_id: str,
    discord_thread_id: int,
    updated_at: float,
    *,
    parent_id: int = 200,
    project_key: str = "codex:chats",
    managed_by: str = "gpt_chat",
    state: str = "active",
) -> MirrorRow:
    return (
        thread_id,
        project_key,
        f"title-{thread_id}",
        parent_id,
        discord_thread_id,
        updated_at,
        managed_by,
        state,
    )


def _candidate_loader(threads: list[ThreadInfo]) -> LoadCandidates:
    deps = candidates.GptCandidateDeps(
        load_user_root_threads=lambda _limit: list(threads),
        derive_project_key=lambda _thread: candidates.GPT_CHAT_PROJECT_KEY,
        filter_app_server_available_threads=lambda values: values,
        transport_name=lambda: candidates.RESIDENT_APP_SERVER_TRANSPORT,
    )

    def load(limit: int) -> tuple[ThreadInfo, ...]:
        return candidates.load_gpt_candidates_with_deps(deps=deps, limit=limit)

    return load


def _db_path(temp_dir: str, rows: tuple[MirrorRow, ...] = ()) -> Path:
    db_path = Path(temp_dir) / "read-service.sqlite"
    with closing(sqlite3.connect(db_path)) as conn, conn:
        init_store_schema(conn)
        _ = conn.executemany(
            "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return db_path


def _stored_rows(db_path: Path) -> tuple[MirrorRow, ...]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows: list[MirrorRow] = conn.execute(
            "SELECT * FROM mirror_threads ORDER BY codex_thread_id",
        ).fetchall()
    return tuple(rows)


def _key(user_id: int = 300) -> snapshots.GptSnapshotKey:
    return snapshots.GptSnapshotKey(100, 200, user_id)


def _service(
    db_path: Path,
    snapshot_store: snapshots.GptSnapshotStore,
    load_candidates: LoadCandidates,
) -> read_service.GptReadService:
    return read_service.GptReadService(
        db_path=db_path,
        deps=read_service.GptReadDeps(
            load_candidates=load_candidates,
            load_mappings=read_service.load_gpt_mappings_read_only,
            snapshot_store=snapshot_store,
            lookup_source=read_service.lookup_gpt_source,
        ),
    )


class GptReadServiceTests(unittest.TestCase):
    def test_list_and_synced_snapshots_are_independent(self) -> None:
        # Given: twelve ordered candidates and two active GPT mappings.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = _db_path(
                temp_dir,
                (
                    _row("candidate-000", 400, 50.0),
                    _row("missing-active", 401, 60.0),
                ),
            )
            candidate_rows = [
                _thread(f"candidate-{index:03d}", 100 - index) for index in range(12)
            ]
            snapshot_store = snapshots.GptSnapshotStore(monotonic=lambda: 10.0)
            service = _service(
                db_path,
                snapshot_store,
                _candidate_loader(candidate_rows),
            )

            # When: list, synced, and a second user's list are read.
            listed = service.list_candidates(_key())
            synced = service.list_synced(_key())
            other = service.list_candidates(_key(301), "1")

            # Then: ordering, defaults, kinds, and users remain independent.
            expected_list = tuple(f"candidate-{index:03d}" for index in range(10))
            self.assertEqual(
                tuple(item.thread.id for item in listed.items), expected_list
            )
            self.assertEqual(
                tuple(item.index for item in listed.items), tuple(range(1, 11))
            )
            self.assertEqual(
                synced.snapshot.codex_thread_ids, ("missing-active", "candidate-000")
            )
            self.assertEqual(other.snapshot.codex_thread_ids, ("candidate-000",))
            self.assertIs(
                snapshot_store.get(_key(), snapshots.GptSnapshotKind.LIST),
                listed.snapshot,
            )
            self.assertIs(
                snapshot_store.get(_key(), snapshots.GptSnapshotKind.SYNCED),
                synced.snapshot,
            )

    def test_list_filters_transitional_gpt_mappings_only(self) -> None:
        # Given: every selectable class and both transitional GPT states are available.
        rows = (
            _row("ordinary", 501, 50.0, project_key="project", managed_by="ordinary"),
            _row("active", 502, 40.0),
            _row("inactive", 503, 30.0, state="inactive"),
            _row("deactivating", 504, 20.0, state="deactivating"),
            _row("reactivating", 505, 10.0, state="reactivating"),
        )
        source_ids = ("unknown", *(row[0] for row in rows))
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            sources = [
                _thread(thread_id, 60 - index * 10)
                for index, thread_id in enumerate(source_ids)
            ]
            service = _service(
                _db_path(temp_dir, rows),
                snapshots.GptSnapshotStore(monotonic=lambda: 15.0),
                _candidate_loader(sources),
            )

            # When: LIST and SYNCED classify the same persisted ownership.
            listed = service.list_candidates(_key())
            synced = service.list_synced(_key())

            # Then: only GPT transitional identities are absent from LIST selection.
            expected = ("unknown", "ordinary", "active", "inactive")
            self.assertEqual(tuple(item.thread.id for item in listed.items), expected)
            self.assertEqual(listed.snapshot.codex_thread_ids, expected)
            self.assertEqual(
                tuple(item.mapping.codex_thread_id for item in synced.active),
                ("active",),
            )
            self.assertEqual(
                tuple(item.mapping.codex_thread_id for item in synced.audit),
                ("inactive", "deactivating", "reactivating"),
            )

    def test_list_count_bounds_are_exact_and_atomic(self) -> None:
        # Given: 105 ordered candidates and one saved list snapshot.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            rows = [
                _thread(f"candidate-{index:03d}", 200 - index) for index in range(105)
            ]
            calls: list[int] = []
            base_loader = _candidate_loader(rows)

            def load(limit: int) -> tuple[ThreadInfo, ...]:
                calls.append(limit)
                return base_loader(limit)

            snapshot_store = snapshots.GptSnapshotStore(monotonic=lambda: 20.0)
            service = _service(_db_path(temp_dir), snapshot_store, load)

            # When: both boundaries pass and invalid values follow them.
            one = service.list_candidates(_key(), "1")
            maximum = service.list_candidates(_key(), "100")
            exact_errors = {
                "": "Invalid GPT list count (malformed): <empty>.",
                "0": "Invalid GPT list count (out-of-range): 0.",
                "101": "Invalid GPT list count (out-of-range): 101.",
            }
            for raw_count, expected_error in exact_errors.items():
                with self.subTest(raw_count=raw_count):
                    with self.assertRaises(read_service.GptListCountError) as context:
                        _ = service.list_candidates(_key(), raw_count)
                    self.assertEqual(str(context.exception), expected_error)

            # Then: only displayed rows are saved and failures replace nothing.
            self.assertEqual(one.snapshot.codex_thread_ids, ("candidate-000",))
            self.assertEqual(len(maximum.items), 100)
            self.assertEqual(maximum.items[-1].thread.id, "candidate-099")
            self.assertEqual(calls, [0, 0])
            self.assertIs(
                snapshot_store.get(_key(), snapshots.GptSnapshotKind.LIST),
                maximum.snapshot,
            )

    def test_unavailable_and_legacy_parent_are_reported_without_deletion(self) -> None:
        # Given: every GPT lifecycle, an unavailable legacy parent, and ordinary data.
        rows = (
            _row("ordinary", 406, 60.0, project_key="project", managed_by="ordinary"),
            _row("active-available", 401, 50.0),
            _row("active-unavailable", 402, 40.0, parent_id=999),
            _row("active-wrong-project", 407, 35.0, project_key="other"),
            _row("deactivating", 403, 30.0, state="deactivating"),
            _row("inactive", 404, 20.0, parent_id=998, state="inactive"),
            _row("reactivating", 405, 10.0, state="reactivating"),
        )
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = _db_path(temp_dir, rows)
            before_rows = _stored_rows(db_path)
            before_bytes = db_path.read_bytes()
            service = _service(
                db_path,
                snapshots.GptSnapshotStore(monotonic=lambda: 30.0),
                _candidate_loader(
                    [_thread("active-available", 5), _thread("inactive", 4)]
                ),
            )

            # When: the synced read resolves availability and parent placement.
            result = service.list_synced(_key())

            # Then: active rows alone are indexed; audit and persistence are retained.
            active = {str(item.mapping.codex_thread_id): item for item in result.active}
            unavailable = active["active-unavailable"]
            self.assertEqual(
                unavailable.source_status, read_service.GptSourceStatus.UNAVAILABLE
            )
            self.assertEqual(
                unavailable.parent_status, read_service.GptParentStatus.LEGACY
            )
            self.assertEqual(unavailable.mapping.discord_channel_id, 999)
            self.assertEqual(
                result.snapshot.codex_thread_ids,
                ("active-available", "active-unavailable", "active-wrong-project"),
            )
            self.assertEqual(
                tuple(item.mapping.lifecycle_state for item in result.audit),
                (
                    store.MirrorThreadLifecycleState.DEACTIVATING,
                    store.MirrorThreadLifecycleState.INACTIVE,
                    store.MirrorThreadLifecycleState.REACTIVATING,
                ),
            )
            self.assertNotIn("ordinary", active)
            with self.assertRaises(snapshots.GptSnapshotSelectionError):
                _ = service.deps.snapshot_store.select(
                    _key(),
                    snapshots.GptSnapshotKind.SYNCED,
                    "4",
                )
            self.assertEqual(_stored_rows(db_path), before_rows)
            self.assertEqual(db_path.read_bytes(), before_bytes)
            self.assertEqual(tuple(Path(temp_dir).glob("*.sqlite-*")), ())
            db_path.unlink()
            self.assertFalse(db_path.exists())


if __name__ == "__main__":
    _ = unittest.main()
