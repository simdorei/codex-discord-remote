"""Runner queue helpers for Discord ask delivery."""

from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import TypeAlias, cast

from codex_discord_runtime import normalize_runner_key
from codex_discord_runner_queue import (
    THREAD_RUNNERS,
    THREAD_RUNNERS_LOCK,
    QueueItem,
    QueueJob,
    GetThreadRunnerFunc,
    NormalizeRunnerKeyFunc,
    RunnerMap,
    enqueue_thread_ask,
    get_thread_runner,
    is_thread_runner_busy,
    retract_thread_ask,
)

BusyState: TypeAlias = tuple[str, str | None, str]
GetBusyStateFunc: TypeAlias = Callable[[str | None], BusyState]
MonotonicFunc: TypeAlias = Callable[[], float]
SleepFunc: TypeAlias = Callable[[float], Awaitable[None]]
ToThreadBusyStateFunc: TypeAlias = Callable[[GetBusyStateFunc, str | None], Awaitable[BusyState]]
SendTextFunc: TypeAlias = Callable[..., Awaitable[int]]
LogFunc: TypeAlias = Callable[[str], None]
WaitForIdleFunc: TypeAlias = Callable[[str | None], Awaitable[BusyState]]
RunPromptAndSendFunc: TypeAlias = Callable[..., Awaitable[None]]
ReportJobFailedFunc: TypeAlias = Callable[[QueueItem, str | None], Awaitable[None]]

__all__ = (
    "THREAD_RUNNERS",
    "THREAD_RUNNERS_LOCK",
    "enqueue_thread_ask",
    "get_thread_runner",
    "is_thread_runner_busy",
    "retract_thread_ask",
    "wait_for_codex_thread_idle",
    "report_thread_runner_job_failed",
    "thread_runner_loop",
)


class ThreadRunnerJobInvalidError(RuntimeError):
    pass


async def _to_thread_busy_state(
    func: GetBusyStateFunc,
    target_thread_id: str | None,
) -> BusyState:
    return await asyncio.to_thread(func, target_thread_id)


async def wait_for_codex_thread_idle(
    target_thread_id: str | None,
    *,
    get_busy_state_func: GetBusyStateFunc,
    timeout_sec: float = 3600.0,
    poll_sec: float = 5.0,
    monotonic_func: MonotonicFunc = time.monotonic,
    sleep_func: SleepFunc = asyncio.sleep,
    to_thread_func: ToThreadBusyStateFunc = _to_thread_busy_state,
) -> BusyState:
    deadline = monotonic_func() + timeout_sec
    last_state = "idle"
    last_thread_id: str | None = None
    last_ref = ""
    while monotonic_func() < deadline:
        state, resolved_thread_id, target_ref = await to_thread_func(
            get_busy_state_func,
            target_thread_id,
        )
        last_state = state
        last_thread_id = resolved_thread_id
        last_ref = target_ref
        if state == "idle":
            return state, resolved_thread_id, target_ref
        await sleep_func(poll_sec)
    return last_state, last_thread_id, last_ref


async def report_thread_runner_job_failed(
    job: QueueItem,
    target_thread_id: str | None,
    *,
    send_text_func: SendTextFunc,
    log_func: LogFunc,
) -> None:
    job_data = cast(QueueJob, job) if isinstance(job, dict) else {}
    channel = job_data.get("channel")
    if channel is None or not hasattr(channel, "send"):
        return
    try:
        _ = await send_text_func(
            channel,
            "Queued ask failed. Check codex_discord_bot.log.",
            context="thread_runner_job_failed",
        )
        log_func(f"thread_runner_job_failure_reported target={target_thread_id or '-'}")
    except Exception:  # noqa: BROAD_EXCEPT_OK
        log_func("thread_runner_job_failure_report_failed\n" + traceback.format_exc())


async def thread_runner_loop(
    target_thread_id: str | None,
    *,
    get_busy_state_func: GetBusyStateFunc,
    wait_for_idle_func: WaitForIdleFunc,
    run_prompt_and_send_func: RunPromptAndSendFunc,
    report_job_failed_func: ReportJobFailedFunc,
    send_text_func: SendTextFunc,
    log_func: LogFunc,
    runners: RunnerMap = THREAD_RUNNERS,
    runners_lock: asyncio.Lock = THREAD_RUNNERS_LOCK,
    normalize_runner_key_func: NormalizeRunnerKeyFunc = normalize_runner_key,
    get_thread_runner_func: GetThreadRunnerFunc = get_thread_runner,
    to_thread_func: ToThreadBusyStateFunc = _to_thread_busy_state,
) -> None:
    key = normalize_runner_key_func(target_thread_id)
    while True:
        runner = await get_thread_runner_func(target_thread_id)
        queue = runner["queue"]
        try:
            job: QueueItem = await asyncio.wait_for(queue.get(), timeout=5)
            job_for_failure: QueueItem = job
        except asyncio.TimeoutError:
            async with runners_lock:
                current = runners.get(key)
                if current is None or current is not runner:
                    continue
                if not current["active"] and queue.empty():
                    _ = runners.pop(key, None)
                    return
            continue

        runner["active"] = True
        try:
            if not isinstance(job, dict):
                raise ThreadRunnerJobInvalidError("Thread runner job is invalid.")
            job_data = cast(QueueJob, job)
            channel = job_data.get("channel")
            prompt = str(job_data.get("prompt") or "").strip()
            job_target_thread_id = str(job_data.get("target_thread_id") or "").strip() or None
            if prompt and hasattr(channel, "send"):
                queued = bool(job_data.get("queued"))
                ack_sent = bool(job_data.get("ack_sent"))
                if queued:
                    busy_state, _busy_thread_id, busy_ref = await to_thread_func(
                        get_busy_state_func,
                        job_target_thread_id,
                    )
                    if busy_state != "idle":
                        busy_label = busy_ref or job_target_thread_id or "selected"
                        _ = await send_text_func(
                            channel,
                            f"Queued ask waiting for `{busy_label}` to become idle. Current state: {busy_state}.",
                            context="thread_runner_waiting",
                        )
                        busy_state, _busy_thread_id, busy_ref = await wait_for_idle_func(
                            job_target_thread_id,
                        )
                        if busy_state != "idle":
                            busy_label = busy_ref or job_target_thread_id or "selected"
                            _ = await send_text_func(
                                channel,
                                f"Queued ask is still blocked for `{busy_label}`. Current state: {busy_state}.",
                                context="thread_runner_still_blocked",
                            )
                            continue
                await run_prompt_and_send_func(
                    channel,
                    prompt,
                    queued=queued,
                    ack_sent=ack_sent,
                    source_message=job_data.get("source_message"),
                    target_thread_id=job_target_thread_id,
                )
        except Exception:  # noqa: BROAD_EXCEPT_OK
            log_func("thread_runner_job_failed\n" + traceback.format_exc())
            await report_job_failed_func(job_for_failure, target_thread_id)
        finally:
            runner["active"] = False
            queue.task_done()
