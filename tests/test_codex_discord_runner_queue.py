from __future__ import annotations

from types import SimpleNamespace
from typing import cast, override
import unittest

import codex_discord_runner_queue as runner_queue
from codex_discord_runtime import normalize_runner_key


class RunnerQueueTests(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncTearDown(self) -> None:
        async with runner_queue.THREAD_RUNNERS_LOCK:
            runner_queue.THREAD_RUNNERS.clear()

    async def test_enqueue_creates_runner_and_reports_busy(self) -> None:
        calls: list[str | None] = []

        async def fake_loop(target_thread_id: str | None) -> None:
            calls.append(target_thread_id)

        channel = SimpleNamespace(id=222)
        source_message = SimpleNamespace(author=SimpleNamespace(id=7))

        size = await runner_queue.enqueue_thread_ask(
            channel,
            "please queue",
            "thread-1",
            queued=True,
            ack_sent=True,
            source_message=source_message,
            thread_runner_loop_func=fake_loop,
        )
        runner = await runner_queue.get_thread_runner("thread-1")
        task = runner["task"]
        if task is not None:
            await task

        self.assertEqual(size, 1)
        self.assertEqual(calls, ["thread-1"])
        self.assertTrue(await runner_queue.is_thread_runner_busy("thread-1"))

    async def test_retract_removes_latest_matching_queued_job(self) -> None:
        channel = SimpleNamespace(id=222)
        other_channel = SimpleNamespace(id=333)
        owner_message = SimpleNamespace(author=SimpleNamespace(id=7))
        other_owner_message = SimpleNamespace(author=SimpleNamespace(id=9))
        runner = await runner_queue.get_thread_runner("thread-1")
        queue = runner["queue"]
        jobs = [
            {"channel": channel, "prompt": "first matching", "source_message": owner_message},
            {"channel": channel, "prompt": "other owner", "source_message": other_owner_message},
            {"channel": channel, "prompt": "latest matching", "source_message": owner_message},
            {"channel": other_channel, "prompt": "other channel", "source_message": owner_message},
        ]
        for job in jobs:
            await queue.put(job)

        result = await runner_queue.retract_thread_ask(
            "thread-1",
            channel_id=222,
            owner_user_id=7,
        )

        remaining_prompts: list[str] = []
        while not queue.empty():
            job = queue.get_nowait()
            if isinstance(job, dict):
                job_data = cast(dict[str, object], job)
                remaining_prompts.append(str(job_data.get("prompt")))
            queue.task_done()

        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["remaining"], 3)
        self.assertEqual(
            remaining_prompts,
            ["first matching", "other owner", "other channel"],
        )

    async def test_retract_missing_runner_reports_normalized_key(self) -> None:
        result = await runner_queue.retract_thread_ask("thread-1")

        self.assertEqual(
            result,
            {
                "removed": 0,
                "remaining": 0,
                "active": False,
                "target_key": normalize_runner_key("thread-1"),
            },
        )
