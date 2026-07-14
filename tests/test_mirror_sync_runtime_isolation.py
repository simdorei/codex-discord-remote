from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

import codex_discord_bot as bot
import codex_discord_gpt_creation_journal as creation_journal
import codex_discord_gpt_read_service as read_service
import codex_discord_gpt_runtime as gpt_runtime
import codex_discord_store as discord_store
from codex_discord_gpt_ownership import MirrorThreadOwnership
from tests.mirror_sync_bridge_types import isolated_mirror_store


class InjectedIsolationError(RuntimeError):
    pass


class MirrorSyncRuntimeIsolationTests(unittest.TestCase):
    def test_nested_store_never_reads_or_migrates_external_runtime_db(self) -> None:
        original_db_path = bot.MIRROR_DB_PATH
        original_runtime = cast(
            gpt_runtime.GptRuntime,
            getattr(bot, "GPT_RUNTIME"),
        )
        with tempfile.TemporaryDirectory(
            prefix="mirror-sync-runtime-isolation-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            external_db = Path(temp_dir) / "external.sqlite"
            isolated_db = Path(temp_dir) / "isolated.sqlite"
            discord_store.init_mirror_db(external_db)
            with sqlite3.connect(external_db) as conn:
                _ = conn.execute("PRAGMA user_version = 1")
                _ = conn.execute(
                    "INSERT INTO mirror_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "external-gpt",
                        "codex:chats",
                        "External",
                        11,
                        22,
                        1.25,
                        "ordinary",
                        "active",
                    ),
                )
                conn.commit()
            external_hash_before = hashlib.sha256(external_db.read_bytes()).digest()

            mapping_paths: list[Path] = []
            journal_paths: list[Path] = []
            load_mappings = read_service.load_gpt_mappings_read_only
            load_protections = creation_journal.load_gpt_creation_protections

            def guarded_load_mappings(
                db_path: Path,
            ) -> tuple[MirrorThreadOwnership, ...]:
                self.assertNotEqual(db_path, external_db)
                mapping_paths.append(db_path)
                return load_mappings(db_path)

            def guarded_load_protections(
                db_path: Path,
            ) -> creation_journal.GptCreationProtections:
                self.assertNotEqual(db_path, external_db)
                journal_paths.append(db_path)
                return load_protections(db_path)

            with isolated_mirror_store(external_db):
                external_runtime = cast(
                    gpt_runtime.GptRuntime,
                    getattr(bot, "GPT_RUNTIME"),
                )
                with isolated_mirror_store(isolated_db):
                    bot.init_mirror_db()
                    runtime = cast(
                        gpt_runtime.GptRuntime,
                        getattr(bot, "GPT_RUNTIME"),
                    )
                    self.assertIsNot(runtime, external_runtime)
                    with (
                        patch.object(
                            read_service,
                            "load_gpt_mappings_read_only",
                            guarded_load_mappings,
                        ),
                        patch.object(
                            creation_journal,
                            "load_gpt_creation_protections",
                            guarded_load_protections,
                        ),
                    ):
                        self.assertIsNone(runtime.mirror_reconciliation(None))
                    self.assertEqual(bot.MIRROR_DB_PATH, isolated_db)

                self.assertEqual(bot.MIRROR_DB_PATH, external_db)
                self.assertIs(
                    cast(gpt_runtime.GptRuntime, getattr(bot, "GPT_RUNTIME")),
                    external_runtime,
                )

            external_hash_after = hashlib.sha256(external_db.read_bytes()).digest()
            with sqlite3.connect(
                external_db.resolve().as_uri() + "?mode=ro",
                uri=True,
            ) as conn:
                version = cast(
                    tuple[int], conn.execute("PRAGMA user_version").fetchone()
                )[0]
                owner = cast(
                    tuple[str],
                    conn.execute(
                        "SELECT managed_by FROM mirror_threads "
                        + "WHERE codex_thread_id = 'external-gpt'"
                    ).fetchone(),
                )[0]

        self.assertEqual(external_hash_after, external_hash_before)
        self.assertEqual(version, 1)
        self.assertEqual(owner, "ordinary")
        self.assertEqual(mapping_paths, [isolated_db])
        self.assertEqual(journal_paths, [isolated_db])
        self.assertEqual(bot.MIRROR_DB_PATH, original_db_path)
        self.assertIs(
            cast(gpt_runtime.GptRuntime, getattr(bot, "GPT_RUNTIME")),
            original_runtime,
        )

    def test_setup_and_body_failures_restore_exact_globals(self) -> None:
        original_db_path = bot.MIRROR_DB_PATH
        original_runtime = cast(
            gpt_runtime.GptRuntime,
            getattr(bot, "GPT_RUNTIME"),
        )

        def fail_runtime_factory(_db_path: Path) -> gpt_runtime.GptRuntime:
            raise InjectedIsolationError("injected runtime construction failure")

        with tempfile.TemporaryDirectory(
            prefix="mirror-sync-runtime-rollback-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            temporary_db = Path(temp_dir) / "temporary.sqlite"
            with self.assertRaisesRegex(
                InjectedIsolationError,
                "construction failure",
            ):
                with isolated_mirror_store(
                    temporary_db,
                    runtime_factory=fail_runtime_factory,
                ):
                    self.fail("failed runtime construction entered the context")

            self.assertEqual(bot.MIRROR_DB_PATH, original_db_path)
            self.assertIs(
                cast(gpt_runtime.GptRuntime, getattr(bot, "GPT_RUNTIME")),
                original_runtime,
            )

            with self.assertRaisesRegex(
                InjectedIsolationError,
                "injected body failure",
            ):
                with isolated_mirror_store(temporary_db):
                    replacement_runtime = cast(
                        gpt_runtime.GptRuntime,
                        getattr(bot, "GPT_RUNTIME"),
                    )
                    self.assertIsNot(replacement_runtime, original_runtime)
                    raise InjectedIsolationError("injected body failure")

        self.assertEqual(bot.MIRROR_DB_PATH, original_db_path)
        self.assertIs(
            cast(gpt_runtime.GptRuntime, getattr(bot, "GPT_RUNTIME")),
            original_runtime,
        )


if __name__ == "__main__":
    _ = unittest.main()
