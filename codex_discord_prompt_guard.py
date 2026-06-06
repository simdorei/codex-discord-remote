"""Prompt deduplication helpers for app/Discord bridge delivery."""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class RecentAppPromptGuardConfig:
    max_age_seconds: float
    scan_bytes: int
    recheck_seconds: float


def parse_session_event_timestamp(event: dict) -> datetime | None:
    raw_timestamp = str(event.get("timestamp") or "").strip()
    if not raw_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_user_text_from_session_event(
    event: dict,
    *,
    extract_message_text_func: Callable[[dict], str],
) -> str:
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return ""
    if event.get("type") == "event_msg" and payload.get("type") == "user_message":
        return str(payload.get("message") or "").strip()
    if event.get("type") != "response_item":
        return ""
    if payload.get("type") != "message" or payload.get("role") != "user":
        return ""
    return extract_message_text_func(payload).strip()


def iter_recent_session_tail_events(
    session_path: Path,
    *,
    scan_bytes: int,
) -> list[dict]:
    if not session_path.exists():
        return []
    size = session_path.stat().st_size
    start = max(0, size - max(1, scan_bytes))
    with session_path.open("rb") as handle:
        handle.seek(start)
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    events: list[dict] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


class RecentAppPromptGuard:
    def __init__(
        self,
        *,
        config: RecentAppPromptGuardConfig,
        choose_thread_func,
        normalize_prompt_text_func: Callable[[str], str],
        extract_message_text_func: Callable[[dict], str],
        log_func: Callable[[str], None],
        format_log_text_len_func: Callable[[str], object],
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.choose_thread = choose_thread_func
        self.normalize_prompt_text = normalize_prompt_text_func
        self.extract_message_text = extract_message_text_func
        self.log = log_func
        self.format_log_text_len = format_log_text_len_func
        self.now = now_func or (lambda: datetime.now(timezone.utc))

    def has_recent_user_prompt(self, target_thread_id: str | None, prompt: str) -> bool:
        normalized_prompt = self.normalize_prompt_text(prompt)
        if not normalized_prompt:
            return False
        try:
            thread = self.choose_thread(target_thread_id, None)
        except Exception:
            self.log(
                f"recent_codex_prompt_dedupe_unavailable target={target_thread_id or '-'} "
                "reason=choose_thread_failed\n" + traceback.format_exc()
            )
            return False
        session_path = Path(thread.rollout_path)
        current = self.now()
        for event in reversed(
            iter_recent_session_tail_events(
                session_path,
                scan_bytes=self.config.scan_bytes,
            )
        ):
            user_text = extract_user_text_from_session_event(
                event,
                extract_message_text_func=self.extract_message_text,
            )
            if not user_text:
                continue
            timestamp = parse_session_event_timestamp(event)
            if timestamp is None:
                continue
            age_seconds = (current - timestamp).total_seconds()
            if age_seconds < 0:
                age_seconds = 0
            if age_seconds > self.config.max_age_seconds:
                return False
            if self.normalize_prompt_text(user_text) == normalized_prompt:
                return True
        return False

    async def wait_for_user_prompt(
        self,
        target_thread_id: str | None,
        prompt: str,
        *,
        to_thread_func=asyncio.to_thread,
        sleep_func=asyncio.sleep,
    ) -> bool:
        if not target_thread_id:
            return False
        if await to_thread_func(self.has_recent_user_prompt, target_thread_id, prompt):
            return True
        delay_seconds = self.config.recheck_seconds
        if delay_seconds <= 0:
            return False
        await sleep_func(delay_seconds)
        if await to_thread_func(self.has_recent_user_prompt, target_thread_id, prompt):
            self.log(
                f"recent_codex_prompt_dedupe_recheck_hit target={target_thread_id} "
                f"delay={delay_seconds:g} prompt_len={self.format_log_text_len(prompt)}"
            )
            return True
        return False
