from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from codex_session_events import JsonValue
import codex_discord_session_mirror_item_builders as item_builders
from codex_discord_session_mirror_item_builders import (
    SessionEvent,
    SessionMirrorItem,
    TextDigestFunc,
)

SessionPayload = Mapping[str, JsonValue]
SkipDiscordOriginPromptFunc = Callable[[str | None, str], bool]
BuildInteractiveNoticeFunc = Callable[[SessionPayload], str | None]
ExtractMessageTextFunc = Callable[[SessionPayload], str]


@dataclass(frozen=True, slots=True)
class CollectionContext:
    codex_thread_id: str
    seen_agent_messages: dict[str, float]
    seen_user_messages: dict[str, float]
    should_skip_discord_origin_prompt: SkipDiscordOriginPromptFunc
    build_interactive_notice: BuildInteractiveNoticeFunc
    extract_message_text: ExtractMessageTextFunc
    recent_text_ttl_seconds: float
    make_text_digest: TextDigestFunc


def remember(ctx: CollectionContext, seen: dict[str, float], text: str) -> None:
    item_builders.remember_recent_session_text(
        seen,
        text,
        ttl_seconds=ctx.recent_text_ttl_seconds,
        make_text_digest_func=ctx.make_text_digest,
    )


def is_new_text(ctx: CollectionContext, seen: dict[str, float], text: str) -> bool:
    if item_builders.has_recent_session_text(
        seen,
        text,
        ttl_seconds=ctx.recent_text_ttl_seconds,
        make_text_digest_func=ctx.make_text_digest,
    ):
        return False
    remember(ctx, seen, text)
    return True


def append_item(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    *,
    kind: str,
    role: str,
    phase: str,
    text: str,
) -> None:
    items.append(
        item_builders.make_session_mirror_item(
            ctx.codex_thread_id,
            event,
            kind=kind,
            role=role,
            phase=phase,
            text=text,
            make_text_digest_func=ctx.make_text_digest,
        )
    )


def append_user_if_new(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    text: str,
    phase: str,
) -> None:
    if ctx.should_skip_discord_origin_prompt(ctx.codex_thread_id, text):
        remember(ctx, ctx.seen_user_messages, text)
        return
    if is_new_text(ctx, ctx.seen_user_messages, text):
        append_item(ctx, items, event, kind="user", role="user", phase=phase or "input", text=text)


def append_agent_if_new(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    text: str,
    *,
    kind: str,
    phase: str,
) -> None:
    if is_new_text(ctx, ctx.seen_agent_messages, text):
        append_item(ctx, items, event, kind=kind, role="assistant", phase=phase, text=text)


def has_terminal_assistant_item(items: list[SessionMirrorItem]) -> bool:
    return any(item.get("role") == "assistant" and item.get("kind") in {"final", "aborted"} for item in items)
