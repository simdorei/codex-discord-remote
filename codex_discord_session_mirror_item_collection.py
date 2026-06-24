from __future__ import annotations

import codex_discord_session_mirror_item_builders as item_builders
import codex_discord_session_mirror_item_events as item_events
from codex_discord_session_mirror_item_append import (
    BuildInteractiveNoticeFunc,
    CollectionContext,
    ExtractMessageTextFunc,
    SessionPayload,
    SkipDiscordOriginPromptFunc,
    append_agent_if_new as _append_agent_if_new,
    append_item as _append_item,
    append_user_if_new as _append_user_if_new,
)
from codex_discord_session_mirror_item_builders import (
    SessionEvent,
    SessionMirrorItem,
    TextDigestFunc,
)

INTERNAL_RESPONSE_USER_PREFIXES = (
    "# AGENTS.md instructions",
    "<INSTRUCTIONS>",
    "<environment_context",
    "<codex_internal_context",
)


def _collect_function_item(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
    payload_type: str,
) -> bool:
    if payload_type == "function_call":
        notice = ctx.build_interactive_notice(payload)
        if notice:
            _append_item(ctx, items, event, kind="interactive", role="assistant", phase="interactive", text=notice)
        return True
    if payload_type == "function_call_output":
        output_text = str(payload.get("output") or "").strip()
        if output_text and "rejected by user" in output_text.lower():
            _append_item(
                ctx,
                items,
                event,
                kind="commentary",
                role="assistant",
                phase="approval_rejected",
                text="[approval_rejected]\nCommand approval was rejected by user.",
            )
        return True
    return False


def _collect_response_message(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
) -> None:
    text = ctx.extract_message_text(payload)
    if not text:
        return
    role = str(payload.get("role") or "?")
    phase = str(payload.get("phase") or "")
    if role == "assistant" and phase == "commentary":
        _append_agent_if_new(ctx, items, event, text, kind="commentary", phase=phase)
        return
    if role == "assistant" and phase == "final_answer":
        _append_agent_if_new(ctx, items, event, text, kind="final", phase=phase)
        return
    if role == "user" and not text.lstrip().startswith(INTERNAL_RESPONSE_USER_PREFIXES):
        _append_user_if_new(ctx, items, event, text, phase)


def _collect_response_item(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
) -> None:
    payload_type = str(payload.get("type") or "")
    if _collect_function_item(ctx, items, event, payload, payload_type):
        return
    if payload_type == "message":
        _collect_response_message(ctx, items, event, payload)


def collect_session_mirror_items(
    codex_thread_id: str,
    events: list[SessionEvent],
    *,
    seen_agent_messages: dict[str, float],
    seen_user_messages: dict[str, float],
    should_skip_discord_origin_prompt_func: SkipDiscordOriginPromptFunc,
    build_interactive_notice_func: BuildInteractiveNoticeFunc,
    extract_message_text_func: ExtractMessageTextFunc,
    recent_text_ttl_seconds: float,
    make_text_digest_func: TextDigestFunc = item_builders.make_text_digest,
) -> list[SessionMirrorItem]:
    ctx = CollectionContext(
        codex_thread_id=codex_thread_id,
        seen_agent_messages=seen_agent_messages,
        seen_user_messages=seen_user_messages,
        should_skip_discord_origin_prompt=should_skip_discord_origin_prompt_func,
        build_interactive_notice=build_interactive_notice_func,
        extract_message_text=extract_message_text_func,
        recent_text_ttl_seconds=recent_text_ttl_seconds,
        make_text_digest=make_text_digest_func,
    )
    items: list[SessionMirrorItem] = []
    for event in events:
        payload = item_events.event_payload(event)
        if payload is None:
            continue
        event_type = str(event.get("type") or "")
        if event_type == "event_msg":
            item_events.collect_event_message(ctx, items, event, payload)
        elif event_type == "response_item":
            _collect_response_item(ctx, items, event, payload)
    return items
