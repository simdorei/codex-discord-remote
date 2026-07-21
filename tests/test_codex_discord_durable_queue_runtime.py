from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import tempfile
from dataclasses import dataclass
from typing import cast
import unittest

from codex_app_server_transport_turn_outcomes import (
    TurnCompletion,
    TurnCompletionPending,
    TurnStatus,
)
from codex_discord_durable_queue_runtime import (
    DurableQueueRuntime,
    DurableQueueRuntimeDeps,
)
import codex_discord_prompt_delivery_prepare as prompt_delivery_prepare
import codex_discord_prompt_mapped_delivery as prompt_mapped_delivery
from codex_discord_queue_job_memory import to_memory_queue_job
from codex_discord_runner_queue import QueueJobValue
import codex_discord_store as store


@dataclass(frozen=True, slots=True)
class FakeQueueChannel:
    id: int

    async def send(self, _text: str) -> None:
        return None


class DurableQueueRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_starting_job_adopts_only_new_turn_after_restart_without_resending(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1")
            record = store.begin_queue_job_attempt(
                db_path,
                record.job_id,
                baseline_turn_ids=("turn-old",),
            )
            sent_prompts: list[str] = []
            runtime = self._runtime(
                db_path,
                states={
                    "turn-old": self._turn("turn-old", TurnStatus.COMPLETED),
                    "turn-new": self._turn("turn-new", TurnStatus.IN_PROGRESS),
                },
                sent_prompts=sent_prompts,
            )
            channel = FakeQueueChannel(id=222)
            job = to_memory_queue_job(record, cast(QueueJobValue, channel), None)

            attempt = await runtime.acquire_turn(
                job,
                record.prompt,
                record.target_thread_id,
                recovery=False,
            )

            persisted = store.list_queue_jobs(db_path)[0]
            self.assertEqual(attempt.attempt_number, 1)
            self.assertEqual(attempt.turn_id, "turn-new")
            self.assertEqual(sent_prompts, [])
            self.assertEqual(persisted.turn_id, "turn-new")

    async def test_starting_job_with_no_new_turn_sends_same_prepared_attempt_once(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1")
            record = store.begin_queue_job_attempt(
                db_path,
                record.job_id,
                baseline_turn_ids=("turn-old",),
            )
            sent_prompts: list[str] = []
            runtime = self._runtime(
                db_path,
                states={"turn-old": self._turn("turn-old", TurnStatus.COMPLETED)},
                sent_prompts=sent_prompts,
            )
            channel = FakeQueueChannel(id=222)
            job = to_memory_queue_job(record, cast(QueueJobValue, channel), None)

            attempt = await runtime.acquire_turn(
                job,
                record.prompt,
                record.target_thread_id,
                recovery=False,
            )

            persisted = store.list_queue_jobs(db_path)[0]
            self.assertEqual(attempt.attempt_number, 1)
            self.assertEqual(sent_prompts, ["request"])
            self.assertEqual(persisted.attempt_count, 1)
            self.assertEqual(persisted.turn_id, "turn-sent")

    async def test_pending_job_waits_for_existing_in_progress_turn_before_sending(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1")
            sent_prompts: list[str] = []
            state_reads = 0

            def get_states(_thread_id: str) -> dict[str, TurnCompletion]:
                nonlocal state_reads
                state_reads += 1
                status = TurnStatus.IN_PROGRESS if state_reads == 1 else TurnStatus.COMPLETED
                return {"turn-existing": self._turn("turn-existing", status)}

            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=sent_prompts,
                get_states=get_states,
            )
            channel = FakeQueueChannel(id=222)
            job = to_memory_queue_job(record, cast(QueueJobValue, channel), None)

            attempt = await runtime.acquire_turn(
                job,
                record.prompt,
                record.target_thread_id,
                recovery=False,
            )

            self.assertEqual(state_reads, 2)
            self.assertEqual(sent_prompts, ["request"])
            self.assertEqual(attempt.turn_id, "turn-sent")

    def _runtime(
        self,
        db_path: Path,
        *,
        states: dict[str, TurnCompletion],
        sent_prompts: list[str],
        get_states: Callable[[str], dict[str, TurnCompletion]] | None = None,
    ) -> DurableQueueRuntime:
        async def run_prompt(
            channel: QueueJobValue,
            prompt: str,
            **_kwargs: QueueJobValue,
        ) -> prompt_delivery_prepare.PromptDeliveryPreparationResult:
            _ = channel
            sent_prompts.append(prompt)
            return prompt_delivery_prepare.PromptDeliveryPreparationResult(
                handled=True,
                target_thread_id="thread-1",
                target_ref="thread-1",
                recent_offsets={},
                delegate_to_session_mirror=False,
                mapped_result=prompt_mapped_delivery.MappedPromptDeliveryResult(
                    handled=True,
                    accepted=True,
                    turn_id="turn-sent",
                ),
            )

        async def send_chunks(
            target: QueueJobValue,
            text: str,
            **_kwargs: QueueJobValue,
        ) -> int:
            _ = target, text
            return 1

        return DurableQueueRuntime(
            DurableQueueRuntimeDeps(
                get_db_path=lambda: db_path,
                get_turn_states=get_states or (lambda _thread_id: states),
                wait_for_live_turn=lambda _thread_id, _turn_id, _timeout: TurnCompletionPending(),
                run_prompt_and_send=run_prompt,
                send_chunks=send_chunks,
                log=lambda _message: None,
            )
        )

    @staticmethod
    def _enqueue(db_path: Path, job_id: str):
        return store.enqueue_queue_job(
            db_path,
            job_id=job_id,
            target_thread_id="thread-1",
            channel_id=222,
            owner_user_id=7,
            discord_message_id=999,
            prompt="request",
            queued=True,
            ack_sent=True,
            created_at=1.0,
        ).job

    @staticmethod
    def _turn(turn_id: str, status: TurnStatus) -> TurnCompletion:
        return TurnCompletion("thread-1", turn_id, status)


if __name__ == "__main__":
    _ = unittest.main()
