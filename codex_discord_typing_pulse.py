from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol

LogFunc = Callable[[str], None]


class ChannelTypingFactory(Protocol):
    def __call__(self, channel: object, *, context: str) -> AbstractAsyncContextManager[None]: ...


@dataclass(slots=True)
class TypingPulseRegistry:
    pulse_seconds: float = 8.0
    interval_seconds: float = 0.5
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    def start(
        self,
        channel: object,
        target_thread_id: str | None,
        context: str,
        *,
        channel_typing: ChannelTypingFactory,
        log: LogFunc,
    ) -> None:
        key = _normalize_key(target_thread_id)
        if not key:
            return
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._run_pulse(
                key,
                channel,
                context,
                channel_typing=channel_typing,
                log=log,
            )
        )
        self._tasks[key] = task
        task.add_done_callback(lambda done_task: self._drop_done_task(key, done_task))
        log(f"typing_pulse_started target={key} context={context or '-'}")

    def stop(self, target_thread_id: str | None) -> None:
        key = _normalize_key(target_thread_id)
        if not key:
            return
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def _run_pulse(
        self,
        key: str,
        channel: object,
        context: str,
        *,
        channel_typing: ChannelTypingFactory,
        log: LogFunc,
    ) -> None:
        try:
            while True:
                async with channel_typing(channel, context=context):
                    await asyncio.sleep(max(0.1, self.pulse_seconds))
                await asyncio.sleep(max(0.0, self.interval_seconds))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - background typing pulse boundary.
            log(f"typing_pulse_failed target={key} context={context or '-'} error_type={type(exc).__name__}")

    def _drop_done_task(self, key: str, done_task: asyncio.Task[None]) -> None:
        if self._tasks.get(key) is done_task:
            _ = self._tasks.pop(key, None)


SESSION_MIRROR_TYPING_PULSES = TypingPulseRegistry()


def start_session_mirror_typing_pulse(
    channel: object,
    target_thread_id: str | None,
    context: str,
    *,
    channel_typing: ChannelTypingFactory,
    log: LogFunc,
) -> None:
    SESSION_MIRROR_TYPING_PULSES.start(
        channel,
        target_thread_id,
        context,
        channel_typing=channel_typing,
        log=log,
    )


def stop_session_mirror_typing_pulse(target_thread_id: str | None) -> None:
    SESSION_MIRROR_TYPING_PULSES.stop(target_thread_id)


def _normalize_key(target_thread_id: str | None) -> str:
    return str(target_thread_id or "").strip()
