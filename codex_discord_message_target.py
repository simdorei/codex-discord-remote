from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, assert_never

import codex_discord_explicit_target as discord_explicit_target
import codex_discord_project_runtime as project_runtime

MessageTargetSource = Literal["mirror", "gpt", "blocked", "selected", "explicit"]
MirrorThreadLookup = Callable[[int | None], str | None]


def exact_channel_block_reason(
    decision: project_runtime.ExactChannelDecision,
) -> str | None:
    match decision:
        case (
            project_runtime.ExactChannelActive() | project_runtime.ExactChannelUnknown()
        ):
            return None
        case project_runtime.ExactChannelBlocked(reason=reason):
            return reason
        case _:
            assert_never(decision)


@dataclass(frozen=True, slots=True)
class DiscordMessageTarget:
    target_thread_id: str | None
    target_source: MessageTargetSource
    persist_mirror_channel: bool = False
    blocked_reason: str | None = None

    def with_explicit_target(
        self, content: str, *, bot_bridge_mention: bool
    ) -> DiscordMessageTarget:
        if not bot_bridge_mention or self.target_source in {"gpt", "blocked"}:
            return self
        explicit_target_thread_id = (
            discord_explicit_target.extract_explicit_codex_thread_id(content)
        )
        if explicit_target_thread_id is None:
            return self
        return DiscordMessageTarget(
            target_thread_id=explicit_target_thread_id,
            target_source="explicit",
        )


def _resolve_unknown_message_target(
    lookup_mirrored_codex_thread_id: MirrorThreadLookup,
    channel_id: int | None,
    parent_channel_id: int | None,
) -> DiscordMessageTarget:
    target_thread_id = lookup_mirrored_codex_thread_id(channel_id)
    if target_thread_id is not None:
        return DiscordMessageTarget(
            target_thread_id=target_thread_id,
            target_source="mirror",
            persist_mirror_channel=parent_channel_id is not None,
        )
    target_thread_id = lookup_mirrored_codex_thread_id(parent_channel_id)
    target_source: MessageTargetSource = "mirror" if target_thread_id else "selected"
    return DiscordMessageTarget(
        target_thread_id=target_thread_id, target_source=target_source
    )


def resolve_discord_message_target(
    lookup_mirrored_codex_thread_id: MirrorThreadLookup,
    channel_id: int | None,
    parent_channel_id: int | None,
    *,
    exact_channel_decision: project_runtime.ExactChannelDecision | None = None,
) -> DiscordMessageTarget:
    safety = (
        project_runtime.ExactChannelUnknown()
        if exact_channel_decision is None
        else exact_channel_decision
    )
    match safety:
        case project_runtime.ExactChannelActive(codex_thread_id=codex_thread_id):
            return DiscordMessageTarget(codex_thread_id, "gpt")
        case project_runtime.ExactChannelBlocked(reason=reason):
            return DiscordMessageTarget(None, "blocked", blocked_reason=reason)
        case project_runtime.ExactChannelUnknown():
            return _resolve_unknown_message_target(
                lookup_mirrored_codex_thread_id,
                channel_id,
                parent_channel_id,
            )
        case _:
            assert_never(safety)
