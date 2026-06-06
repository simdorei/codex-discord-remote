"""Runner queue helpers for Discord ask delivery."""

from __future__ import annotations

import asyncio
import time
import traceback

from dataclasses import dataclass

from codex_discord_runtime import normalize_runner_key


THREAD_RUNNERS_LOCK = asyncio.Lock()
THREAD_RUNNERS: dict[str, dict[str, object]] = {}


@dataclass(frozen=True)
class ThreadAskJob:
    channel: object
    prompt: str
    target_thread_id: str | None
    queued: bool = False
    ack_sent: bool = False
    source_message: object | None = None


def coerce_thread_ask_job(job: object) -> ThreadAskJob:
    if isinstance(job, ThreadAskJob):
        return job
    if not isinstance(job, dict):
        raise RuntimeError("Thread runner job is invalid.")
    return ThreadAskJob(
        channel=job.get("channel"),
        prompt=str(job.get("prompt") or ""),
        target_thread_id=str(job.get("target_thread_id") or "").strip() or None,
        queued=bool(job.get("queued")),
        ack_sent=bool(job.get("ack_sent")),
        source_message=job.get("source_message"),
    )


async def get_thread_runner(
    target_thread_id: str | None,
    *,
    runners: dict[str, dict[str, object]] = THREAD_RUNNERS,
    runners_lock: object = THREAD_RUNNERS_LOCK,
    normalize_runner_key_func=normalize_runner_key,
) -> dict[str, object]:
    key = normalize_runner_key_func(target_thread_id)
    async with runners_lock:
        runner = runners.get(key)
        if runner is None:
            runner = {
                "queue": asyncio.Queue(),
                "task": None,
                "active": False,
                "target_thread_id": target_thread_id,
            }
            runners[key] = runner
        return runner


async def is_thread_runner_busy(
    target_thread_id: str | None,
    *,
    get_thread_runner_func=get_thread_runner,
) -> bool:
    runner = await get_thread_runner_func(target_thread_id)
    queue = runner["queue"]
    return bool(runner.get("active")) or (
        isinstance(queue, asyncio.Queue) and queue.qsize() > 0
    )


async def wait_for_codex_thread_idle(
    target_thread_id: str | None,
    *,
    get_busy_state_func,
    timeout_sec: float = 3600.0,
    poll_sec: float = 5.0,
    monotonic_func=time.monotonic,
    sleep_func=asyncio.sleep,
    to_thread_func=asyncio.to_thread,
) -> tuple[str, str | None, str]:
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


async def enqueue_thread_ask(
    channel: object,
    prompt: str,
    target_thread_id: str | None,
    *,
    queued: bool = False,
    ack_sent: bool = False,
    source_message: object | None = None,
    thread_runner_loop_func,
    get_thread_runner_func=get_thread_runner,
) -> int:
    runner = await get_thread_runner_func(target_thread_id)
    queue = runner["queue"]
    if not isinstance(queue, asyncio.Queue):
        raise RuntimeError("Thread runner queue is invalid.")
    await queue.put(
        ThreadAskJob(
            channel=channel,
            prompt=prompt,
            target_thread_id=target_thread_id,
            queued=queued,
            ack_sent=ack_sent,
            source_message=source_message,
        )
    )
    task = runner.get("task")
    if not isinstance(task, asyncio.Task) or task.done():
        runner["task"] = asyncio.create_task(thread_runner_loop_func(target_thread_id))
    return queue.qsize()


async def report_thread_runner_job_failed(
    job: object,
    target_thread_id: str | None,
    *,
    log_func,
) -> None:
    try:
        ask_job = coerce_thread_ask_job(job)
    except RuntimeError:
        return
    channel = ask_job.channel
    if channel is None or not hasattr(channel, "send"):
        return
    try:
        await channel.send("Queued ask failed. Check codex_discord_bot.log.")
        log_func(f"thread_runner_job_failure_reported target={target_thread_id or '-'}")
    except Exception:
        log_func("thread_runner_job_failure_report_failed\n" + traceback.format_exc())


async def run_prompt_flow(
    channel: object,
    prompt: str,
    *,
    queued: bool = False,
    source_message: object | None = None,
    target_thread_id: str | None = None,
    build_context_warning_func,
    send_chunks_func,
    build_ask_start_message_func,
    run_prompt_and_send_func,
) -> None:
    warning = build_context_warning_func(target_thread_id)
    if warning:
        await send_chunks_func(channel, warning)
    await channel.send(build_ask_start_message_func(prompt, queued=queued))
    await run_prompt_and_send_func(
        channel,
        prompt,
        queued=queued,
        ack_sent=True,
        source_message=source_message,
        target_thread_id=target_thread_id,
    )


async def thread_runner_loop(
    target_thread_id: str | None,
    *,
    get_busy_state_func,
    wait_for_idle_func,
    run_prompt_and_send_func,
    report_job_failed_func,
    log_func,
    runners: dict[str, dict[str, object]] = THREAD_RUNNERS,
    runners_lock: object = THREAD_RUNNERS_LOCK,
    normalize_runner_key_func=normalize_runner_key,
    get_thread_runner_func=get_thread_runner,
    to_thread_func=asyncio.to_thread,
) -> None:
    key = normalize_runner_key_func(target_thread_id)
    while True:
        runner = await get_thread_runner_func(target_thread_id)
        queue = runner["queue"]
        if not isinstance(queue, asyncio.Queue):
            return
        try:
            job = await asyncio.wait_for(queue.get(), timeout=5)
        except asyncio.TimeoutError:
            async with runners_lock:
                current = runners.get(key)
                if current is runner and not bool(current.get("active")) and queue.empty():
                    runners.pop(key, None)
                    return
            continue

        runner["active"] = True
        try:
            ask_job = coerce_thread_ask_job(job)
            channel = ask_job.channel
            prompt = str(ask_job.prompt or "").strip()
            job_target_thread_id = ask_job.target_thread_id
            if prompt and hasattr(channel, "send"):
                queued = ask_job.queued
                ack_sent = ask_job.ack_sent
                if queued:
                    busy_state, _busy_thread_id, busy_ref = await to_thread_func(
                        get_busy_state_func,
                        job_target_thread_id,
                    )
                    if busy_state != "idle":
                        await channel.send(
                            f"Queued ask waiting for `{busy_ref or job_target_thread_id or 'selected'}` "
                            f"to become idle. Current state: {busy_state}."
                        )
                        busy_state, _busy_thread_id, busy_ref = await wait_for_idle_func(
                            job_target_thread_id,
                        )
                        if busy_state != "idle":
                            await channel.send(
                                f"Queued ask is still blocked for `{busy_ref or job_target_thread_id or 'selected'}`. "
                                f"Current state: {busy_state}."
                            )
                            continue
                await run_prompt_and_send_func(
                    channel,
                    prompt,
                    queued=queued,
                    ack_sent=ack_sent,
                    source_message=ask_job.source_message,
                    target_thread_id=job_target_thread_id,
                )
        except Exception:
            log_func("thread_runner_job_failed\n" + traceback.format_exc())
            await report_job_failed_func(job, target_thread_id)
        finally:
            runner["active"] = False
            queue.task_done()
