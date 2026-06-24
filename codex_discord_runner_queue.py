"""Queue state helpers for per-thread Discord runner jobs."""

from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Awaitable, Callable, Coroutine
from typing import Protocol, TypeAlias, TypedDict, cast

from codex_discord_runtime import normalize_runner_key


class QueueRecord(Protocol):
    pass


QueueJobValue: TypeAlias = QueueRecord | str | int | bool | None
QueueJob: TypeAlias = dict[str, QueueJobValue]
QueueItem: TypeAlias = QueueJob | QueueJobValue
QueueCandidate: TypeAlias = asyncio.Queue[QueueItem] | QueueItem


class ThreadRunner(TypedDict):
    queue: asyncio.Queue[QueueItem]
    task: asyncio.Task[None] | None
    active: bool
    target_thread_id: str | None


RunnerMap: TypeAlias = dict[str, ThreadRunner]
NormalizeRunnerKeyFunc: TypeAlias = Callable[[str | None], str]
ThreadRunnerLoopFunc: TypeAlias = Callable[[str | None], Coroutine[None, None, None]]
GetThreadRunnerFunc: TypeAlias = Callable[[str | None], Awaitable[ThreadRunner]]


THREAD_RUNNERS_LOCK = asyncio.Lock()
THREAD_RUNNERS: RunnerMap = {}


def _queue_or_none(queue_candidate: QueueCandidate) -> asyncio.Queue[QueueItem] | None:
    if not isinstance(queue_candidate, asyncio.Queue):
        return None
    return cast(asyncio.Queue[QueueItem], queue_candidate)


async def get_thread_runner(
    target_thread_id: str | None,
    *,
    runners: RunnerMap = THREAD_RUNNERS,
    runners_lock: asyncio.Lock = THREAD_RUNNERS_LOCK,
    normalize_runner_key_func: NormalizeRunnerKeyFunc = normalize_runner_key,
) -> ThreadRunner:
    key = normalize_runner_key_func(target_thread_id)
    async with runners_lock:
        runner = runners.get(key)
        if runner is not None:
            return runner
        new_runner: ThreadRunner = {
            "queue": asyncio.Queue[QueueItem](),
            "task": None,
            "active": False,
            "target_thread_id": target_thread_id,
        }
        runners[key] = new_runner
        return new_runner


async def is_thread_runner_busy(
    target_thread_id: str | None,
    *,
    get_thread_runner_func: GetThreadRunnerFunc = get_thread_runner,
) -> bool:
    runner = await get_thread_runner_func(target_thread_id)
    return runner["active"] or runner["queue"].qsize() > 0


async def enqueue_thread_ask(
    channel: QueueJobValue,
    prompt: str,
    target_thread_id: str | None,
    *,
    queued: bool = False,
    ack_sent: bool = False,
    source_message: QueueJobValue = None,
    thread_runner_loop_func: ThreadRunnerLoopFunc,
    get_thread_runner_func: GetThreadRunnerFunc = get_thread_runner,
) -> int:
    runner = await get_thread_runner_func(target_thread_id)
    queue = runner["queue"]
    await queue.put(
        {
            "channel": channel,
            "prompt": prompt,
            "target_thread_id": target_thread_id,
            "queued": queued,
            "ack_sent": ack_sent,
            "source_message": source_message,
        }
    )
    task = runner.get("task")
    if not isinstance(task, asyncio.Task) or task.done():
        runner["task"] = asyncio.create_task(thread_runner_loop_func(target_thread_id))
    return queue.qsize()


async def retract_thread_ask(
    target_thread_id: str | None,
    *,
    channel_id: int | None = None,
    owner_user_id: int | None = None,
    runners: RunnerMap = THREAD_RUNNERS,
    runners_lock: asyncio.Lock = THREAD_RUNNERS_LOCK,
    normalize_runner_key_func: NormalizeRunnerKeyFunc = normalize_runner_key,
) -> dict[str, int | bool | str]:
    key = normalize_runner_key_func(target_thread_id)

    def matches(job: QueueItem) -> bool:
        if not isinstance(job, dict):
            return False
        job_data = cast(QueueJob, job)
        if channel_id is not None:
            channel = job_data.get("channel")
            if int(getattr(channel, "id", 0) or 0) != int(channel_id):
                return False
        if owner_user_id is not None:
            source_message = job_data.get("source_message")
            author = getattr(source_message, "author", None)
            if int(getattr(author, "id", 0) or 0) != int(owner_user_id):
                return False
        return True

    async with runners_lock:
        runner = runners.get(key)
        if runner is None:
            return {"removed": 0, "remaining": 0, "active": False, "target_key": key}
        queue = _queue_or_none(runner.get("queue"))
        if queue is None:
            return {
                "removed": 0,
                "remaining": 0,
                "active": runner["active"],
                "target_key": key,
            }

        drained: list[QueueItem] = []
        while True:
            try:
                drained.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        remove_index = -1
        for index, job in enumerate(drained):
            if matches(job):
                remove_index = index

        removed = 0
        for index, job in enumerate(drained):
            queue.task_done()
            if index == remove_index:
                removed += 1
                continue
            queue.put_nowait(job)

        return {
            "removed": removed,
            "remaining": queue.qsize(),
            "active": runner["active"],
            "target_key": key,
        }
