from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

import codex_discord_gpt_snapshots as snapshots


class GptSnapshotTests(unittest.TestCase):
    def test_independent_reusable_snapshots(self) -> None:
        now = [100.0]
        store = snapshots.GptSnapshotStore(monotonic=lambda: now[0])
        key = snapshots.GptSnapshotKey(
            guild_id=1,
            configured_general_channel_id=2,
            user_id=3,
        )

        first_list = store.replace(key, snapshots.GptSnapshotKind.LIST, ("list-a", "list-b"))
        synced = store.replace(key, snapshots.GptSnapshotKind.SYNCED, ("synced-a",))
        replacement_list = store.replace(key, snapshots.GptSnapshotKind.LIST, ("list-c", "list-d"))

        self.assertEqual(first_list.codex_thread_ids, ("list-a", "list-b"))
        self.assertEqual(replacement_list.codex_thread_ids, ("list-c", "list-d"))
        self.assertIs(store.get(key, snapshots.GptSnapshotKind.SYNCED), synced)
        self.assertEqual(
            store.select(key, snapshots.GptSnapshotKind.LIST, " 2, 1, 2 "),
            ("list-d", "list-c"),
        )
        self.assertEqual(
            store.select(key, snapshots.GptSnapshotKind.LIST, "1"),
            ("list-c",),
        )
        with self.assertRaises(FrozenInstanceError):
            setattr(replacement_list, "saved_at", 999.0)

    def test_expired_or_invalid_selection_is_atomic(self) -> None:
        now = [25.0]
        store = snapshots.GptSnapshotStore(monotonic=lambda: now[0])
        key = snapshots.GptSnapshotKey(
            guild_id=10,
            configured_general_channel_id=20,
            user_id=30,
        )
        _ = store.replace(key, snapshots.GptSnapshotKind.LIST, ("one", "two", "three"))

        invalid_values = (None, "", "0", "-1", "x", "1,,2", "4", "1, 0", "1,4", "1.0", "+1")
        for raw in invalid_values:
            with self.subTest(raw=raw):
                with self.assertRaises(snapshots.GptSnapshotSelectionError):
                    _ = store.select(key, snapshots.GptSnapshotKind.LIST, raw)

        self.assertEqual(
            store.select(key, snapshots.GptSnapshotKind.LIST, "3,1,3,2"),
            ("three", "one", "two"),
        )
        self.assertEqual(
            store.get(key, snapshots.GptSnapshotKind.LIST).codex_thread_ids,
            ("one", "two", "three"),
        )

        now[0] = 625.0
        with self.assertRaises(snapshots.GptSnapshotExpiredError):
            _ = store.get(key, snapshots.GptSnapshotKind.LIST)
        with self.assertRaises(snapshots.GptSnapshotMissingError):
            _ = store.get(key, snapshots.GptSnapshotKind.SYNCED)

    def test_oversized_decimal_is_typed_and_non_mutating(self) -> None:
        store = snapshots.GptSnapshotStore(monotonic=lambda: 10.0)
        key = snapshots.GptSnapshotKey(
            guild_id=100,
            configured_general_channel_id=200,
            user_id=300,
        )
        original = store.replace(key, snapshots.GptSnapshotKind.LIST, ("only",))
        oversized = "9" * 5000

        with self.assertRaises(snapshots.GptSnapshotSelectionError):
            _ = snapshots.parse_csv_indices(oversized)
        with self.assertRaises(snapshots.GptSnapshotSelectionError):
            _ = store.select(key, snapshots.GptSnapshotKind.LIST, oversized)

        self.assertIs(store.get(key, snapshots.GptSnapshotKind.LIST), original)


if __name__ == "__main__":
    _ = unittest.main()
