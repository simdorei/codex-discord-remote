from __future__ import annotations

import gc
import json
import sqlite3
import tempfile
import unittest
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path

import codex_desktop_bridge_session_tail as session_tail
import codex_discord_gpt_cursor as gpt_cursor
import codex_discord_store as discord_store
from codex_bridge_state import JsonObject
from codex_discord_gpt_ownership import CodexThreadId


def _line(text: str, ending: bytes = b"\n") -> bytes:
    return json.dumps({"text": text}, ensure_ascii=False).encode("utf-8") + ending


def _request(db_path: Path, rollout_path: Path) -> gpt_cursor.GptCursorRequest:
    return gpt_cursor.GptCursorRequest(db_path, CodexThreadId("cursor-thread"), rollout_path)


def _read(snapshot: session_tail.SessionEventStream, offset: int, limit: int) -> tuple[list[JsonObject], int]:
    return session_tail.read_session_snapshot_events(snapshot, offset, max_events=limit)


def _deps(batch_size: int = 2) -> gpt_cursor.GptCursorDeps:
    return gpt_cursor.GptCursorDeps(_read, discord_store.update_session_mirror_cursor, batch_size)


@contextmanager
def _temp_root() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory(prefix="app-gpt-discord-sync-todo-10-") as temp_dir:
        try:
            yield Path(temp_dir)
        finally:
            _ = gc.collect()


def _stored_boundary(db_path: Path) -> tuple[str, int]:
    stored = discord_store.get_session_mirror_offset(db_path, "cursor-thread")
    if stored is None:
        raise AssertionError("cursor boundary was not persisted")
    return stored[:2]


