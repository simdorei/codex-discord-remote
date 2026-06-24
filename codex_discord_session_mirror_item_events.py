from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import codex_discord_session_mirror_item_builders as item_builders
from codex_discord_session_mirror_item_append import (
    CollectionContext,
    SessionPayload,
    append_agent_if_new,
    append_item,
    append_user_if_new,
    has_terminal_assistant_item,
    remember,
)
from codex_discord_session_mirror_item_builders import SessionEvent, SessionMirrorItem

ABORTED_PAYLOAD_TYPES = {"turn_aborted", "task_aborted", "task_cancelled"}


def event_payload(event: SessionEvent) -> SessionPayload | None:
    payload = event.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        return None
    return cast(SessionPayload, payload)


def format_no_visible_reply_text(payload: SessionPayload) -> str:
    details: list[str] = []
    turn_id = str(payload.get("turn_id") or "").strip()
    if turn_id:
        details.append(f"turn_id={turn_id}")
    duration_ms = payload.get("duration_ms")
    if duration_ms is not None:
        details.append(f"duration_ms={duration_ms}")
    if not details:
        return "Codex turn completed without a visible reply."
    return f"Codex turn completed without a visible reply.\nDetails: {', '.join(details)}"


def append_no_visible_reply_if_needed(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
) -> None:
    if has_terminal_assistant_item(items):
        return
    append_item(
        ctx,
        items,
        event,
        kind="final",
        role="assistant",
        phase="no_visible_reply",
        text=format_no_visible_reply_text(payload),
    )


def collect_agent_event_message(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
) -> None:
    phase = str(payload.get("phase") or "commentary")
    text = str(payload.get("message") or "").strip()
    if not text:
        return
    if phase == "final_answer":
        append_agent_if_new(ctx, items, event, text, kind="final", phase=phase)
        return
    remember(ctx, ctx.seen_agent_messages, text)
    append_item(ctx, items, event, kind="commentary", role="assistant", phase=phase, text=text)


def collect_event_message(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
) -> None:
    payload_type = str(payload.get("type") or "")
    if payload_type == "agent_message":
        collect_agent_event_message(ctx, items, event, payload)
        return
    if payload_type == "user_message":
        text = str(payload.get("message") or "").strip()
        if text:
            append_user_if_new(ctx, items, event, text, "input")
        return
    if payload_type == "task_complete" and payload.get("last_agent_message") is None:
        append_no_visible_reply_if_needed(ctx, items, event, payload)
        return
    if payload_type in ABORTED_PAYLOAD_TYPES:
        append_item(
            ctx,
            items,
            event,
            kind="aborted",
            role="assistant",
            phase=payload_type,
            text=item_builders.format_aborted_event_text(dict(payload)),
        )
