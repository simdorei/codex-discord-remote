import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import final
import unittest

import anyio
from unittest.mock import patch

import codex_discord_gpt_creation_journal as journal
import codex_discord_gpt_candidates as gpt_candidates
import codex_discord_gpt_discord_adapter as adapter
import codex_discord_gpt_lifecycle as lifecycle
import codex_discord_gpt_snapshots as snapshots
import codex_discord_gpt_sync_workflow as workflow
from codex_discord_gpt_ownership import MirrorThreadOwnership
from codex_discord_store_schema import init_store_schema
from codex_thread_models import ThreadInfo
from tests.test_codex_discord_gpt_discord_adapter import (
    FakeClient,
    FakeGuild,
    FakeTextChannel,
    FakeThread,
)


type MirrorRow = tuple[str, str, str, int, int, float, str, str]
type StoredState = tuple[list[MirrorRow], list[tuple[str, str, int | None]]]
type Client = adapter.DiscordClient
type Mapping = MirrorThreadOwnership
type Creation = journal.GptCreationOperation
type Recovery = journal.GptCreationRecoveryRequest
type LifeOp = lifecycle.GptLifecycleOperation
type Transition = lifecycle.GptLifecycleTransition
_TEMP_PREFIX = "app-gpt-discord-sync-todo-13-"
_MAPPING_SQL = "SELECT * FROM mirror_threads ORDER BY codex_thread_id"
_OPERATION_SQL = "SELECT codex_thread_id,status,discord_thread_id FROM gpt_chat_creation_ops ORDER BY codex_thread_id"
_LIST_KIND = snapshots.GptSnapshotKind.LIST
_TRANSITION = lifecycle.transition_gpt_lifecycle
_FINALIZE = lifecycle.GptLifecycleOperation.FINALIZE_REACTIVATION


@final
class FakeExternal:
    def __init__(self, lock: anyio.Lock, db_path: Path) -> None:
        self.lock, self.db_path = lock, db_path
        self.resolved = self.created = self.recovered = 0
        self.revived: list[tuple[int, int]] = []
        self.guild = FakeGuild(1)
        self.channel = FakeTextChannel(10, self.guild)
        self.retained = FakeThread(0, "Retained", self.channel)
        self.history_identity = self.retained.history
        self.fail_revival = self.block_revival = False
        self.entered, self.release = anyio.Event(), anyio.Event()

    async def resolve(self, _client: Client) -> FakeTextChannel:
        assert self.lock.locked()
        self.resolved += 1
        return self.channel

    async def revive(self, _client: Client, mapping: Mapping) -> FakeThread:
        assert self.lock.locked()
        self.revived.append((mapping.discord_thread_id, mapping.discord_channel_id))
        self.entered.set()
        if self.block_revival:
            await self.release.wait()
        if self.fail_revival:
            raise adapter.GptDiscordRetainedThreadError()
        self.retained.archived = self.retained.locked = False
        return self.retained

    async def create(self, _client: Client, operation: Creation) -> FakeThread:
        assert (
            self.lock.locked()
            and operation.status is journal.GptCreationStatus.CREATE_STARTED
        )
        self.created += 1
        return FakeThread(300 + self.created, str(operation.marker_token), self.channel)

    async def recover(self, _client: Client, request: Recovery) -> FakeThread:
        assert self.lock.locked()
        self.recovered += 1
        request.finalize_cursor(request.operation)
        _ = _TRANSITION(self.db_path, request.operation.codex_thread_id, _FINALIZE)
        journal.complete_gpt_creation(self.db_path, request.operation)
        assert (thread_id := request.operation.discord_thread_id) is not None
        return FakeThread(int(thread_id), "Recovered", self.channel)


def mirror_row(
    owner: str, title: str, parent: int, thread: int, state: str
) -> MirrorRow:
    return owner, "codex:chats", title, parent, thread, 1.0, "gpt_chat", state


class GptSyncWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def stored(self, db_path: Path) -> StoredState:
        with closing(sqlite3.connect(db_path)) as conn:
            mappings: list[MirrorRow] = conn.execute(_MAPPING_SQL).fetchall()
            operations: list[tuple[str, str, int | None]] = conn.execute(
                _OPERATION_SQL
            ).fetchall()
        return mappings, operations

    def setup_case(
        self,
        rows: list[MirrorRow],
        specs: tuple[tuple[str, str], ...] | None = None,
    ) -> tuple[Path, workflow.GptSyncRequest, FakeExternal, anyio.Lock]:
        root = self.enterContext(tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX))
        rollout = Path(root) / "rollout.jsonl"
        _ = rollout.write_text("{}\n", encoding="utf-8")
        specs = specs or tuple((row[0], row[2]) for row in rows)
        sources = tuple(
            ThreadInfo(*spec, "", 1, str(rollout), "", "", 0) for spec in specs
        )
        db_path = Path(root) / "workflow.sqlite"
        with closing(sqlite3.connect(db_path)) as conn, conn:
            init_store_schema(conn)
            _ = conn.executemany(
                "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
        store = snapshots.GptSnapshotStore(monotonic=lambda: 1.0)
        key = snapshots.GptSnapshotKey(1, 10, 5)
        _ = store.replace(key, _LIST_KIND, tuple(item.id for item in sources))
        lock = anyio.Lock()
        external = FakeExternal(lock, db_path)

        _ = self.enterContext(
            patch.object(gpt_candidates, "load_gpt_candidates", return_value=sources)
        )
        self.enterContext(
            patch.multiple(
                adapter,
                resolve_configured_text_channel=external.resolve,
                revive_retained_gpt_thread=external.revive,
                create_gpt_marker_thread=external.create,
                recover_gpt_creation=external.recover,
            )
        )
        raw_indices = ",".join(str(index) for index in range(1, len(sources) + 1))
        client = FakeClient(external.guild)
        request = workflow.GptSyncRequest(
            db_path, store, key, raw_indices, client, lock
        )
        return db_path, request, external, lock

    async def test_mixed_active_inactive_new_is_additive_and_idempotent(self) -> None:
        source_specs = (("active", "Active"), ("inactive", "Inactive"), ("new", "New"))
        rows: list[MirrorRow] = [
            mirror_row("active", "Active", 10, 101, "active"),
            mirror_row("inactive", "Inactive", 55, 202, "inactive"),
        ]
        db_path, request, external, _lock = self.setup_case(rows, source_specs)

        await workflow.sync_gpt_selection(request)
        await workflow.sync_gpt_selection(request)

        assert (external.created, external.revived) == (1, [(202, 55)])
        assert [row[7] for row in self.stored(db_path)[0]] == ["active"] * 3
        assert self.stored(db_path)[1] == []

    async def test_preflight_zero_mutation_then_external_failure_retains_retry_state(
        self,
    ) -> None:
        rows = [mirror_row("target", "Wrong", 55, 202, "inactive")]
        db_path, request, external, lock = self.setup_case(
            rows, (("target", "Target"),)
        )
        before = self.stored(db_path)
        with self.assertRaises(workflow.GptSyncPreflightError):
            _ = await workflow.sync_gpt_selection(request)
        assert self.stored(db_path) == before
        assert (external.created, external.revived) == (0, [])
        with closing(sqlite3.connect(db_path)) as conn, conn:
            _ = conn.execute("UPDATE mirror_threads SET thread_title='Target'")
        external.fail_revival = external.block_revival = True
        errors: list[workflow.GptSyncRetryableError] = []

        async def run_failure() -> None:
            try:
                _ = await workflow.sync_gpt_selection(request)
            except workflow.GptSyncRetryableError as exc:
                errors.append(exc)

        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(run_failure)
            await external.entered.wait()
            assert lock.locked()
            assert self.stored(db_path)[0][0][7] == "reactivating"
            external.release.set()

        assert len(errors) == 1
        assert self.stored(db_path)[0][0][7] == "reactivating"
        assert self.stored(db_path)[1] == []

    async def test_no_project_resync_revives_same_archived_locked_thread_without_creation(
        self,
    ) -> None:
        rows = [mirror_row("no-project", "No project", 55, 77, "inactive")]
        db_path, request, external, _lock = self.setup_case(rows)

        await workflow.sync_gpt_selection(request)

        mapping = self.stored(db_path)[0][0]
        assert (mapping[3], mapping[4], mapping[7]) == (55, 77, "active")
        assert external.revived == [(77, 55)]
        assert external.retained.history is external.history_identity
        assert external.retained.history == ["first", "second"]
        assert external.retained.archived is external.retained.locked is False
        assert external.created == 0 and self.stored(db_path)[1] == []

    async def test_no_project_missing_retained_id_is_retryable_without_replacement(
        self,
    ) -> None:
        rows = [mirror_row("missing", "Missing", 55, 88, "inactive")]
        db_path, request, external, _lock = self.setup_case(rows)
        external.fail_revival = True

        with self.assertRaises(workflow.GptSyncRetryableError):
            _ = await workflow.sync_gpt_selection(request)

        mapping = self.stored(db_path)[0][0]
        assert (mapping[3], mapping[4], mapping[7]) == (55, 88, "reactivating")
        assert (external.created, self.stored(db_path)[1]) == (0, [])

    async def test_second_reservation_failure_is_public_safe_and_retry_converges(
        self,
    ) -> None:
        rows = [
            mirror_row("first", "First", 55, 201, "inactive"),
            mirror_row("second", "Second", 56, 202, "inactive"),
        ]
        db_path, request, external, _lock = self.setup_case(rows)
        calls = 0

        def fail_second(
            db_path: Path, codex_thread_id: str, operation: LifeOp
        ) -> Transition:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("sensitive_internal_detail")
            return _TRANSITION(db_path, codex_thread_id, operation)

        with (
            patch.object(lifecycle, "transition_gpt_lifecycle", fail_second),
            self.assertRaises(workflow.GptSyncRetryableError) as caught,
        ):
            await workflow.sync_gpt_selection(request)

        assert type(caught.exception) is workflow.GptSyncRetryableError
        assert "sensitive_internal_detail" not in str(caught.exception)
        states = [row[7] for row in self.stored(db_path)[0]]
        assert states == ["reactivating", "inactive"]
        assert self.stored(db_path)[1] == []
        assert (external.created, external.revived) == (0, [])

        await workflow.sync_gpt_selection(request)

        mappings, operations = self.stored(db_path)
        assert external.revived == [(201, 55), (202, 56)]
        assert [(row[3], row[4]) for row in mappings] == [(55, 201), (56, 202)]
        assert [row[7] for row in mappings] == ["active", "active"]
        assert (external.created, operations) == (0, [])

    async def test_ordinary_owner_after_inactive_gpt_rejects_whole_selection_preflight(
        self,
    ) -> None:
        ordinary = mirror_row("ordinary", "Ordinary", 56, 202, "active")
        ordinary = (*ordinary[:6], "ordinary", ordinary[7])
        rows: list[MirrorRow] = [
            mirror_row("first", "First", 55, 201, "inactive"),
            ordinary,
        ]
        db_path, request, external, _lock = self.setup_case(rows)
        before = self.stored(db_path)
        snapshot = request.snapshot_store.get(request.snapshot_key, _LIST_KIND)

        with self.assertRaises(workflow.GptSyncPreflightError) as caught:
            await workflow.sync_gpt_selection(request)

        assert self.stored(db_path) == before
        assert request.snapshot_store.get(request.snapshot_key, _LIST_KIND) is snapshot
        assert external.resolved == external.created == external.recovered == 0
        assert external.revived == []
        assert type(caught.exception) is workflow.GptSyncPreflightError