class GptCursorTests(unittest.TestCase):
    def test_last_complete_record_boundary_handles_multibyte_and_batches(self) -> None:
        with _temp_root() as root:
            db_path, rollout_path = root / "cursor.sqlite", root / "rollout.jsonl"
            data = _line("one", b"\r\n") + _line("\ud55c\uae00") + _line("three") + _line("four", b"\r\n") + _line("five")
            _ = rollout_path.write_bytes(data)
            ordinary_events, ordinary_offset = session_tail.read_new_session_events(rollout_path, 0, max_events=2)
            calls: list[tuple[int, int]] = []

            def read(snapshot: session_tail.SessionEventStream, offset: int, limit: int) -> tuple[list[JsonObject], int]:
                calls.append((offset, limit))
                return _read(snapshot, offset, limit)

            deps = gpt_cursor.GptCursorDeps(read, discord_store.update_session_mirror_cursor, 2)
            boundary = gpt_cursor.establish_reactivation_cursor(_request(db_path, rollout_path), deps=deps)

            self.assertEqual(boundary, (str(rollout_path), len(data)))
            self.assertEqual(_stored_boundary(db_path), boundary)
            self.assertEqual((len(ordinary_events), calls[1][0]), (2, ordinary_offset))
            self.assertGreaterEqual(len(calls), 3)
            self.assertTrue(all(limit == 2 for _, limit in calls))

    def test_partial_truncation_rotation_and_append_during_scan(self) -> None:
        with _temp_root() as root:
            db_path, rollout_path = root / "cursor.sqlite", root / "same.jsonl"
            discord_store.update_session_mirror_cursor(db_path, "cursor-thread", str(rollout_path), 999)

            complete = _line("kept")
            _ = rollout_path.write_bytes(complete + b'{"text":"partial"')
            truncated = gpt_cursor.establish_reactivation_cursor(_request(db_path, rollout_path), deps=_deps())
            self.assertEqual(truncated, (str(rollout_path), len(complete)))

            before_append = _line("one") + _line("two")
            _ = rollout_path.write_bytes(before_append)
            appended = False

            def append_after_chunk(source_path: Path, copied_bytes: int) -> None:
                nonlocal appended
                if not appended:
                    self.assertEqual(copied_bytes, 8)
                    with source_path.open("ab") as handle:
                        _ = handle.write(_line("post-reactivation"))
                    appended = True
                self.assertGreater(copied_bytes, 0)

            append_deps = gpt_cursor.GptCursorDeps(_read, discord_store.update_session_mirror_cursor, 1, 8, append_after_chunk)
            appended_boundary = gpt_cursor.establish_reactivation_cursor(_request(db_path, rollout_path), deps=append_deps)
            self.assertEqual(appended_boundary, (str(rollout_path), len(before_append)))

            rotated_path = root / "rotated.jsonl"
            rotated_complete = _line("new")
            _ = rotated_path.write_bytes(rotated_complete + b'{"text":"' + "\ud55c".encode("utf-8")[:2])
            rotated = gpt_cursor.establish_reactivation_cursor(_request(db_path, rotated_path), deps=_deps())
            self.assertEqual(rotated, (str(rotated_path), len(rotated_complete)))
            self.assertEqual(_stored_boundary(db_path), rotated)

    def test_same_path_replacement_during_copy_is_typed_and_non_mutating(self) -> None:
        with _temp_root() as root:
            db_path, rollout_path = root / "cursor.sqlite", root / "rollout.jsonl"
            replacement_path = root / "replacement.jsonl"
            _ = rollout_path.write_bytes(_line("old") * 4)
            replacement_bytes = _line("replacement")
            _ = replacement_path.write_bytes(replacement_bytes)
            discord_store.update_session_mirror_cursor(db_path, "cursor-thread", "stable-rollout", 23)
            replaced = False

            def replace_after_chunk(source_path: Path, copied_bytes: int) -> None:
                nonlocal replaced
                if not replaced:
                    self.assertEqual(copied_bytes, 8)
                    source_path.unlink()
                    _ = replacement_path.rename(source_path)
                    replaced = True
                self.assertGreater(copied_bytes, 0)

            deps = gpt_cursor.GptCursorDeps(_read, discord_store.update_session_mirror_cursor, 2, 8, replace_after_chunk)
            with self.assertRaises(gpt_cursor.GptCursorSourceError) as raised:
                _ = gpt_cursor.establish_reactivation_cursor(_request(db_path, rollout_path), deps=deps)

            self.assertEqual(raised.exception.failure, gpt_cursor.GptCursorSourceFailure.CHANGED)
            self.assertNotIn(rollout_path.name, str(raised.exception))
            self.assertEqual(_stored_boundary(db_path), ("stable-rollout", 23))
            self.assertEqual(rollout_path.read_bytes(), replacement_bytes)

    def test_eof_before_initial_extent_is_typed_and_non_mutating(self) -> None:
        with _temp_root() as root:
            db_path, rollout_path = root / "cursor.sqlite", root / "rollout.jsonl"
            _ = rollout_path.write_bytes(_line("truncate") * 4)
            discord_store.update_session_mirror_cursor(db_path, "cursor-thread", "stable-rollout", 31)
            truncated = False

            def truncate_after_chunk(source_path: Path, copied_bytes: int) -> None:
                nonlocal truncated
                if not truncated:
                    self.assertEqual(copied_bytes, 8)
                    with source_path.open("wb"):
                        pass
                    truncated = True

            deps = gpt_cursor.GptCursorDeps(_read, discord_store.update_session_mirror_cursor, 2, 8, truncate_after_chunk)
            with self.assertRaises(gpt_cursor.GptCursorSourceError) as raised:
                _ = gpt_cursor.establish_reactivation_cursor(_request(db_path, rollout_path), deps=deps)

            self.assertEqual(raised.exception.failure, gpt_cursor.GptCursorSourceFailure.CHANGED)
            self.assertEqual(_stored_boundary(db_path), ("stable-rollout", 31))

    def test_empty_file_persists_zero_boundary(self) -> None:
        with _temp_root() as root:
            db_path, rollout_path = root / "cursor.sqlite", root / "empty.jsonl"
            rollout_path.touch()

            boundary = gpt_cursor.establish_reactivation_cursor(_request(db_path, rollout_path), deps=_deps())

            self.assertEqual(boundary, (str(rollout_path), 0))
            self.assertEqual(_stored_boundary(db_path), boundary)

    def test_source_failures_are_typed_public_safe_and_do_not_persist(self) -> None:
        with _temp_root() as root:
            directory = root / "directory"
            directory.mkdir()
            malformed, invalid_utf8 = root / "malformed.jsonl", root / "invalid.jsonl"
            _ = malformed.write_bytes(_line("valid") + b"{bad}\n")
            _ = invalid_utf8.write_bytes(_line("valid") + b"\xff\n")
            updates: list[tuple[Path, str, str, int]] = []

            def persist(db_path: Path, thread_id: str, rollout_path: str, offset: int) -> None:
                updates.append((db_path, thread_id, rollout_path, offset))

            deps = gpt_cursor.GptCursorDeps(_read, persist, 2)
            cases = (
                (root / "missing.jsonl", gpt_cursor.GptCursorSourceFailure.MISSING),
                (directory, gpt_cursor.GptCursorSourceFailure.UNREADABLE),
                (malformed, gpt_cursor.GptCursorSourceFailure.INVALID),
                (invalid_utf8, gpt_cursor.GptCursorSourceFailure.INVALID),
            )
            for source, failure in cases:
                with self.subTest(failure=failure):
                    with self.assertRaises(gpt_cursor.GptCursorSourceError) as raised:
                        _ = gpt_cursor.establish_reactivation_cursor(_request(root / "cursor.sqlite", source), deps=deps)
                    self.assertEqual(raised.exception.failure, failure)
                    self.assertNotIn(source.name, str(raised.exception))
            self.assertEqual(updates, [])

    def test_persistence_failure_rolls_back_and_releases_connection(self) -> None:
        with _temp_root() as root:
            db_path, rollout_path = root / "cursor.sqlite", root / "rollout.jsonl"
            _ = rollout_path.write_bytes(_line("complete"))
            discord_store.update_session_mirror_cursor(db_path, "cursor-thread", "old-rollout", 17)
            with closing(sqlite3.connect(db_path)) as conn:
                with conn, closing(conn.execute("CREATE TRIGGER reject_cursor BEFORE INSERT ON codex_session_mirror_offsets BEGIN SELECT RAISE(ABORT, 'rejected'); END")):
                    pass

            with self.assertRaises(gpt_cursor.GptCursorPersistenceError) as raised:
                _ = gpt_cursor.establish_reactivation_cursor(_request(db_path, rollout_path))

            self.assertEqual(str(raised.exception), "The GPT reactivation cursor could not be saved.")
            self.assertEqual(_stored_boundary(db_path), ("old-rollout", 17))


if __name__ == "__main__":
    _ = unittest.main()
