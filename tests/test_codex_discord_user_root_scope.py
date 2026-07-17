from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import codex_discord_gpt_registration_store as gpt_registration_store
import codex_discord_user_root_scope as user_root_scope
from codex_thread_models import ThreadInfo


class UserRootScopeTests(unittest.TestCase):
    def test_scope_keeps_gpt_chat_created_state_roots(self) -> None:
        roots = [
            *[_thread(f"ordinary-{index}") for index in range(21)],
            _thread("gpt-active"),
            _thread("gpt-inactive"),
            _thread("gpt-third"),
        ]

        scoped = user_root_scope.load_ordinary_user_root_threads(
            lambda _limit: roots,
            db_path=Path("unused.sqlite"),
            load_ids=lambda _path: frozenset(
                {"gpt-active", "gpt-inactive", "gpt-third"}
            ),
        )

        self.assertEqual(len(roots), 24)
        self.assertEqual(len(scoped), 24)
        self.assertEqual(scoped, roots)

    def test_limit_is_applied_without_excluding_gpt_chat_created_roots(self) -> None:
        observed_limits: list[int] = []

        def load_roots(limit: int) -> list[ThreadInfo]:
            observed_limits.append(limit)
            return [_thread("gpt"), _thread("ordinary-1"), _thread("ordinary-2")]

        scoped = user_root_scope.load_ordinary_user_root_threads(
            load_roots,
            db_path=Path("unused.sqlite"),
            limit=1,
            load_ids=lambda _path: frozenset({"gpt"}),
        )

        self.assertEqual(observed_limits, [0])
        self.assertEqual([thread.id for thread in scoped], ["gpt"])

    def test_registration_store_reads_managed_gpt_rows_in_every_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "CREATE TABLE mirror_threads ("
                    "codex_thread_id TEXT PRIMARY KEY, project_key TEXT NOT NULL, "
                    "managed_by TEXT NOT NULL, lifecycle_state TEXT NOT NULL)"
                )
                _ = conn.executemany(
                    "INSERT INTO mirror_threads VALUES (?, ?, ?, ?)",
                    [
                        ("gpt-active", "codex:chats", "gpt_chat", "active"),
                        ("gpt-inactive", "codex:chats", "gpt_chat", "inactive"),
                        ("ordinary", "project", "ordinary", "active"),
                    ],
                )

            registered = gpt_registration_store.load_gpt_registered_thread_ids_read_only(
                db_path
            )

        self.assertEqual(registered, frozenset({"gpt-active", "gpt-inactive"}))

    def test_registration_store_does_not_guess_gpt_owner_on_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "CREATE TABLE mirror_threads ("
                    "codex_thread_id TEXT PRIMARY KEY, project_key TEXT NOT NULL)"
                )
                _ = conn.executemany(
                    "INSERT INTO mirror_threads VALUES (?, ?)",
                    [("legacy-gpt", "codex:chats"), ("ordinary", "project")],
                )

            registered = gpt_registration_store.load_gpt_registered_thread_ids_read_only(
                db_path
            )

        self.assertEqual(registered, frozenset())


def _thread(thread_id: str) -> ThreadInfo:
    return ThreadInfo(
        id=thread_id,
        title=thread_id,
        cwd=r"C:\repo",
        updated_at=1,
        rollout_path=f"{thread_id}.jsonl",
        model="gpt",
        reasoning_effort="high",
        tokens_used=0,
    )


if __name__ == "__main__":
    _ = unittest.main()
