"""Persistent Codex app-server transport for Discord delivery."""

from __future__ import annotations

from typing import final

from codex_app_server_transport_delivery import (
    AppServerDeliveryResult as AppServerDeliveryResult,
    start_turn_no_wait as start_turn_no_wait,
    steer_or_start_no_wait as steer_or_start_no_wait,
)
from codex_app_server_transport_replies import (
    CodexAppServerTransportError as CodexAppServerTransportError,
    JsonArray,
    JsonObject,
    build_approval_response as build_approval_response,
    build_input_response as build_input_response,
    parse_approval_answer as parse_approval_answer,
    resolve_input_answers as resolve_input_answers,
    split_input_values as split_input_values,
)
from codex_app_server_transport_resident import ResidentCodexAppServerTransport
from codex_app_server_transport_threads import (
    get_in_progress_turn_id as get_in_progress_turn_id,
)


__all__ = [
    "AppServerDeliveryResult",
    "CodexAppServerTransportError",
    "PersistentCodexAppServer",
    "build_approval_response",
    "build_input_response",
    "parse_approval_answer",
    "resolve_input_answers",
    "split_input_values",
]


@final
class PersistentCodexAppServer(ResidentCodexAppServerTransport):
    def reply_to_pending_approval(self, thread_id: str, answer_text: str) -> JsonObject:
        request = self.get_latest_pending_approval_request(thread_id)
        if request is None:
            raise CodexAppServerTransportError("No pending app-server approval request for this thread.")
        request_id = str(request.get("id") or "").strip()
        method = str(request.get("method") or "").strip()
        params = request.get("params") or {}
        if not request_id:
            raise CodexAppServerTransportError("Pending app-server approval request had no id.")
        if not isinstance(params, dict):
            params = {}
        result, decision_action = build_approval_response(method, params, answer_text)
        self.respond_to_server_request(request_id, result)
        return {
            "request_id": request_id,
            "request_kind": method,
            "decision_action": decision_action,
            "verification_busy_state": "submitted",
        }

    def reply_to_pending_input(self, thread_id: str, answer_text: str) -> JsonObject:
        request = self.get_latest_pending_input_request(thread_id)
        if request is None:
            raise CodexAppServerTransportError("No pending app-server input request for this thread.")
        request_id = str(request.get("id") or "").strip()
        params = request.get("params") or {}
        if not request_id:
            raise CodexAppServerTransportError("Pending app-server input request had no id.")
        if not isinstance(params, dict):
            params = {}
        response_payload, answers_by_question = build_input_response(params, answer_text)
        self.respond_to_server_request(request_id, response_payload)
        answers_json: JsonObject = {}
        for question_id, values in answers_by_question.items():
            answer_values: JsonArray = []
            answer_values.extend(values)
            answers_json[question_id] = answer_values
        return {
            "request_id": request_id,
            "answers_by_question": answers_json,
            "verification_busy_state": "submitted",
        }

    def read_thread(self, thread_id: str, *, include_turns: bool = False) -> JsonObject:
        return self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
            timeout_sec=8.0,
        )

    def resume_thread(self, thread_id: str) -> JsonObject:
        return self.request("thread/resume", {"threadId": thread_id}, timeout_sec=10.0)

    def start_turn(self, thread_id: str, prompt: str) -> JsonObject:
        return self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt, "text_elements": []}],
            },
            timeout_sec=12.0,
        )

    def steer_turn(self, thread_id: str, prompt: str, *, expected_turn_id: str) -> JsonObject:
        return self.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": expected_turn_id,
                "input": [{"type": "text", "text": prompt, "text_elements": []}],
            },
            timeout_sec=10.0,
        )

    def interrupt_turn(self, thread_id: str, turn_id: str) -> JsonObject:
        return self.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout_sec=10.0,
        )

    def get_active_turn_id(self, thread_id: str) -> str | None:
        with self._lock:
            turn_id = self._pending.active_turn_id(thread_id)
            if turn_id:
                return turn_id
        try:
            payload = self.read_thread(thread_id, include_turns=True).get("thread")
        except (CodexAppServerTransportError, OSError, TimeoutError) as exc:
            self._log(
                f"app_server_active_turn_read_failed thread={thread_id} "
                + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
            )
            return None
        if not isinstance(payload, dict):
            return None
        return get_in_progress_turn_id(payload)

    def get_active_turn_id_or_raise(self, thread_id: str) -> str | None:
        with self._lock:
            turn_id = self._pending.active_turn_id(thread_id)
            if turn_id:
                return turn_id
        payload = self.read_thread(thread_id, include_turns=True).get("thread")
        if not isinstance(payload, dict):
            return None
        return get_in_progress_turn_id(payload)


DEFAULT_CLIENT = PersistentCodexAppServer()
