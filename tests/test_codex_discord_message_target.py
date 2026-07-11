from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import codex_discord_message_target as message_target
import codex_discord_store as store


def todo08_temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(
        prefix="app-gpt-discord-sync-todo-08-", ignore_cleanup_errors=True
    )


class DiscordMessageTargetTests(unittest.TestCase):
    def test_unknown_channel_without_parent_preserves_selected_fallback(self) -> None:
        resolved = message_target.resolve_discord_message_target(
            lambda _channel_id: None,
            999,
            None,
        )

        self.assertIsNone(resolved.target_thread_id)
        self.assertEqual(resolved.target_source, "selected")
        self.assertFalse(resolved.persist_mirror_channel)

    def test_resolve_discord_message_target_prefers_direct_thread_mapping(self) -> None:
        with todo08_temp_dir() as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.upsert_mirror_thread(
                db_path, "parent-thread", "project", "Parent", 100, 900, now=1.0
            )
            store.upsert_mirror_thread(
                db_path, "direct-thread", "project", "Direct", 100, 901, now=2.0
            )

            resolved = message_target.resolve_discord_message_target(
                lambda channel_id: store.get_mirrored_codex_thread_id(
                    db_path, channel_id
                ),
                901,
                100,
            )

        self.assertEqual(resolved.target_thread_id, "direct-thread")
        self.assertEqual(resolved.target_source, "mirror")
        self.assertTrue(resolved.persist_mirror_channel)

    def test_discord_thread_target_args_prefer_mapped_thread(self) -> None:
        with todo08_temp_dir() as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.upsert_mirror_thread(
                db_path, "thread-1", "project", "title", 111, 222, now=1.0
            )
            target = store.get_mirrored_codex_thread_id(db_path, 222)

        self.assertEqual(
            [] if target is None else ["--thread-id", target],
            ["--thread-id", "thread-1"],
        )

    def test_resolve_discord_message_target_uses_parent_channel_fallback(self) -> None:
        with todo08_temp_dir() as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.upsert_mirror_thread(
                db_path, "parent-thread", "project", "Parent", 100, 900, now=1.0
            )

            resolved = message_target.resolve_discord_message_target(
                lambda channel_id: store.get_mirrored_codex_thread_id(
                    db_path, channel_id
                ),
                999,
                100,
            )

        self.assertEqual(resolved.target_thread_id, "parent-thread")
        self.assertEqual(resolved.target_source, "mirror")
        self.assertFalse(resolved.persist_mirror_channel)

    def test_project_text_channel_does_not_replace_mirror_thread_mapping(self) -> None:
        with todo08_temp_dir() as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.upsert_mirror_thread(
                db_path, "thread-1", "project", "Thread", 100, 900, now=1.0
            )

            resolved = message_target.resolve_discord_message_target(
                lambda channel_id: store.get_mirrored_codex_thread_id(
                    db_path, channel_id
                ),
                100,
                None,
            )

        self.assertEqual(resolved.target_thread_id, "thread-1")
        self.assertEqual(resolved.target_source, "mirror")
        self.assertFalse(resolved.persist_mirror_channel)

    def test_selected_target_becomes_explicit_only_for_bot_bridge_mentions(
        self,
    ) -> None:
        with todo08_temp_dir() as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            selected = message_target.resolve_discord_message_target(
                lambda channel_id: store.get_mirrored_codex_thread_id(
                    db_path, channel_id
                ),
                999,
                None,
            )

        content = "Continue route Work thread: 019eeaac-6170-7133-86ac-bef0f1c6e865"

        explicit = selected.with_explicit_target(content, bot_bridge_mention=True)
        unmentioned = selected.with_explicit_target(content, bot_bridge_mention=False)

        self.assertEqual(
            explicit.target_thread_id, "019eeaac-6170-7133-86ac-bef0f1c6e865"
        )
        self.assertEqual(explicit.target_source, "explicit")
        self.assertIsNone(unmentioned.target_thread_id)
        self.assertEqual(unmentioned.target_source, "selected")

    def test_selected_target_becomes_explicit_for_codex_session_label(self) -> None:
        selected = message_target.DiscordMessageTarget(None, "selected")
        content = (
            "Route/target:\n"
            "- codex/session: `019ef4b8-c325-7a70-8781-bdcc5b21a653`\n"
            "- selected/extension routing forbidden"
        )

        explicit = selected.with_explicit_target(content, bot_bridge_mention=True)

        self.assertEqual(
            explicit.target_thread_id, "019ef4b8-c325-7a70-8781-bdcc5b21a653"
        )
        self.assertEqual(explicit.target_source, "explicit")


if __name__ == "__main__":
    _ = unittest.main()
