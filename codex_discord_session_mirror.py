"""Session mirror event extraction helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
from typing import Callable


TextDigestFunc = Callable[..., str]
SkipDiscordOriginPromptFunc = Callable[[str | None, str], bool]
BuildInteractiveNoticeFunc = Callable[[dict], str | None]
ExtractMessageTextFunc = Callable[[dict], str]


@dataclass
class SessionMirrorState:
    active_output_targets: dict[str, float] = field(default_factory=dict)
    pending_cursor_targets: set[str] = field(default_factory=set)


def make_text_digest(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part or "").encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def remember_recent_session_text(
    seen: dict[str, float],
    text: str,
    *,
    ttl_seconds: float,
    now_func: Callable[[], float] = time.monotonic,
    make_text_digest_func: TextDigestFunc = make_text_digest,
) -> None:
    current = now_func()
    expired = [
        digest
        for digest, seen_at in seen.items()
        if current - seen_at > ttl_seconds
    ]
    for digest in expired:
        seen.pop(digest, None)
    seen[make_text_digest_func(text.strip())] = current


def has_recent_session_text(
    seen: dict[str, float],
    text: str,
    *,
    ttl_seconds: float,
    now_func: Callable[[], float] = time.monotonic,
    make_text_digest_func: TextDigestFunc = make_text_digest,
) -> bool:
    current = now_func()
    digest = make_text_digest_func(text.strip())
    seen_at = seen.get(digest)
    if seen_at is None:
        return False
    if current - seen_at > ttl_seconds:
        seen.pop(digest, None)
        return False
    return True


def make_session_mirror_event_digest(
    codex_thread_id: str,
    event: dict,
    kind: str,
    role: str,
    phase: str,
    text: str,
    *,
    make_text_digest_func: TextDigestFunc = make_text_digest,
) -> str:
    payload = event.get("payload") or {}
    payload_type = payload.get("type") if isinstance(payload, dict) else ""
    return make_text_digest_func(
        "session-mirror",
        codex_thread_id,
        event.get("timestamp") or "",
        event.get("type") or "",
        payload_type or "",
        kind,
        role,
        phase,
        text,
    )


def make_session_mirror_item(
    codex_thread_id: str,
    event: dict,
    *,
    kind: str,
    role: str,
    phase: str,
    text: str,
    make_text_digest_func: TextDigestFunc = make_text_digest,
) -> dict[str, str]:
    clean_text = str(text or "").strip()
    return {
        "digest": make_session_mirror_event_digest(
            codex_thread_id,
            event,
            kind,
            role,
            phase,
            clean_text,
            make_text_digest_func=make_text_digest_func,
        ),
        "kind": kind,
        "role": role,
        "phase": phase,
        "text": clean_text,
    }


def collect_session_mirror_items(
    codex_thread_id: str,
    events: list[dict],
    *,
    seen_agent_messages: dict[str, float],
    seen_user_messages: dict[str, float],
    should_skip_discord_origin_prompt_func: SkipDiscordOriginPromptFunc,
    build_interactive_notice_func: BuildInteractiveNoticeFunc,
    extract_message_text_func: ExtractMessageTextFunc,
    recent_text_ttl_seconds: float,
    make_text_digest_func: TextDigestFunc = make_text_digest,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for event in events:
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue

        event_type = str(event.get("type") or "")
        payload_type = str(payload.get("type") or "")
        if event_type == "event_msg":
            if payload_type == "agent_message":
                phase = str(payload.get("phase") or "commentary")
                text = str(payload.get("message") or "").strip()
                if not text:
                    continue
                if phase == "final_answer":
                    if has_recent_session_text(
                        seen_agent_messages,
                        text,
                        ttl_seconds=recent_text_ttl_seconds,
                        make_text_digest_func=make_text_digest_func,
                    ):
                        continue
                    remember_recent_session_text(
                        seen_agent_messages,
                        text,
                        ttl_seconds=recent_text_ttl_seconds,
                        make_text_digest_func=make_text_digest_func,
                    )
                    items.append(
                        make_session_mirror_item(
                            codex_thread_id,
                            event,
                            kind="final",
                            role="assistant",
                            phase=phase,
                            text=text,
                            make_text_digest_func=make_text_digest_func,
                        )
                    )
                    continue
                remember_recent_session_text(
                    seen_agent_messages,
                    text,
                    ttl_seconds=recent_text_ttl_seconds,
                    make_text_digest_func=make_text_digest_func,
                )
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="commentary",
                        role="assistant",
                        phase=phase,
                        text=text,
                        make_text_digest_func=make_text_digest_func,
                    )
                )
                continue
            if payload_type == "user_message":
                text = str(payload.get("message") or "").strip()
                if not text:
                    continue
                if should_skip_discord_origin_prompt_func(codex_thread_id, text):
                    remember_recent_session_text(
                        seen_user_messages,
                        text,
                        ttl_seconds=recent_text_ttl_seconds,
                        make_text_digest_func=make_text_digest_func,
                    )
                    continue
                if has_recent_session_text(
                    seen_user_messages,
                    text,
                    ttl_seconds=recent_text_ttl_seconds,
                    make_text_digest_func=make_text_digest_func,
                ):
                    continue
                remember_recent_session_text(
                    seen_user_messages,
                    text,
                    ttl_seconds=recent_text_ttl_seconds,
                    make_text_digest_func=make_text_digest_func,
                )
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="user",
                        role="user",
                        phase="input",
                        text=text,
                        make_text_digest_func=make_text_digest_func,
                    )
                )
                continue
            if payload_type in {"turn_aborted", "task_aborted", "task_cancelled"}:
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="aborted",
                        role="assistant",
                        phase=payload_type,
                        text="Aborted.",
                        make_text_digest_func=make_text_digest_func,
                    )
                )
                continue
            continue

        if event_type != "response_item":
            continue

        if payload_type == "function_call":
            notice = build_interactive_notice_func(payload)
            if notice:
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="interactive",
                        role="assistant",
                        phase="interactive",
                        text=notice,
                        make_text_digest_func=make_text_digest_func,
                    )
                )
            continue

        if payload_type == "function_call_output":
            output_text = str(payload.get("output") or "").strip()
            if output_text and "rejected by user" in output_text.lower():
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="commentary",
                        role="assistant",
                        phase="approval_rejected",
                        text="[approval_rejected]\nCommand approval was rejected by user.",
                        make_text_digest_func=make_text_digest_func,
                    )
                )
            continue

        if payload_type != "message":
            continue
        text = extract_message_text_func(payload)
        if not text:
            continue
        role = str(payload.get("role") or "?")
        phase = str(payload.get("phase") or "")
        if role == "assistant" and phase == "commentary":
            if has_recent_session_text(
                seen_agent_messages,
                text,
                ttl_seconds=recent_text_ttl_seconds,
                make_text_digest_func=make_text_digest_func,
            ):
                continue
            remember_recent_session_text(
                seen_agent_messages,
                text,
                ttl_seconds=recent_text_ttl_seconds,
                make_text_digest_func=make_text_digest_func,
            )
            items.append(
                make_session_mirror_item(
                    codex_thread_id,
                    event,
                    kind="commentary",
                    role=role,
                    phase=phase,
                    text=text,
                    make_text_digest_func=make_text_digest_func,
                )
            )
            continue
        if role == "assistant" and phase == "final_answer":
            if has_recent_session_text(
                seen_agent_messages,
                text,
                ttl_seconds=recent_text_ttl_seconds,
                make_text_digest_func=make_text_digest_func,
            ):
                continue
            remember_recent_session_text(
                seen_agent_messages,
                text,
                ttl_seconds=recent_text_ttl_seconds,
                make_text_digest_func=make_text_digest_func,
            )
            items.append(
                make_session_mirror_item(
                    codex_thread_id,
                    event,
                    kind="final",
                    role=role,
                    phase=phase,
                    text=text,
                    make_text_digest_func=make_text_digest_func,
                )
            )
            continue
        if role == "user":
            if should_skip_discord_origin_prompt_func(codex_thread_id, text):
                remember_recent_session_text(
                    seen_user_messages,
                    text,
                    ttl_seconds=recent_text_ttl_seconds,
                    make_text_digest_func=make_text_digest_func,
                )
                continue
            if has_recent_session_text(
                seen_user_messages,
                text,
                ttl_seconds=recent_text_ttl_seconds,
                make_text_digest_func=make_text_digest_func,
            ):
                continue
            remember_recent_session_text(
                seen_user_messages,
                text,
                ttl_seconds=recent_text_ttl_seconds,
                make_text_digest_func=make_text_digest_func,
            )
            items.append(
                make_session_mirror_item(
                    codex_thread_id,
                    event,
                    kind="user",
                    role=role,
                    phase=phase or "input",
                    text=text,
                    make_text_digest_func=make_text_digest_func,
                )
            )
    return items


def format_session_mirror_text(item: dict[str, str]) -> str:
    kind = item.get("kind") or ""
    text = item.get("text") or ""
    if kind == "commentary":
        return f"In progress\n\n{text}"
    if kind == "user":
        return f"Codex app user\n\n{text}"
    if kind == "aborted":
        return "Aborted."
    return text
