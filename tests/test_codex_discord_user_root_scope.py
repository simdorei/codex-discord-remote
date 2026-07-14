from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import codex_discord_gpt_registration_store as gpt_registration_store
import codex_discord_user_root_scope as user_root_scope
from codex_discord_store_schema import init_store_schema
from codex_thread_models import ThreadInfo


class UserRootScopeTests(unittest.TestCase):
    def test_scope_is_root_ids_minus_all_gpt_mapping_ids(self) -> None:
        roots = [
            _thread("unregistered-projectless", r"C:\Documents\Codex\2026-07-14\new-chat"),
            _thread("gpt-active", r"C:\repo"),
            _thread("ordinary", r"C:\repo"),
            _thread("gpt-inactive", r"C:\repo"),
        ]

        scoped = user_root_scope.load_ordinary_user_root_threads(
            lambda _limit: roots,
            db_path=Path("unused.sqlite"),
            load_ids=lambda _path: frozenset({"gpt-active", "gpt-inactive"}),
        )

        self.assertEqual(
            [thread.id for thread in scoped],
            ["unregistered-projectless", "ordinary"],
        )

    def test_limit_is_applied_after_gpt_ids_are_removed(self) -> None:
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
        self.assertEqual([thread.id for thread in scoped], ["ordinary-1"])

    def test_registration_store_reads_all_gpt_lifecycle_states_only(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            with sqlite3.connect(db_path) as conn:
                init_store_schema(conn)
                _ = conn.executemany(
                    "INSERT INTO mirror_threads ("
                    "codex_thread_id, project_key, thread_title, "
                    "discord_channel_id, discord_thread_id, updated_at, "
                    "managed_by, lifecycle_state"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("gpt-active", "codex:chats", "active", 1, 11, 1.0, "gpt_chat", "active"),
                        ("gpt-inactive", "codex:chats", "inactive", 1, 12, 1.0, "gpt_chat", "inactive"),
                        ("ordinary", "project", "ordinary", 2, 13, 1.0, "ordinary", "active"),
                    ],
                )

            registered = gpt_registration_store.load_gpt_registered_thread_ids_read_only(
                db_path
            )

        self.assertEqual(registered, frozenset({"gpt-active", "gpt-inactive"}))


def _thread(thread_id: str, cwd: str = r"C:\repo") -> ThreadInfo:
    return ThreadInfo(
        id=thread_id,
        title=thread_id,
        cwd=cwd,
        updated_at=1,
        rollout_path=f"{thread_id}.jsonl",
        model="gpt",
        reasoning_effort="high",
        tokens_used=0,
    )


if __name__ == "__main__":
    _ = unittest.main()
