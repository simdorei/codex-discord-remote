from __future__ import annotations

from codex_desktop_bridge_final_answer_types import (
    FinalAnswerWatchDeps,
    JsonObject,
    StreamCallback,
    WatchForFinalAnswerResult,
    WatchState,
    append_commentary,
    result_from_state,
)
from codex_desktop_bridge_final_answer_response_items import process_response_item
from codex_session_events import JsonEvent


def process_watch_event(
    event: JsonEvent,
    *,
    include_commentary: bool,
    stream_live: bool,
    stream_label: str,
    stream_callback: StreamCallback | None,
    deps: FinalAnswerWatchDeps,
    state: WatchState,
) -> WatchForFinalAnswerResult | None:
    payload = _event_payload(event)
    if payload is None:
        return None

    event_type = str(event.get("type") or "")
    payload_type = str(payload.get("type") or "")

    if event_type == "event_msg":
        return _process_event_msg(
            payload_type,
            payload,
            include_commentary=include_commentary,
            stream_live=stream_live,
            stream_label=stream_label,
            stream_callback=stream_callback,
            deps=deps,
            state=state,
        )

    if event_type == "response_item":
        return process_response_item(
            payload_type,
            payload,
            include_commentary=include_commentary,
            stream_live=stream_live,
            stream_label=stream_label,
            stream_callback=stream_callback,
            deps=deps,
            state=state,
        )

    return None


def _process_event_msg(
    payload_type: str,
    payload: JsonObject,
    *,
    include_commentary: bool,
    stream_live: bool,
    stream_label: str,
    stream_callback: StreamCallback | None,
    deps: FinalAnswerWatchDeps,
    state: WatchState,
) -> WatchForFinalAnswerResult | None:
    if payload_type == "agent_message":
        phase = str(payload.get("phase") or "")
        if phase == "final_answer":
            return None
        message = str(payload.get("message") or "").strip()
        state.did_stream_live = append_commentary(
            message,
            include_commentary=include_commentary,
            commentary=state.commentary,
            dedupe=state.seen_agent_messages,
            stream_live=stream_live,
            stream_label=stream_label,
            stream_callback=stream_callback,
            deps=deps,
            did_stream_live=state.did_stream_live,
        )
        return None

    if payload_type in {"turn_aborted", "task_aborted", "task_cancelled"}:
        return result_from_state("aborted", state)

    return None


def _event_payload(event: JsonEvent) -> JsonObject | None:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return None
