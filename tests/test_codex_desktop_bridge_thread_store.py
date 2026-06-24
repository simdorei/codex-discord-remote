from __future__ import annotations

import unittest
from unittest.mock import patch

import codex_desktop_bridge_state as bridge_state
import codex_desktop_bridge_thread_store as thread_store
from codex_thread_models import ThreadInfo


class ThreadStoreErrorTests(unittest.TestCase):
    def test_get_thread_by_id_raises_typed_thread_not_found(self) -> None:
        with self.assertRaises(thread_store.ThreadNotFoundError) as raised:
            _ = thread_store.get_thread_by_id("missing", threads=[])

        self.assertIsInstance(raised.exception, RuntimeError)
        self.assertEqual(str(raised.exception), "Thread not found: missing")
        self.assertEqual(raised.exception.thread_ref, "missing")

    def test_resolve_thread_ref_raises_typed_recent_errors(self) -> None:
        first = _thread("first", "C:\\repo")
        second = _thread("second", "C:\\repo")

        with patch.object(thread_store, "load_recent_threads", return_value=[]):
            with self.assertRaisesRegex(
                thread_store.NoCodexThreadsError,
                "No Codex threads found in the local state DB.",
            ):
                _ = thread_store.resolve_thread_ref("1")

        with (
            patch.object(thread_store, "load_recent_threads", return_value=[first]),
            patch.object(bridge_state, "get_selected_thread_id", return_value="first"),
        ):
            with self.assertRaisesRegex(thread_store.NoAlternateThreadError, "No alternate thread found."):
                _ = thread_store.resolve_thread_ref("other")

        with patch.object(thread_store, "load_recent_threads", return_value=[first]):
            with self.assertRaises(thread_store.ThreadIndexOutOfRangeError) as raised:
                _ = thread_store.resolve_thread_ref("2")
        self.assertEqual(str(raised.exception), "Thread index out of range: 2")
        self.assertEqual(raised.exception.thread_ref, "2")

        with patch.object(thread_store, "load_recent_threads", return_value=[first, second]):
            with self.assertRaises(thread_store.AmbiguousThreadRefError) as ambiguous:
                _ = thread_store.resolve_thread_ref("repo")
        self.assertEqual(
            str(ambiguous.exception),
            "Multiple threads match workspace `repo`. Use one of: repo:1, repo:2",
        )

    def test_resolve_archived_thread_ref_raises_typed_archived_errors(self) -> None:
        first = _thread("archived-1", "C:\\archive")
        second = _thread("archived-2", "C:\\archive")

        with patch.object(thread_store, "load_archived_threads", return_value=[]):
            with self.assertRaisesRegex(
                thread_store.NoArchivedCodexThreadsError,
                "No archived Codex threads found in the local state DB.",
            ):
                _ = thread_store.resolve_archived_thread_ref("1")

        with patch.object(thread_store, "load_archived_threads", return_value=[first]):
            with self.assertRaises(thread_store.ArchivedThreadIndexOutOfRangeError) as raised:
                _ = thread_store.resolve_archived_thread_ref("2")
        self.assertEqual(str(raised.exception), "Archived thread index out of range: 2")
        self.assertEqual(raised.exception.thread_ref, "2")

        with patch.object(thread_store, "load_archived_threads", return_value=[first, second]):
            with self.assertRaises(thread_store.AmbiguousArchivedThreadRefError) as ambiguous:
                _ = thread_store.resolve_archived_thread_ref("archive")
        self.assertEqual(
            str(ambiguous.exception),
            "Multiple archived threads match workspace `archive`. Use one of: archive:1, archive:2",
        )

    def test_choose_thread_raises_typed_errors(self) -> None:
        with patch.object(thread_store, "load_recent_threads", return_value=[]):
            with self.assertRaisesRegex(
                thread_store.NoCodexThreadsError,
                "No Codex threads found in the local state DB.",
            ):
                _ = thread_store.choose_thread(None, None)

        with patch.object(thread_store, "load_recent_threads", return_value=[_thread("thread-1", "C:\\repo")]):
            with self.assertRaises(thread_store.ThreadNotFoundError) as raised:
                _ = thread_store.choose_thread("missing", None)

        self.assertEqual(str(raised.exception), "Thread not found: missing")
        self.assertEqual(raised.exception.thread_ref, "missing")

    def test_choose_thread_finds_explicit_id_outside_recent_window(self) -> None:
        target = _thread("target", "C:\\repo")

        def load_recent_threads(limit: int = 20) -> list[ThreadInfo]:
            if limit == 50:
                return [_thread("recent", "C:\\repo")]
            if limit == 0:
                return [_thread("recent", "C:\\repo"), target]
            raise AssertionError(f"unexpected limit: {limit}")

        with patch.object(thread_store, "load_recent_threads", side_effect=load_recent_threads):
            chosen = thread_store.choose_thread("target", None)

        self.assertEqual(chosen, target)


def _thread(thread_id: str, cwd: str) -> ThreadInfo:
    return ThreadInfo(
        id=thread_id,
        title=f"Thread {thread_id}",
        cwd=cwd,
        updated_at=1,
        rollout_path=f"{thread_id}.jsonl",
        model="gpt",
        reasoning_effort="high",
        tokens_used=0,
    )


if __name__ == "__main__":
    _ = unittest.main()
