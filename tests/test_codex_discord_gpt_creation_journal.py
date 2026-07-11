from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import Final, TypeAlias
import unittest
from unittest.mock import patch

import codex_discord_gpt_creation_journal as journal
from codex_discord_gpt_lifecycle import GptCapacityExceededError, GptLifecycleOperation, transition_gpt_lifecycle
from codex_discord_gpt_ownership import CodexThreadId, DiscordChannelId, DiscordThreadId
from codex_discord_store_schema import init_store_schema


MirrorRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]
JournalRow: TypeAlias = tuple[str, str, str, int, str, str, int | None, float, float]
_TEMP_PREFIX: Final = "app-gpt-discord-sync-todo-05-"


class GptCreationJournalTests(unittest.TestCase):
    def _db_path(self, temp_dir: str) -> Path:
        db_path = Path(temp_dir) / "journal.sqlite"
        with closing(sqlite3.connect(db_path)) as conn:
            init_store_schema(conn)
        return db_path

    def _intent(self, owner: str) -> journal.GptCreationIntent:
        return journal.GptCreationIntent(CodexThreadId(owner), "Original title " + ("x" * 120), DiscordChannelId(500))

    def _snapshot(self, db_path: Path) -> tuple[list[MirrorRow], list[JournalRow]]:
        with closing(sqlite3.connect(db_path)) as conn:
            mappings: list[MirrorRow] = conn.execute("SELECT * FROM mirror_threads ORDER BY codex_thread_id").fetchall()
            operations: list[JournalRow] = conn.execute("SELECT * FROM gpt_chat_creation_ops ORDER BY codex_thread_id").fetchall()
        return mappings, operations

    def _insert_mappings(self, db_path: Path, rows: list[MirrorRow]) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            _ = conn.executemany("INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            conn.commit()

    def _execute(self, db_path: Path, script: str) -> None:
        with closing(sqlite3.connect(db_path)) as conn:
            _ = conn.executescript(script)
            conn.commit()

    def _started(self, db_path: Path, owner: str) -> journal.GptCreationOperation:
        return journal.mark_gpt_creation_started(db_path, journal.prepare_gpt_creation(db_path, self._intent(owner)))

    def _identified(self, db_path: Path, owner: str) -> journal.GptCreationOperation:
        return journal.handoff_gpt_creation(db_path, self._started(db_path, owner), DiscordThreadId(700))

    def test_exact_marker_and_atomic_identical_handoff_resume(self) -> None:
        # Given: one new operation and display titles spanning every boundary class.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            prepared = journal.prepare_gpt_creation(db_path, self._intent("owner"))
            token = str(prepared.marker_token)
            title_cases = (
                ("", ""),
                (" \t\n ", ""),
                ("한글 제목 😀", "한글 제목 😀"),
                (f"] {token}\n[ hostile", f"] {token} [ hostile"),
                ("L" * 200, "L" * (100 - len(token) - 1)),
            )

            # When/Then: each name has one exact leading marker and safe display suffix.
            for title, expected_suffix in title_cases:
                with self.subTest(title_class=len(title)):
                    operation = replace(prepared, thread_title=title)
                    name = journal.format_gpt_creation_thread_name(operation)
                    expected = token if not expected_suffix else f"{token} {expected_suffix}"
                    self.assertEqual(name, expected)
                    self.assertLessEqual(len(name), 100)
                    self.assertEqual(journal.parse_gpt_creation_thread_name(name), prepared.nonce)
                    self.assertIsNone(journal.parse_gpt_creation_thread_name("x" + name))

            # When: creation is handed off, retried, finalized, and retried again.
            started = journal.mark_gpt_creation_started(db_path, prepared)
            identified = journal.handoff_gpt_creation(db_path, started, DiscordThreadId(700))
            handoff_snapshot = self._snapshot(db_path)
            self.assertEqual(journal.handoff_gpt_creation(db_path, identified, DiscordThreadId(700)), identified)

            # Then: nonce, exact mapping, and both retry boundaries are unchanged.
            self.assertRegex(str(prepared.nonce), r"^[0-9a-f]{32}$")
            self.assertEqual(prepared.status, journal.GptCreationStatus.PREPARED)
            self.assertEqual(identified.status, journal.GptCreationStatus.DISCORD_IDENTIFIED)
            self.assertEqual(identified.discord_thread_id, DiscordThreadId(700))
            self.assertEqual(self._snapshot(db_path), handoff_snapshot)
            self.assertEqual(handoff_snapshot[0], [("owner", "codex:chats", prepared.thread_title, 500, 700, identified.updated_at, "gpt_chat", "reactivating")])
            _ = transition_gpt_lifecycle(db_path, "owner", GptLifecycleOperation.FINALIZE_REACTIVATION)
            active_snapshot = self._snapshot(db_path)
            self.assertEqual(journal.handoff_gpt_creation(db_path, identified, DiscordThreadId(700)), identified)
            self.assertEqual(self._snapshot(db_path), active_snapshot)

    def test_loose_marker_conflicting_identity_and_partial_handoff_do_not_mutate(self) -> None:
        # Given: loose markers and every incompatible mapping identity/state.
        nonce = journal.GptCreationNonce("a" * 32)
        token = f"[gpt-sync:{nonce}]"
        loose_names = (f"title {token}", f"x{token}", "[gpt-sync:" + ("A" * 32) + "] title", "[gpt-sync:" + ("a" * 31) + "] title", token + "title", token + "\nother")
        for name in loose_names:
            self.assertIsNone(journal.parse_gpt_creation_thread_name(name))
        self.assertEqual(journal.parse_gpt_creation_thread_name(token), nonce)

        title = self._intent("owner").thread_title
        conflict_rows: tuple[tuple[str, list[MirrorRow]], ...] = (
            ("discord-id", [("owner", "codex:chats", title, 500, 701, 1.0, "gpt_chat", "reactivating")]),
            ("parent", [("owner", "codex:chats", title, 501, 700, 1.0, "gpt_chat", "reactivating")]),
            ("title", [("owner", "codex:chats", "Different", 500, 700, 1.0, "gpt_chat", "reactivating")]),
            ("project", [("owner", "other", title, 500, 700, 1.0, "gpt_chat", "reactivating")]),
            ("ordinary", [("owner", "codex:chats", title, 500, 700, 1.0, "ordinary", "active")]),
            ("inactive", [("owner", "codex:chats", title, 500, 700, 1.0, "gpt_chat", "inactive")]),
            ("deactivating", [("owner", "codex:chats", title, 500, 700, 1.0, "gpt_chat", "deactivating")]),
            ("other-owner", [("other", "other", "Other", 500, 700, 1.0, "ordinary", "active")]),
            ("duplicate", [("owner", "codex:chats", title, 500, 700, 1.0, "gpt_chat", "reactivating"), ("other", "other", "Other", 500, 700, 1.0, "ordinary", "active")]),
        )

        # When/Then: each conflict is typed and byte-for-byte non-mutating.
        for label, rows in conflict_rows:
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
                db_path = self._db_path(temp_dir)
                started = self._started(db_path, "owner")
                self._insert_mappings(db_path, rows)
                before = self._snapshot(db_path)
                with self.assertRaises(journal.GptCreationAmbiguityError):
                    _ = journal.handoff_gpt_creation(db_path, started, DiscordThreadId(700))
                self.assertEqual(self._snapshot(db_path), before)

        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            started = self._started(db_path, "owner")
            self._execute(db_path, "PRAGMA ignore_check_constraints=ON; UPDATE gpt_chat_creation_ops SET status='discord_identified';")
            malformed_before = self._snapshot(db_path)
            with self.assertRaises(journal.GptCreationAmbiguityError):
                _ = journal.handoff_gpt_creation(db_path, started, DiscordThreadId(700))
            self.assertEqual(self._snapshot(db_path), malformed_before)

        # Given/When/Then: both insert and compatible-update paths roll back on journal failure.
        for existing_mapping in (False, True):
            with self.subTest(existing_mapping=existing_mapping), tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
                db_path = self._db_path(temp_dir)
                started = self._started(db_path, "owner")
                if existing_mapping:
                    self._insert_mappings(db_path, [("owner", "codex:chats", started.thread_title, 500, 700, 1.0, "gpt_chat", "reactivating")])
                self._execute(db_path, "CREATE TRIGGER fail_handoff BEFORE UPDATE ON gpt_chat_creation_ops BEGIN SELECT RAISE(ABORT, 'injected handoff failure'); END;")
                before = self._snapshot(db_path)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected handoff failure"):
                    _ = journal.handoff_gpt_creation(db_path, started, DiscordThreadId(700))
                self.assertEqual(self._snapshot(db_path), before)

    def test_capacity_race_and_reactivation_never_create_a_journal(self) -> None:
        # Given: four distinct GPT owners and two concurrent fifth-slot contenders.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            self._insert_mappings(db_path, [(f"active-{i}", "codex:chats", "Active", 500, 600, 1.0, "gpt_chat", "active") for i in range(4)])
            barrier = threading.Barrier(3)

            def reserve(owner: str) -> bool:
                _ = barrier.wait()
                try:
                    _ = journal.prepare_gpt_creation(db_path, self._intent(owner))
                except GptCapacityExceededError:
                    return False
                return True

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(reserve, owner) for owner in ("race-a", "race-b")]
                _ = barrier.wait()
                outcomes = [future.result(timeout=10.0) for future in futures]
            self.assertEqual(sorted(outcomes), [False, True])
            self.assertEqual(len(self._snapshot(db_path)[1]), 1)

        # Given/When/Then: reactivation never journals and cannot be prepared as new.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            self._insert_mappings(db_path, [("inactive", "codex:chats", "Inactive", 500, 700, 1.0, "gpt_chat", "inactive")])
            _ = transition_gpt_lifecycle(db_path, "inactive", GptLifecycleOperation.BEGIN_REACTIVATION)
            self.assertEqual(journal.load_gpt_creation_protections(db_path).unfinished, ())
            with self.assertRaises(journal.GptCreationAmbiguityError):
                _ = journal.prepare_gpt_creation(db_path, self._intent("inactive"))

        # Given/When/Then: a grandfathered over-five store refuses a new owner unchanged.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            self._insert_mappings(db_path, [(f"legacy-{i}", "codex:chats", "Legacy", 500, 700 + i, 1.0, "gpt_chat", "active") for i in range(6)])
            before = self._snapshot(db_path)
            with self.assertRaises(GptCapacityExceededError) as raised:
                _ = journal.prepare_gpt_creation(db_path, self._intent("absent"))
            self.assertEqual((raised.exception.used_slots, raised.exception.requested_increase), (6, 1))
            self.assertEqual(self._snapshot(db_path), before)

        # Given/When/Then: deterministic nonce collision and duplicate operation are typed/no-op.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir, patch("codex_discord_gpt_creation_journal.secrets.token_hex", return_value="a" * 32):
            db_path = self._db_path(temp_dir)
            first = journal.prepare_gpt_creation(db_path, self._intent("first"))
            before = self._snapshot(db_path)
            for owner, conflict in (("second", "nonce_collision"), ("first", "existing_operation")):
                with self.subTest(conflict=conflict), self.assertRaises(journal.GptCreationAmbiguityError) as raised:
                    _ = journal.prepare_gpt_creation(db_path, self._intent(owner))
                self.assertEqual(raised.exception.conflict, conflict)
                self.assertEqual(self._snapshot(db_path), before)
            self.assertEqual(first.nonce, journal.GptCreationNonce("a" * 32))

    def test_status_progression_and_unfinished_protection_projections(self) -> None:
        # Given: one identified/update-path operation and one prepared operation.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            alpha = journal.prepare_gpt_creation(db_path, self._intent("alpha"))
            beta = journal.prepare_gpt_creation(db_path, self._intent("beta"))
            with self.assertRaises(journal.GptCreationAmbiguityError):
                _ = journal.handoff_gpt_creation(db_path, alpha, DiscordThreadId(700))
            started = journal.mark_gpt_creation_started(db_path, alpha)
            retry_snapshot = self._snapshot(db_path)
            self.assertEqual(journal.mark_gpt_creation_started(db_path, started), started)
            self.assertEqual(self._snapshot(db_path), retry_snapshot)
            self._insert_mappings(db_path, [("alpha", "codex:chats", started.thread_title, 500, 700, 1.0, "gpt_chat", "reactivating")])
            identified = journal.handoff_gpt_creation(db_path, started, DiscordThreadId(700))

            # When/Then: nullable rows, exact markers, and non-null blockers are projected.
            protections = journal.load_gpt_creation_protections(db_path)
            self.assertEqual(protections.unfinished, (identified, beta))
            self.assertEqual(protections.marker_tokens, frozenset((identified.marker_token, beta.marker_token)))
            self.assertEqual(protections.nullable_discord_thread_ids, (DiscordThreadId(700), None))
            self.assertEqual(protections.discord_thread_ids, frozenset((DiscordThreadId(700),)))
            self.assertEqual(self._snapshot(db_path)[0][0][5], identified.updated_at)

    def test_explicit_completion_cancellation_and_mismatch_are_atomic(self) -> None:
        # Given/When/Then: mismatched, repeated, and premature mutations are typed/no-op.
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            cancelled = journal.prepare_gpt_creation(db_path, self._intent("cancelled"))
            before = self._snapshot(db_path)
            with self.assertRaises(journal.GptCreationMutationError):
                journal.cancel_gpt_creation(db_path, replace(cancelled, nonce=journal.GptCreationNonce("f" * 32)))
            self.assertEqual(self._snapshot(db_path), before)
            journal.cancel_gpt_creation(db_path, cancelled)
            with self.assertRaises(journal.GptCreationMutationError):
                journal.cancel_gpt_creation(db_path, cancelled)

            identified = self._identified(db_path, "completed")
            before_final = self._snapshot(db_path)
            with self.assertRaises(journal.GptCreationMutationError):
                journal.complete_gpt_creation(db_path, identified)
            self.assertEqual(self._snapshot(db_path), before_final)
            _ = transition_gpt_lifecycle(db_path, "completed", GptLifecycleOperation.FINALIZE_REACTIVATION)
            journal.complete_gpt_creation(db_path, identified)
            mappings, operations = self._snapshot(db_path)
            self.assertEqual((mappings[0][7], operations), ("active", []))
            with self.assertRaises(journal.GptCreationMutationError):
                journal.complete_gpt_creation(db_path, identified)

        # Given/When/Then: active mapping title/parent corruption blocks deletion unchanged.
        for label, corruption in (("title", "thread_title='Corrupt'"), ("parent", "discord_channel_id=999")):
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
                db_path = self._db_path(temp_dir)
                identified = self._identified(db_path, label)
                _ = transition_gpt_lifecycle(db_path, label, GptLifecycleOperation.FINALIZE_REACTIVATION)
                self._execute(db_path, f"UPDATE mirror_threads SET {corruption};")
                before = self._snapshot(db_path)
                with self.assertRaises(journal.GptCreationMutationError):
                    journal.complete_gpt_creation(db_path, identified)
                self.assertEqual(self._snapshot(db_path), before)


if __name__ == "__main__":
    _ = unittest.main()
