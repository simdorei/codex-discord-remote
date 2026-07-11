from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Final, TypeAlias
import unittest

import codex_discord_store as store
from codex_discord_store_schema import init_store_schema


MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]
JournalRow: TypeAlias = tuple[str, str, str, int, str, str, int | None, float, float]
LifecycleOperation: TypeAlias = Callable[[Path, str], "store.GptLifecycleTransition"]
_TEMP_PREFIX: Final = "app-gpt-discord-sync-todo-04-"


class GptLifecycleTests(unittest.TestCase):
    _DEACTIVATE: store.GptLifecycleOperation = store.GptLifecycleOperation.BEGIN_DEACTIVATION
    _FINISH_DEACTIVATE: store.GptLifecycleOperation = store.GptLifecycleOperation.FINALIZE_DEACTIVATION
    _REACTIVATE: store.GptLifecycleOperation = store.GptLifecycleOperation.BEGIN_REACTIVATION
    _FINISH_REACTIVATE: store.GptLifecycleOperation = store.GptLifecycleOperation.FINALIZE_REACTIVATION
    _CLEAR: store.GptLifecycleOperation = store.GptLifecycleOperation.BEGIN_CLEAR_DEACTIVATION

    def _db_path(self, temp_dir: str) -> Path:
        db_path = Path(temp_dir) / "lifecycle.sqlite"
        with closing(sqlite3.connect(db_path)) as conn:
            init_store_schema(conn)
        return db_path

    def _insert_rows(self, db_path: Path, rows: list[MirrorRow]) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            _ = conn.executemany(
                "INSERT INTO mirror_threads (codex_thread_id, project_key, thread_title, "
                + "discord_channel_id, discord_thread_id, updated_at, managed_by, "
                + "lifecycle_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()

    def _row(self, db_path: Path, owner: str) -> MirrorRow:
        with closing(sqlite3.connect(db_path)) as conn:
            rows: list[MirrorRow] = conn.execute(
                "SELECT * FROM mirror_threads WHERE codex_thread_id = ?",
                (owner,),
            ).fetchall()
        return rows[0]

    def _snapshot(self, db_path: Path) -> tuple[list[MirrorRow], list[JournalRow]]:
        with closing(sqlite3.connect(db_path)) as conn:
            mappings: list[MirrorRow] = conn.execute(
                "SELECT * FROM mirror_threads ORDER BY codex_thread_id"
            ).fetchall()
            journals: list[JournalRow] = conn.execute(
                "SELECT * FROM gpt_chat_creation_ops ORDER BY codex_thread_id"
            ).fetchall()
        return mappings, journals

    def test_allowed_transitions_and_atomic_fifth_slot(self) -> None:
        # Given: four occupied slots and one exact inactive GPT mapping.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            rows: list[MirrorRow] = [
                (f"active-{index}", "codex:chats", "Active", 100, 200 + index, 1.0, "gpt_chat", "active")
                for index in range(4)
            ]
            rows.append(("target", "codex:chats", "Target", 100, 300, 1.0, "gpt_chat", "inactive"))
            self._insert_rows(db_path, rows)

            # When: reactivation takes slot five and each retry/finalization path runs.
            reserved = store.transition_gpt_lifecycle(db_path, "target", self._REACTIVATE)
            reserved_at = self._row(db_path, "target")[5]
            retry = store.transition_gpt_lifecycle(db_path, "target", self._REACTIVATE)
            retry_at = self._row(db_path, "target")[5]
            activated = store.transition_gpt_lifecycle(db_path, "target", self._FINISH_REACTIVATE)
            deactivating = store.transition_gpt_lifecycle(db_path, "target", self._DEACTIVATE)
            deactivating_at = self._row(db_path, "target")[5]
            deactivation_retry = store.transition_gpt_lifecycle(db_path, "target", self._DEACTIVATE)
            deactivation_retry_at = self._row(db_path, "target")[5]
            inactive = store.transition_gpt_lifecycle(db_path, "target", self._FINISH_DEACTIVATE)
            _ = store.transition_gpt_lifecycle(db_path, "target", self._REACTIVATE)
            cleared = store.transition_gpt_lifecycle(db_path, "target", self._CLEAR)
            final = store.transition_gpt_lifecycle(db_path, "target", self._FINISH_DEACTIVATE)

            # Then: only real transitions retimestamp, capacity is exact, and no journal exists.
            self.assertTrue(reserved.changed)
            self.assertFalse(retry.changed)
            self.assertEqual(retry_at, reserved_at)
            self.assertEqual(activated.state, store.MirrorThreadLifecycleState.ACTIVE)
            self.assertEqual(deactivating.state, store.MirrorThreadLifecycleState.DEACTIVATING)
            self.assertFalse(deactivation_retry.changed)
            self.assertEqual(deactivation_retry_at, deactivating_at)
            self.assertEqual(inactive.state, store.MirrorThreadLifecycleState.INACTIVE)
            self.assertEqual(cleared.previous_state, store.MirrorThreadLifecycleState.REACTIVATING)
            self.assertEqual(final.state, store.MirrorThreadLifecycleState.INACTIVE)
            self.assertGreater(self._row(db_path, "target")[5], 1.0)
            self.assertEqual(store.audit_gpt_capacity(db_path).used_slots, 4)
            self.assertEqual(self._snapshot(db_path)[1], [])

    def test_transition_table_covers_every_state_and_retry(self) -> None:
        operations: tuple[tuple[str, LifecycleOperation], ...] = (
            ("deactivate", lambda path, owner: store.transition_gpt_lifecycle(path, owner, self._DEACTIVATE)),
            ("finish-deactivate", lambda path, owner: store.transition_gpt_lifecycle(path, owner, self._FINISH_DEACTIVATE)),
            ("reactivate", lambda path, owner: store.transition_gpt_lifecycle(path, owner, self._REACTIVATE)),
            ("finish-reactivate", lambda path, owner: store.transition_gpt_lifecycle(path, owner, self._FINISH_REACTIVATE)),
            ("clear", lambda path, owner: store.transition_gpt_lifecycle(path, owner, self._CLEAR)),
        )
        allowed = {
            ("deactivate", "active"): "deactivating",
            ("deactivate", "deactivating"): "deactivating",
            ("finish-deactivate", "deactivating"): "inactive",
            ("reactivate", "inactive"): "reactivating",
            ("reactivate", "reactivating"): "reactivating",
            ("finish-reactivate", "reactivating"): "active",
            ("clear", "active"): "deactivating",
            ("clear", "deactivating"): "deactivating",
            ("clear", "reactivating"): "deactivating",
        }

        # Given/When/Then: every action/state pair either follows the table or is non-mutating.
        for operation_name, operation in operations:
            for state in ("active", "deactivating", "inactive", "reactivating"):
                with self.subTest(operation=operation_name, state=state):
                    with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
                        db_path = self._db_path(temp_dir)
                        self._insert_rows(
                            db_path,
                            [("owner", "codex:chats", "Owner", 10, 20, 1.0, "gpt_chat", state)],
                        )
                        before = self._snapshot(db_path)
                        expected = allowed.get((operation_name, state))
                        if expected is None:
                            with self.assertRaises(store.GptLifecycleTransitionError):
                                _ = operation(db_path, "owner")
                            self.assertEqual(self._snapshot(db_path), before)
                        else:
                            result = operation(db_path, "owner")
                            self.assertEqual(result.state.value, expected)
                            stored_at = self._row(db_path, "owner")[5]
                            self.assertEqual(stored_at == 1.0, expected == state)

    def test_unfinished_journal_capacity_deduplicates_only_exact_owner(self) -> None:
        # Given: duplicate Discord owners, an ordinary row, and two unfinished creations.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            self._insert_rows(
                db_path,
                [
                    ("gpt-a", "codex:chats", "A", 10, 50, 1.0, "gpt_chat", "active"),
                    ("gpt-b", "codex:chats", "B", 10, 50, 1.0, "gpt_chat", "active"),
                    ("target", "codex:chats", "T", 10, 51, 1.0, "gpt_chat", "inactive"),
                    ("ordinary", "project", "O", 10, 52, 1.0, "ordinary", "active"),
                ],
            )
            journals: list[JournalRow] = [
                ("target", "codex:chats", "T", 10, "a" * 32, "prepared", None, 1.0, 1.0),
                ("new-owner", "codex:chats", "N", 10, "b" * 32, "create_started", None, 1.0, 1.0),
            ]
            with closing(sqlite3.connect(db_path)) as conn:
                _ = conn.executemany("INSERT INTO gpt_chat_creation_ops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", journals)
                conn.commit()
            journal_before = self._snapshot(db_path)[1]

            # When: capacity is audited and the already-reserved exact owner reactivates.
            audit = store.audit_gpt_capacity(db_path, requested_increase=1)
            result = store.transition_gpt_lifecycle(db_path, "target", self._REACTIVATE)

            # Then: distinct owners consume four slots and mapping+journal identity consumes one.
            self.assertEqual((audit.used_slots, audit.projected_slots), (4, 5))
            self.assertTrue(result.changed)
            self.assertEqual(store.audit_gpt_capacity(db_path).used_slots, 4)
            self.assertEqual(self._snapshot(db_path)[1], journal_before)
            with self.assertRaises(store.GptCapacityExceededError):
                _ = store.audit_gpt_capacity(db_path, requested_increase=2)

    def test_forbidden_transition_overfive_increase_and_race_do_not_mutate(self) -> None:
        # Given: a grandfathered over-five database and a separate two-candidate fifth-slot race.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            legacy_path = self._db_path(temp_dir)
            legacy_rows: list[MirrorRow] = [
                (f"legacy-{index}", "codex:chats", "Legacy", 10, 100 + index, 1.0, "gpt_chat", "active")
                for index in range(6)
            ]
            legacy_rows.append(("inactive", "codex:chats", "Inactive", 10, 200, 1.0, "gpt_chat", "inactive"))
            legacy_rows.append(("reserved", "codex:chats", "Reserved", 10, 201, 1.0, "gpt_chat", "reactivating"))
            self._insert_rows(legacy_path, legacy_rows)
            legacy_before = self._snapshot(legacy_path)

            # When: an increase and a forbidden finalization are attempted.
            self.assertEqual(store.audit_gpt_capacity(legacy_path).used_slots, 7)
            with self.assertRaises(store.GptCapacityExceededError):
                _ = store.transition_gpt_lifecycle(legacy_path, "inactive", self._REACTIVATE)
            with self.assertRaises(store.GptLifecycleTransitionError):
                _ = store.transition_gpt_lifecycle(legacy_path, "inactive", self._FINISH_REACTIVATE)

            # Then: the grandfathered state is unchanged, while zero-delta work stays legal.
            self.assertEqual(self._snapshot(legacy_path), legacy_before)
            self.assertEqual(store.audit_gpt_capacity(legacy_path, requested_increase=0).projected_slots, 7)
            retry = store.transition_gpt_lifecycle(legacy_path, "reserved", self._REACTIVATE)
            self.assertFalse(retry.changed)
            _ = store.transition_gpt_lifecycle(legacy_path, "reserved", self._FINISH_REACTIVATE)
            for owner in ("legacy-0", "legacy-1", "legacy-2"):
                _ = store.transition_gpt_lifecycle(legacy_path, owner, self._DEACTIVATE)
                _ = store.transition_gpt_lifecycle(legacy_path, owner, self._FINISH_DEACTIVATE)
            self.assertEqual(store.audit_gpt_capacity(legacy_path).used_slots, 4)
            _ = store.transition_gpt_lifecycle(legacy_path, "inactive", self._REACTIVATE)
            self.assertEqual(store.audit_gpt_capacity(legacy_path).used_slots, 5)

            race_path = Path(temp_dir) / "race.sqlite"
            with closing(sqlite3.connect(race_path)) as conn:
                init_store_schema(conn)
            race_rows: list[MirrorRow] = [
                (f"active-{index}", "codex:chats", "Active", 20, 300 + index, 1.0, "gpt_chat", "active")
                for index in range(4)
            ]
            race_rows.extend(
                [
                    ("race-a", "codex:chats", "A", 20, 400, 1.0, "gpt_chat", "inactive"),
                    ("race-b", "codex:chats", "B", 20, 401, 1.0, "gpt_chat", "inactive"),
                ]
            )
            self._insert_rows(race_path, race_rows)
            barrier = threading.Barrier(3)

            def reserve(owner: str) -> bool:
                _ = barrier.wait()
                try:
                    _ = store.transition_gpt_lifecycle(race_path, owner, self._REACTIVATE)
                except store.GptCapacityExceededError:
                    return False
                return True

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(reserve, owner) for owner in ("race-a", "race-b")]
                _ = barrier.wait()
                outcomes = [future.result(timeout=10.0) for future in futures]

            self.assertEqual(sorted(outcomes), [False, True])
            self.assertEqual(store.audit_gpt_capacity(race_path).used_slots, 5)
            self.assertEqual(
                sorted((self._row(race_path, owner)[7] for owner in ("race-a", "race-b"))),
                ["inactive", "reactivating"],
            )

    def test_invalid_identity_state_and_request_are_typed_and_atomic(self) -> None:
        # Given: ordinary, wrong-project, malformed-state, and missing identities.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            self._insert_rows(
                db_path,
                [
                    ("ordinary", "project", "O", 10, 20, 1.0, "ordinary", "active"),
                    ("wrong-project", "project", "P", 10, 21, 1.0, "gpt_chat", "active"),
                ],
            )
            with closing(sqlite3.connect(db_path)) as conn:
                _ = conn.execute("PRAGMA ignore_check_constraints = ON")
                _ = conn.execute(
                    "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("bad-state", "codex:chats", "S", 10, 22, 1.0, "gpt_chat", "corrupt"),
                )
                conn.commit()
            before = self._snapshot(db_path)

            # When/Then: each invalid boundary raises its exact type and changes nothing.
            cases: tuple[tuple[str, type[RuntimeError]], ...] = (
                ("missing", store.GptMappingNotFoundError),
                ("ordinary", store.GptLifecycleOwnerError),
                ("wrong-project", store.GptLifecycleProjectError),
                ("bad-state", store.GptLifecycleStateError),
            )
            for owner, error_type in cases:
                with self.subTest(owner=owner):
                    with self.assertRaises(error_type):
                        _ = store.transition_gpt_lifecycle(db_path, owner, self._DEACTIVATE)
                    self.assertEqual(self._snapshot(db_path), before)
            with self.assertRaises(store.GptCapacityRequestError):
                _ = store.audit_gpt_capacity(db_path, requested_increase=-1)
            self.assertEqual(self._snapshot(db_path), before)


if __name__ == "__main__":
    _ = unittest.main()
