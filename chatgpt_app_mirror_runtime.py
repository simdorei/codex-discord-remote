from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
import re
from typing import Protocol

from chatgpt_app_mirror_models import (
    ChatGptMirrorCyclePlan,
    ChatGptMirrorDelivery,
    ChatGptRole,
    ChatGptSnapshot,
)


_DISCORD_MENTION_PATTERN = re.compile(r"@(everyone|here)|<@(?:!?\d+|&\d+)>")


class MirrorTask(Protocol):
    def done(self) -> bool: ...


class ChatGptAppMirrorOwner(Protocol):
    chatgpt_app_mirror_task: MirrorTask | None
    chatgpt_app_mirror_last_failure: str | None

    def is_closed(self) -> bool: ...

    async def send_chatgpt_mirror_delivery(
        self,
        delivery: ChatGptMirrorDelivery,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ChatGptAppMirrorRuntimeDeps:
    enabled: bool
    poll_seconds: float
    read_snapshot: Callable[[], Awaitable[ChatGptSnapshot]]
    prepare_cycle: Callable[[ChatGptSnapshot], Awaitable[ChatGptMirrorCyclePlan]]
    mark_delivery: Callable[[ChatGptMirrorDelivery], Awaitable[bool]]
    create_task: Callable[[Coroutine[object, object, None]], MirrorTask]
    sleep: Callable[[float], Awaitable[None]]
    expected_exceptions: tuple[type[BaseException], ...]
    log: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ChatGptAppMirrorRuntime:
    deps: ChatGptAppMirrorRuntimeDeps

    async def start(self, owner: ChatGptAppMirrorOwner) -> None:
        if not self.deps.enabled:
            self.deps.log("chatgpt_app_mirror_disabled")
            return
        task = _get_task(owner)
        if task is not None and not task.done():
            self.deps.log("chatgpt_app_mirror_already_running")
            return
        new_task = self.deps.create_task(self.loop(owner))
        owner.chatgpt_app_mirror_task = new_task
        self.deps.log(
            f"chatgpt_app_mirror_started seconds={self.deps.poll_seconds:g}"
        )

    async def loop(self, owner: ChatGptAppMirrorOwner) -> None:
        while not owner.is_closed():
            try:
                await self.run_cycle(owner)
                if owner.chatgpt_app_mirror_last_failure is not None:
                    owner.chatgpt_app_mirror_last_failure = None
                    self.deps.log("chatgpt_app_mirror_recovered")
            except self.deps.expected_exceptions as exc:
                signature = type(exc).__name__
                if owner.chatgpt_app_mirror_last_failure != signature:
                    owner.chatgpt_app_mirror_last_failure = signature
                    self.deps.log(
                        "chatgpt_app_mirror_poll_failed "
                        + f"error_type={signature} error={str(exc)[:300]}"
                    )
            await self.deps.sleep(self.deps.poll_seconds)

    async def run_cycle(self, owner: ChatGptAppMirrorOwner) -> None:
        snapshot = await self.deps.read_snapshot()
        plan = await self.deps.prepare_cycle(snapshot)
        for delivery in plan.deliveries:
            await owner.send_chatgpt_mirror_delivery(delivery)
            _ = await self.deps.mark_delivery(delivery)


def format_chatgpt_mirror_delivery(delivery: ChatGptMirrorDelivery) -> str:
    label = "User" if delivery.turn.role is ChatGptRole.USER else "ChatGPT"
    safe_text = _DISCORD_MENTION_PATTERN.sub(_escape_discord_mention, delivery.turn.text)
    return f"**GPT chat · {label}**\n{safe_text}"


def _escape_discord_mention(match: re.Match[str]) -> str:
    return match.group(0).replace("@", "@\u200b", 1)


def _get_task(owner: ChatGptAppMirrorOwner) -> MirrorTask | None:
    return owner.chatgpt_app_mirror_task
