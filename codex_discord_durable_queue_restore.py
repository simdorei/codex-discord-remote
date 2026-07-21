from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from codex_discord_queue_job_memory import to_memory_queue_job
from codex_discord_runner_queue import QueueJob, QueueJobValue
import codex_discord_store as store


class QueueRestoreBot(Protocol):
    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[QueueJobValue, str]: ...
    def fetch_channel(self, channel_id: int) -> Awaitable[QueueJobValue]: ...
    def is_allowed_message_channel(self, channel: QueueJobValue) -> bool: ...


class QueueRestoreDeps(Protocol):
    @property
    def get_db_path(self) -> Callable[[], Path]: ...

    @property
    def log(self) -> Callable[[str], None]: ...


async def restore_queue_jobs(bot: QueueRestoreBot, deps: QueueRestoreDeps) -> list[QueueJob]:
    records = await asyncio.to_thread(store.list_queue_jobs, deps.get_db_path())
    jobs: list[QueueJob] = []
    for record in records:
        channel, source = bot.get_cached_channel_or_thread(record.channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(record.channel_id)
                source = "fetch"
            except (OSError, RuntimeError, TimeoutError) as exc:
                deps.log(
                    f"queue_restore_channel_failed channel={record.channel_id} "
                    + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
                )
                continue
        if not bot.is_allowed_message_channel(channel):
            deps.log(f"queue_restore_channel_denied channel={record.channel_id} source={source}")
            continue
        jobs.append(to_memory_queue_job(record, channel, None))
    return jobs
