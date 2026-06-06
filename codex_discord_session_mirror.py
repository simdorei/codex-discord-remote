"""Session mirror contracts for Codex app events sent to Discord."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable, MutableMapping


def make_text_digest(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part or "").encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def make_discord_origin_prompt_digest(target_key: str | None, prompt: str) -> str:
    return make_text_digest("discord-origin", str(target_key or "").strip(), str(prompt or "").strip())


def make_session_mirror_event_digest(
    codex_thread_id: str,
    event: dict,
    kind: str,
    role: str,
    phase: str,
    text: str,
) -> str:
    payload = event.get("payload") or {}
    payload_type = payload.get("type") if isinstance(payload, dict) else ""
    return make_text_digest(
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
) -> dict[str, str]:
    clean_text = str(text or "").strip()
    return {
        "digest": make_session_mirror_event_digest(codex_thread_id, event, kind, role, phase, clean_text),
        "kind": kind,
        "role": role,
        "phase": phase,
        "text": clean_text,
    }


def default_extract_message_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("content") or ""
            if text:
                parts.append(str(text))
        if parts:
            return "\n".join(parts).strip()
    return str(payload.get("message") or payload.get("text") or "").strip()


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


@dataclass(frozen=True)
class SessionMirrorCollector:
    origin_prompts: MutableMapping[str, float]
    origin_prompt_ttl_seconds: float
    recent_text_ttl_seconds: float
    normalize_target_key_func: Callable[[str | None], str]
    extract_message_text_func: Callable[[dict], str] = default_extract_message_text
    make_interactive_notice_func: Callable[[dict], str | None] = lambda _payload: None
    time_func: Callable[[], float] = time.monotonic

    def target_key(self, target_thread_id: str | None) -> str:
        return self.normalize_target_key_func(target_thread_id)

    def cleanup_recent_discord_origin_prompts(self, *, now: float | None = None) -> None:
        current = self.time_func() if now is None else now
        expired = [
            digest
            for digest, seen_at in self.origin_prompts.items()
            if current - seen_at > self.origin_prompt_ttl_seconds
        ]
        for digest in expired:
            self.origin_prompts.pop(digest, None)

    def mark_recent_discord_origin_prompt(
        self,
        target_thread_id: str | None,
        prompt: str,
        *,
        now: float | None = None,
    ) -> None:
        current = self.time_func() if now is None else now
        self.cleanup_recent_discord_origin_prompts(now=current)
        digest = make_discord_origin_prompt_digest(self.target_key(target_thread_id), prompt)
        self.origin_prompts[digest] = current

    def should_skip_discord_origin_prompt(
        self,
        target_thread_id: str | None,
        text: str,
        *,
        now: float | None = None,
    ) -> bool:
        current = self.time_func() if now is None else now
        self.cleanup_recent_discord_origin_prompts(now=current)
        digest = make_discord_origin_prompt_digest(self.target_key(target_thread_id), text)
        if digest not in self.origin_prompts:
            return False
        self.origin_prompts.pop(digest, None)
        return True

    def remember_recent_session_text(
        self,
        seen: MutableMapping[str, float],
        text: str,
        *,
        now: float | None = None,
    ) -> None:
        current = self.time_func() if now is None else now
        expired = [
            digest
            for digest, seen_at in seen.items()
            if current - seen_at > self.recent_text_ttl_seconds
        ]
        for digest in expired:
            seen.pop(digest, None)
        seen[make_text_digest(text.strip())] = current

    def has_recent_session_text(
        self,
        seen: MutableMapping[str, float],
        text: str,
        *,
        now: float | None = None,
    ) -> bool:
        current = self.time_func() if now is None else now
        digest = make_text_digest(text.strip())
        seen_at = seen.get(digest)
        if seen_at is None:
            return False
        if current - seen_at > self.recent_text_ttl_seconds:
            seen.pop(digest, None)
            return False
        return True

    def collect_session_mirror_items(
        self,
        codex_thread_id: str,
        events: list[dict],
        *,
        seen_agent_messages: MutableMapping[str, float],
        seen_user_messages: MutableMapping[str, float],
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
                    if phase == "final_answer":
                        continue
                    text = str(payload.get("message") or "").strip()
                    if not text:
                        continue
                    self.remember_recent_session_text(seen_agent_messages, text)
                    items.append(
                        make_session_mirror_item(
                            codex_thread_id,
                            event,
                            kind="commentary",
                            role="assistant",
                            phase=phase,
                            text=text,
                        )
                    )
                    continue
                if payload_type == "user_message":
                    text = str(payload.get("message") or "").strip()
                    if not text:
                        continue
                    if self.should_skip_discord_origin_prompt(codex_thread_id, text):
                        self.remember_recent_session_text(seen_user_messages, text)
                        continue
                    if self.has_recent_session_text(seen_user_messages, text):
                        continue
                    self.remember_recent_session_text(seen_user_messages, text)
                    items.append(
                        make_session_mirror_item(
                            codex_thread_id,
                            event,
                            kind="user",
                            role="user",
                            phase="input",
                            text=text,
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
                        )
                    )
                    continue
                continue

            if event_type != "response_item":
                continue

            if payload_type == "function_call":
                notice = self.make_interactive_notice_func(payload)
                if notice:
                    items.append(
                        make_session_mirror_item(
                            codex_thread_id,
                            event,
                            kind="interactive",
                            role="assistant",
                            phase="interactive",
                            text=notice,
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
                        )
                    )
                continue

            if payload_type != "message":
                continue
            text = self.extract_message_text_func(payload)
            if not text:
                continue
            role = str(payload.get("role") or "?")
            phase = str(payload.get("phase") or "")
            if role == "assistant" and phase == "commentary":
                if self.has_recent_session_text(seen_agent_messages, text):
                    continue
                self.remember_recent_session_text(seen_agent_messages, text)
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="commentary",
                        role=role,
                        phase=phase,
                        text=text,
                    )
                )
                continue
            if role == "assistant" and phase == "final_answer":
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="final",
                        role=role,
                        phase=phase,
                        text=text,
                    )
                )
                continue
            if role == "user":
                if self.should_skip_discord_origin_prompt(codex_thread_id, text):
                    self.remember_recent_session_text(seen_user_messages, text)
                    continue
                if self.has_recent_session_text(seen_user_messages, text):
                    continue
                self.remember_recent_session_text(seen_user_messages, text)
                items.append(
                    make_session_mirror_item(
                        codex_thread_id,
                        event,
                        kind="user",
                        role=role,
                        phase=phase or "input",
                        text=text,
                    )
                )
        return items
