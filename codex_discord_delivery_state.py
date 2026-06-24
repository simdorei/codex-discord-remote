from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
import hashlib
import time
from dataclasses import dataclass, field
from typing import Protocol

from codex_discord_text import (
    DISCORD_MAX_LEN,
    env_flag,
    format_discord_command_label,
    split_message,
)

DISCORD_RESTARTING_ERROR = "Discord bot is restarting; refusing new Discord delivery."
DISCORD_RESTARTING_NOTICE = (
    "Discord bot is restarting; this request was not accepted.\n"
    "Retry after the bot is online again."
)
DISCORD_SEND_RETRY_DELAYS_SECONDS = (0.75, 2.0)
DISCORD_CHUNK_MARKERS_ENABLED = env_flag("DISCORD_CHUNK_MARKERS", True)


class DiscordDeliveryRejected(RuntimeError):
    pass


class LogFunc(Protocol):
    def __call__(self, message: str, /) -> None: ...


DiscordIdValue = int | str | bytes | bytearray | None


class MessageSendResult(Protocol):
    @property
    def id(self) -> DiscordIdValue: ...


class MessageableIdentityLike(Protocol):
    @property
    def id(self) -> DiscordIdValue: ...


class Messageable(Protocol):
    @property
    def id(self) -> DiscordIdValue: ...

    async def send(self, content: str) -> MessageSendResult | None: ...


class InteractionResponse(Protocol):
    async def send_message(self, content: str, *, ephemeral: bool = False) -> None: ...


class InteractionCommandLike(Protocol):
    @property
    def name(self) -> str: ...


class InteractionCommandSource(Protocol):
    @property
    def command(self) -> InteractionCommandLike | None: ...


class InteractionLike(Protocol):
    @property
    def command(self) -> InteractionCommandLike | None: ...

    @property
    def response(self) -> InteractionResponse: ...

    @property
    def channel_id(self) -> DiscordIdValue: ...


@dataclass(slots=True)  # noqa: MUTABLE_OK
class DiscordDeliveryState:
    active_deliveries: set[str] = field(default_factory=set)
    stopping: bool = False
    retry_delays_seconds: tuple[float, ...] = DISCORD_SEND_RETRY_DELAYS_SECONDS
    chunk_markers_enabled: bool = DISCORD_CHUNK_MARKERS_ENABLED


def get_messageable_id(target: MessageableIdentityLike | InteractionLike) -> str:
    return str(getattr(target, "id", None) or getattr(target, "channel_id", None) or "-")


def build_delivery_id(text: str) -> str:
    return hashlib.blake2s(
        str(text or "").encode("utf-8", errors="replace"),
        digest_size=4,
    ).hexdigest()


def set_discord_delivery_stopping(
    state: DiscordDeliveryState,
    reason: str,
    *,
    log_func: LogFunc,
) -> None:
    if state.stopping:
        return
    state.stopping = True
    log_func(
        f"discord_delivery_stopping reason={format_discord_command_label(reason, limit=80)} "
        + f"active={len(state.active_deliveries)}"
    )


def clear_discord_delivery_stopping(state: DiscordDeliveryState) -> None:
    state.stopping = False


def is_discord_delivery_stopping(state: DiscordDeliveryState) -> bool:
    return state.stopping


def begin_discord_delivery(
    state: DiscordDeliveryState,
    label: str,
    *,
    log_func: LogFunc,
    allow_during_stop: bool = False,
) -> str:
    if state.stopping and not allow_during_stop:
        safe_label = format_discord_command_label(label, limit=120)
        log_func(f"discord_delivery_rejected context={safe_label or '-'} reason=stopping")
        raise DiscordDeliveryRejected(DISCORD_RESTARTING_ERROR)
    token = f"{label}:{time.monotonic_ns()}"
    state.active_deliveries.add(token)
    return token


def end_discord_delivery(state: DiscordDeliveryState, token: str) -> None:
    state.active_deliveries.discard(token)


async def wait_for_discord_delivery_drain(
    state: DiscordDeliveryState,
    *,
    timeout_seconds: float,
    reason: str,
    log_func: LogFunc,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while state.active_deliveries:
        if time.monotonic() >= deadline:
            log_func(
                f"discord_delivery_drain_timeout reason={reason} "
                + f"active={len(state.active_deliveries)}"
            )
            return False
        await asyncio.sleep(0.1)
    log_func(f"discord_delivery_drain_done reason={reason}")
    return True


async def sleep_discord_delivery_retry(delay_seconds: float) -> None:
    await asyncio.sleep(delay_seconds)


def split_delivery_chunks(text: str, *, state: DiscordDeliveryState) -> list[str]:
    text = str(text or "")
    if not state.chunk_markers_enabled:
        return split_message(text)
    marker_budget = 32
    chunks = split_message(text, limit=max(1, DISCORD_MAX_LEN - marker_budget))
    if len(chunks) <= 1:
        return chunks
    total = len(chunks)
    return [f"[{index}/{total}]\n{chunk}" for index, chunk in enumerate(chunks, start=1)]


def get_interaction_command_name(interaction: InteractionCommandSource) -> str:
    command = interaction.command
    return "-" if command is None else str(command.name or "-")
