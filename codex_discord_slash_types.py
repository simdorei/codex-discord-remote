from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "BasicSlashCommandDeps",
    "ContextMessageBuilder",
    "ContextRefreshMessageBuilder",
    "InteractionBridgeRunner",
    "InteractionChunksSender",
    "InteractionResponse",
    "SlashCallback",
    "SlashCommandBot",
    "SlashCommandTree",
    "SlashInteraction",
    "WeeklyUsageMessageBuilder",
]


SlashCallback = Callable[..., Awaitable[None]]


class SlashCommandTree(Protocol):
    def command(
        self,
        *,
        name: str,
        description: str,
    ) -> Callable[[SlashCallback], SlashCallback]: ...


class SlashCommandBot(Protocol):
    @property
    def tree(self) -> SlashCommandTree: ...


class InteractionResponse(Protocol):
    def defer(self, thinking: bool = False, **kwargs: bool) -> Awaitable[None]: ...


class SlashInteraction(Protocol):
    channel_id: int | None
    response: InteractionResponse


class InteractionChunksSender(Protocol):
    def __call__(
        self,
        interaction: SlashInteraction,
        text: str,
        *,
        title: str,
    ) -> Awaitable[None]: ...


class InteractionBridgeRunner(Protocol):
    def __call__(
        self,
        interaction: SlashInteraction,
        argv: list[str],
        title: str,
    ) -> Awaitable[tuple[int, str]]: ...


class ContextMessageBuilder(Protocol):
    def __call__(
        self,
        channel_id: int | None,
        *,
        all_threads: bool = False,
        limit: int = 20,
    ) -> str: ...


class ContextRefreshMessageBuilder(Protocol):
    def __call__(self, channel_id: int | None, *, limit: int) -> str: ...


class WeeklyUsageMessageBuilder(Protocol):
    def __call__(self, *, days: int) -> str: ...


@dataclass(frozen=True, slots=True)
class BasicSlashCommandDeps:
    check_allowed: Callable[[SlashInteraction], bool]
    send_not_allowed: Callable[[SlashInteraction], Awaitable[None]]
    send_chunks: InteractionChunksSender
    run_bridge: InteractionBridgeRunner
    build_help: Callable[[], str]
    build_where: Callable[[int | None], str]
    build_context: ContextMessageBuilder
    build_context_refresh: ContextRefreshMessageBuilder
    build_weekly_usage: WeeklyUsageMessageBuilder
    clamp_context_refresh_limit: Callable[[int], int]
    resolve_target_args: Callable[[int | None, str | None], list[str]]
