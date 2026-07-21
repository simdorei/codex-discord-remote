from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import codex_discord_store as store
from codex_discord_store_queue import QueueJobState


class QueueStoreTests(unittest.TestCase):
    def test_queue_job_survives_reopen_and_duplicate_discord_message_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            first = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=999,
                prompt="first request",
                queued=True,
                ack_sent=True,
                created_at=10.0,
            )
            duplicate = store.enqueue_queue_job(
                db_path,
                job_id="job-duplicate",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=999,
                prompt="first request",
                queued=True,
                ack_sent=True,
                created_at=11.0,
            )

            records = store.list_queue_jobs(db_path)

        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.job.job_id, "job-1")
        self.assertEqual([record.job_id for record in records], ["job-1"])
        self.assertIs(records[0].state, QueueJobState.PENDING)

    def test_attempt_turn_and_flush_state_are_durable_and_target_scoped(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for index, target in enumerate(("thread-1", "thread-1", "thread-2"), start=1):
                _ = store.enqueue_queue_job(
                    db_path,
                    job_id=f"job-{index}",
                    target_thread_id=target,
                    channel_id=200 + index,
                    owner_user_id=7,
                    discord_message_id=900 + index,
                    prompt=f"request {index}",
                    queued=True,
                    ack_sent=True,
                    created_at=float(index),
                )
            started = store.begin_queue_job_attempt(
                db_path,
                "job-1",
                baseline_turn_ids=("turn-old",),
            )
            running = store.mark_queue_job_running(db_path, "job-1", "turn-new")

            deleted = store.flush_queue_jobs(db_path, "thread-1")
            remaining = store.list_queue_jobs(db_path)

        self.assertEqual(started.attempt_count, 1)
        self.assertIs(started.state, QueueJobState.STARTING)
        self.assertEqual(started.baseline_turn_ids, ("turn-old",))
        self.assertIs(running.state, QueueJobState.RUNNING)
        self.assertEqual(running.turn_id, "turn-new")
        self.assertEqual([record.job_id for record in deleted], ["job-1", "job-2"])
        self.assertEqual([record.job_id for record in remaining], ["job-3"])


if __name__ == "__main__":
    _ = unittest.main()
