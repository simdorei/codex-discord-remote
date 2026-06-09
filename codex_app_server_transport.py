"""Persistent Codex app-server transport for Discord delivery."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import codex_desktop_bridge as bridge


LogFunc = Callable[[str], None]


class CodexAppServerTransportError(RuntimeError):
    pass


@dataclass
class AppServerDeliveryResult:
    exit_code: int
    output: str
    thread_id: str | None = None
    turn_id: str | None = None
    target_ref: str = ""
    session_path: str | None = None
    start_offset: int | None = None
    delivery_pending: bool = False
    transport: str = "resident-app-server"


class PersistentCodexAppServer:
    """Single-process JSONL client for `codex app-server`.

    The Discord bot calls this object from worker threads. Requests are
    serialized, while a reader thread continuously drains stdout so streamed
    notifications cannot block the app-server process.
    """

    def __init__(
        self,
        *,
        executable_resolver: Callable[[], str] = bridge.resolve_codex_app_server_executable,
        log_func: LogFunc | None = None,
    ) -> None:
        self.executable_resolver = executable_resolver
        self.log_func = log_func
        self.process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._responses: dict[str, dict] = {}
        self._notifications: deque[dict] = deque(maxlen=1000)
        self._active_turns: dict[str, str] = {}
        self._server_requests: dict[str, dict] = {}
        self._server_request_order: deque[str] = deque(maxlen=500)
        self._closed_error: str | None = None
        self._initialized = False
        self._started_at = 0.0

    def _log(self, text: str) -> None:
        if self.log_func is not None:
            self.log_func(text)

    def start(self) -> None:
        with self._lock:
            if self._is_running() and self._initialized:
                return
            self.close_locked()
            executable = self.executable_resolver()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self.process = subprocess.Popen(
                    [executable, "app-server"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise CodexAppServerTransportError(
                    "Failed to start resident Codex app-server. "
                    f"executable={executable!r}"
                ) from exc

            if self.process.stdin is None or self.process.stdout is None:
                self.close_locked()
                raise CodexAppServerTransportError("Resident Codex app-server stdio is unavailable.")

            self._responses.clear()
            self._notifications.clear()
            self._active_turns.clear()
            self._server_requests.clear()
            self._server_request_order.clear()
            self._closed_error = None
            self._initialized = False
            self._started_at = time.time()
            self._stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
            self._stdout_thread.start()

        self._request_started(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-discord-harness",
                    "title": "Codex Discord Harness",
                    "version": "1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout_sec=8.0,
        )
        self.notify("initialized", {})
        with self._lock:
            self._initialized = True
        self._log(f"app_server_transport_started executable={executable}")

    def _is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _drain_stdout(self) -> None:
        try:
            process = self.process
            stdout = process.stdout if process is not None else None
            if stdout is None:
                return
            for raw_line in stdout:
                self._handle_raw_line(raw_line)
        except Exception as exc:
            with self._condition:
                self._closed_error = f"reader failed: {exc}"
                self._condition.notify_all()
        finally:
            with self._condition:
                if self._closed_error is None:
                    self._closed_error = "app-server exited"
                self._condition.notify_all()

    def _handle_raw_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._log(f"app_server_transport_non_json line={line[:200]!r}")
            return
        if not isinstance(message, dict):
            return
        message_id = message.get("id")
        with self._condition:
            if message_id is not None and "method" in message and "result" not in message and "error" not in message:
                self._record_server_request_locked(str(message_id), message)
                self._condition.notify_all()
            elif message_id is not None:
                self._responses[str(message_id)] = message
                self._condition.notify_all()
            else:
                self._record_notification_locked(message)

    def _record_server_request_locked(self, request_id: str, message: dict) -> None:
        self._server_requests[request_id] = message
        self._server_request_order.append(request_id)
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        thread_id = _extract_thread_id(params) if isinstance(params, dict) else ""
        self._log(
            f"app_server_request_pending id={request_id} method={method or '-'} "
            f"target={thread_id or '-'}"
        )

    def _record_notification_locked(self, message: dict) -> None:
        self._notifications.append(message)
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return
        if method == "turn/started":
            thread_id = _extract_thread_id(params)
            turn_id = _extract_turn_id(params)
            if thread_id and turn_id:
                self._active_turns[thread_id] = turn_id
                self._log(f"app_server_turn_started target={thread_id} turn={turn_id}")
        elif method == "turn/completed":
            thread_id = _extract_thread_id(params)
            turn_id = _extract_turn_id(params)
            if thread_id and self._active_turns.get(thread_id) == turn_id:
                self._active_turns.pop(thread_id, None)
                self._log(f"app_server_turn_completed target={thread_id} turn={turn_id or '-'}")

    def close_locked(self) -> None:
        process = self.process
        self.process = None
        self._initialized = False
        if process is None:
            return
        stdin = process.stdin
        if stdin is not None and not stdin.closed:
            try:
                stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def close(self) -> None:
        with self._condition:
            self.close_locked()
            self._closed_error = "app-server closed"
            self._condition.notify_all()

    def restart(self) -> None:
        with self._condition:
            self.close_locked()
            self._closed_error = "app-server restarting"
            self._condition.notify_all()
        self.start()

    def request(self, method: str, params: dict | None = None, *, timeout_sec: float = 10.0) -> dict:
        self.start()
        return self._request_started(method, params or {}, timeout_sec=timeout_sec)

    def _request_started(self, method: str, params: dict, *, timeout_sec: float) -> dict:
        with self._request_lock:
            if not self._is_running():
                raise CodexAppServerTransportError("Resident Codex app-server is not running.")
            request_id = str(uuid.uuid4())
            payload = {
                "id": request_id,
                "method": method,
                "params": params,
            }
            self._write_message(payload)
            deadline = time.time() + max(timeout_sec, 0.0)
            with self._condition:
                while True:
                    response = self._responses.pop(request_id, None)
                    if response is not None:
                        if "error" in response:
                            error = response.get("error") or {}
                            if isinstance(error, dict):
                                detail = str(error.get("message") or error)
                            else:
                                detail = str(error)
                            raise CodexAppServerTransportError(f"{method} failed: {detail}")
                        result = response.get("result") or {}
                        if not isinstance(result, dict):
                            raise CodexAppServerTransportError(f"{method} returned an invalid payload.")
                        return result
                    if self._closed_error and not self._is_running():
                        raise CodexAppServerTransportError(
                            f"Codex app-server exited while waiting for {method}: {self._closed_error}"
                        )
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise TimeoutError(f"Timed out waiting for app-server response to {method}.")
                    self._condition.wait(timeout=min(remaining, 0.5))

    def notify(self, method: str, params: dict | None = None) -> None:
        self._write_message({"method": method, "params": params or {}})

    def _write_message(self, payload: dict) -> None:
        process = self.process
        stdin = process.stdin if process is not None else None
        if stdin is None or stdin.closed:
            raise CodexAppServerTransportError("Resident Codex app-server stdin is closed.")
        with self._write_lock:
            stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stdin.flush()

    def respond_to_server_request(self, request_id: str, result: dict) -> None:
        if not self._is_running():
            raise CodexAppServerTransportError("Cannot answer app-server request because the server is not running.")
        self._write_message({"id": request_id, "result": result})
        with self._condition:
            self._server_requests.pop(request_id, None)
        self._log(f"app_server_request_resolved id={request_id}")

    def get_pending_server_requests(self, thread_id: str | None = None) -> list[dict]:
        with self._lock:
            requests = [
                self._server_requests[request_id]
                for request_id in self._server_request_order
                if request_id in self._server_requests
            ]
        if not thread_id:
            return list(requests)
        result: list[dict] = []
        for request in requests:
            params = request.get("params") or {}
            if isinstance(params, dict) and _extract_thread_id(params) == thread_id:
                result.append(request)
        return result

    def get_latest_pending_approval_request(self, thread_id: str) -> dict | None:
        approval_methods = {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "execCommandApproval",
            "applyPatchApproval",
        }
        for request in reversed(self.get_pending_server_requests(thread_id)):
            if str(request.get("method") or "") in approval_methods:
                return request
        return None

    def get_latest_pending_input_request(self, thread_id: str) -> dict | None:
        for request in reversed(self.get_pending_server_requests(thread_id)):
            if str(request.get("method") or "") == "item/tool/requestUserInput":
                return request
        return None

    def reply_to_pending_approval(self, thread_id: str, answer_text: str) -> dict:
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

    def reply_to_pending_input(self, thread_id: str, answer_text: str) -> dict:
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
        return {
            "request_id": request_id,
            "answers_by_question": answers_by_question,
            "verification_busy_state": "submitted",
        }

    def read_thread(self, thread_id: str, *, include_turns: bool = False) -> dict:
        return self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
            timeout_sec=8.0,
        )

    def resume_thread(self, thread_id: str) -> dict:
        return self.request("thread/resume", {"threadId": thread_id}, timeout_sec=10.0)

    def start_turn(self, thread_id: str, prompt: str) -> dict:
        return self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt, "text_elements": []}],
            },
            timeout_sec=12.0,
        )

    def steer_turn(self, thread_id: str, prompt: str, *, expected_turn_id: str) -> dict:
        return self.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": expected_turn_id,
                "input": [{"type": "text", "text": prompt, "text_elements": []}],
            },
            timeout_sec=10.0,
        )

    def interrupt_turn(self, thread_id: str, turn_id: str) -> dict:
        return self.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout_sec=10.0,
        )

    def get_active_turn_id(self, thread_id: str) -> str | None:
        with self._lock:
            turn_id = self._active_turns.get(thread_id)
            if turn_id:
                return turn_id
        try:
            payload = self.read_thread(thread_id, include_turns=True).get("thread") or {}
        except Exception:
            return None
        return get_in_progress_turn_id(payload)


def get_in_progress_turn_id(thread_payload: dict) -> str | None:
    turns = thread_payload.get("turns") or []
    if not isinstance(turns, list):
        return None
    for turn in reversed(turns):
        if not isinstance(turn, dict):
            continue
        turn_id = str(turn.get("id") or "").strip()
        status = str(turn.get("status") or "").strip()
        if turn_id and status == "inProgress":
            return turn_id
    return None


def parse_approval_answer(answer_text: str) -> tuple[str, str, str]:
    normalized = str(answer_text).strip()
    lowered = normalized.lower()
    if normalized == "1" or lowered in {"approve", "approved", "accept", "yes", "y", "ok", "예", "네", "승인"}:
        return "accept", "approved", "turn"
    if normalized == "2" or lowered in {"approve session", "accept session", "session", "approve_for_session"}:
        return "acceptForSession", "approved_for_session", "session"
    if normalized == "3" or lowered in {"decline", "reject", "no", "n", "아니요", "거절"}:
        return "decline", "denied", "turn"
    if lowered in {"cancel", "skip", "dismiss", "건너뛰기", "취소"}:
        return "cancel", "abort", "turn"
    raise CodexAppServerTransportError(
        "Unrecognized approval reply. Use 1 to approve, 2 to approve for this session, "
        "3 to decline, or cancel to skip."
    )


def build_approval_response(method: str, params: dict, answer_text: str) -> tuple[dict, str]:
    decision, legacy_decision, scope = parse_approval_answer(answer_text)
    if method == "item/commandExecution/requestApproval":
        return {"decision": decision}, decision
    if method == "item/fileChange/requestApproval":
        if decision == "acceptForSession":
            return {"decision": "acceptForSession"}, decision
        return {"decision": decision}, decision
    if method == "item/permissions/requestApproval":
        if decision in {"accept", "acceptForSession"}:
            permissions = params.get("permissions")
            if not isinstance(permissions, dict):
                permissions = {"network": None, "fileSystem": None}
            return {"permissions": permissions, "scope": scope}, decision
        return {
            "permissions": {"network": None, "fileSystem": None},
            "scope": "turn",
            "strictAutoReview": False,
        }, decision
    if method in {"execCommandApproval", "applyPatchApproval"}:
        return {"decision": legacy_decision}, legacy_decision
    raise CodexAppServerTransportError(f"Unsupported app-server approval request method: {method}")


def split_input_values(raw_value: str) -> list[str]:
    values = [part.strip() for part in str(raw_value).split("|")]
    return [value for value in values if value]


def resolve_input_answers(question: dict, raw_value: str) -> list[str]:
    values = split_input_values(raw_value)
    if not values:
        raise CodexAppServerTransportError("Answer text was empty.")
    options = question.get("options") or []
    labels = [
        str(option.get("label") or "").strip()
        for option in options
        if isinstance(option, dict) and str(option.get("label") or "").strip()
    ]
    resolved: list[str] = []
    for value in values:
        if labels and value.isdigit():
            index = int(value) - 1
            if 0 <= index < len(labels):
                resolved.append(labels[index])
                continue
        matched = next((label for label in labels if label.lower() == value.lower()), None)
        resolved.append(matched or value)
    return resolved


def build_input_response(params: dict, answer_text: str) -> tuple[dict, dict[str, list[str]]]:
    questions = params.get("questions") or []
    if not isinstance(questions, list) or not questions:
        raise CodexAppServerTransportError("No pending input questions were available to answer.")
    question_map: dict[str, dict] = {}
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise CodexAppServerTransportError(f"Pending input question {index} was invalid.")
        question_id = str(question.get("id") or "").strip()
        if not question_id:
            raise CodexAppServerTransportError(f"Pending input question {index} did not include an id.")
        question_map[question_id] = question

    normalized = str(answer_text).strip()
    if not normalized:
        raise CodexAppServerTransportError("Answer text was empty.")

    answers_by_question: dict[str, list[str]] = {}
    if len(question_map) == 1 and "=" not in normalized:
        only_question_id = next(iter(question_map))
        answers_by_question[only_question_id] = resolve_input_answers(
            question_map[only_question_id],
            normalized,
        )
    else:
        assignments: dict[str, str] = {}
        for segment in normalized.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            if "=" not in segment:
                raise CodexAppServerTransportError(
                    "Multi-question replies must use question_id=value; other_id=value format."
                )
            question_id, raw_value = segment.split("=", 1)
            question_id = question_id.strip()
            raw_value = raw_value.strip()
            if not question_id:
                raise CodexAppServerTransportError("A reply_input assignment was missing the question id.")
            assignments[question_id] = raw_value
        missing = [question_id for question_id in question_map if question_id not in assignments]
        if missing:
            raise CodexAppServerTransportError("Missing answers for question ids: " + ", ".join(missing))
        unknown = [question_id for question_id in assignments if question_id not in question_map]
        if unknown:
            raise CodexAppServerTransportError("Unknown question ids: " + ", ".join(unknown))
        for question_id, raw_value in assignments.items():
            answers_by_question[question_id] = resolve_input_answers(
                question_map[question_id],
                raw_value,
            )

    return {
        "answers": {
            question_id: {"answers": values}
            for question_id, values in answers_by_question.items()
        }
    }, answers_by_question


def _extract_thread_id(params: dict) -> str:
    for key in ("threadId", "conversationId"):
        value = str(params.get(key) or "").strip()
        if value:
            return value
    thread = params.get("thread")
    if isinstance(thread, dict):
        return str(thread.get("id") or "").strip()
    turn = params.get("turn")
    if isinstance(turn, dict):
        return str(turn.get("threadId") or turn.get("conversationId") or "").strip()
    return ""


def _extract_turn_id(params: dict) -> str:
    for key in ("turnId", "id"):
        value = str(params.get(key) or "").strip()
        if value:
            return value
    turn = params.get("turn")
    if isinstance(turn, dict):
        return str(turn.get("id") or "").strip()
    return ""


def get_thread_status_type(thread_payload: dict) -> str:
    status = thread_payload.get("status") or {}
    if isinstance(status, dict):
        return str(status.get("type") or "").strip()
    return ""


def ensure_thread_loaded(client: PersistentCodexAppServer, thread_id: str) -> dict:
    thread_payload = (client.read_thread(thread_id, include_turns=False).get("thread") or {})
    if get_thread_status_type(thread_payload) != "notLoaded":
        return thread_payload
    resumed = client.resume_thread(thread_id)
    resumed_thread = resumed.get("thread") or {}
    if not isinstance(resumed_thread, dict):
        raise CodexAppServerTransportError("thread/resume did not return a thread payload.")
    return resumed_thread


def _delivery_context(
    target_thread_id: str | None,
    *,
    bridge_module=bridge,
) -> tuple[object, str, dict[str, tuple[object, Path, int]]]:
    thread = bridge_module.choose_thread(target_thread_id, None)
    target_ref = bridge_module.get_thread_workspace_ref(thread)
    recent_offsets = bridge_module.snapshot_recent_session_offsets(
        limit=10,
        include_threads=[thread],
    )
    return thread, target_ref, recent_offsets


def _make_output(
    *,
    thread: object,
    target_ref: str,
    method: str,
    turn_id: str | None,
    delivered_thread: object | None,
    delivery_pending: bool,
    bridge_module=bridge,
) -> str:
    lines = [
        f"target_thread: {getattr(thread, 'id', '')}",
        f"title: {bridge_module.format_title_preview(getattr(thread, 'title', ''))}",
        f"cwd: {getattr(thread, 'cwd', '')}",
        f"transport: resident-app-server {method}",
    ]
    if turn_id:
        lines.append(f"[app_server_delivery] turn_id={turn_id}")
    if delivered_thread is not None:
        lines.append(f"[delivery_verified] {bridge_module.get_thread_label(delivered_thread)}")
    elif delivery_pending:
        lines.append("[delivery_pending]")
        lines.append(
            "Codex app-server accepted the request, but local session recording was not confirmed before the deadline."
        )
        lines.append("Discord will keep watching the mapped session for the next Codex reply.")
    if target_ref:
        lines.append(f"thread_ref: {target_ref}")
    return "\n".join(lines)


def _wait_for_delivery(
    recent_offsets: dict[str, tuple[object, Path, int]],
    prompt: str,
    *,
    bridge_module=bridge,
    timeout_sec: float,
) -> object | None:
    if timeout_sec <= 0:
        return None
    return bridge_module.wait_for_prompt_delivery(
        recent_offsets,
        prompt,
        timeout_sec=timeout_sec,
    )


def start_turn_no_wait(
    client: PersistentCodexAppServer,
    prompt: str,
    target_thread_id: str | None,
    *,
    bridge_module=bridge,
    confirm_timeout_sec: float = 6.0,
) -> AppServerDeliveryResult:
    thread, target_ref, recent_offsets = _delivery_context(
        target_thread_id,
        bridge_module=bridge_module,
    )
    session_path = Path(getattr(thread, "rollout_path", ""))
    start_offset = session_path.stat().st_size if session_path.exists() else None
    ensure_thread_loaded(client, thread.id)
    result = client.start_turn(thread.id, prompt)
    turn = result.get("turn") or {}
    turn_id = str(turn.get("id") or "").strip() or None
    delivered_thread = _wait_for_delivery(
        recent_offsets,
        prompt,
        bridge_module=bridge_module,
        timeout_sec=confirm_timeout_sec,
    )
    if delivered_thread is not None and getattr(delivered_thread, "id", None) != thread.id:
        return AppServerDeliveryResult(
            1,
            "Prompt landed in a different thread after app-server delivery. "
            f"Expected {bridge_module.get_thread_label(thread)}, "
            f"but it was recorded in {bridge_module.get_thread_label(delivered_thread)}.",
            thread_id=thread.id,
            turn_id=turn_id,
            target_ref=target_ref,
            session_path=str(session_path) if session_path else None,
            start_offset=start_offset,
        )
    delivery_pending = delivered_thread is None
    output = _make_output(
        thread=thread,
        target_ref=target_ref,
        method="turn/start",
        turn_id=turn_id,
        delivered_thread=delivered_thread,
        delivery_pending=delivery_pending,
        bridge_module=bridge_module,
    )
    return AppServerDeliveryResult(
        0,
        output,
        thread_id=thread.id,
        turn_id=turn_id,
        target_ref=target_ref,
        session_path=str(session_path) if session_path else None,
        start_offset=start_offset,
        delivery_pending=delivery_pending,
    )


def steer_or_start_no_wait(
    client: PersistentCodexAppServer,
    prompt: str,
    target_thread_id: str | None,
    *,
    bridge_module=bridge,
    confirm_timeout_sec: float = 6.0,
) -> AppServerDeliveryResult:
    thread, target_ref, recent_offsets = _delivery_context(
        target_thread_id,
        bridge_module=bridge_module,
    )
    session_path = Path(getattr(thread, "rollout_path", ""))
    start_offset = session_path.stat().st_size if session_path.exists() else None
    ensure_thread_loaded(client, thread.id)
    active_turn_id = client.get_active_turn_id(thread.id)
    method = "turn/steer" if active_turn_id else "turn/start"
    if active_turn_id:
        result = client.steer_turn(thread.id, prompt, expected_turn_id=active_turn_id)
        turn_id = active_turn_id
        turn = result.get("turn")
        if isinstance(turn, dict):
            turn_id = str(turn.get("id") or turn_id).strip() or turn_id
    else:
        result = client.start_turn(thread.id, prompt)
        turn = result.get("turn") or {}
        turn_id = str(turn.get("id") or "").strip() or None
    delivered_thread = _wait_for_delivery(
        recent_offsets,
        prompt,
        bridge_module=bridge_module,
        timeout_sec=confirm_timeout_sec,
    )
    if delivered_thread is not None and getattr(delivered_thread, "id", None) != thread.id:
        return AppServerDeliveryResult(
            1,
            "Prompt landed in a different thread after app-server delivery. "
            f"Expected {bridge_module.get_thread_label(thread)}, "
            f"but it was recorded in {bridge_module.get_thread_label(delivered_thread)}.",
            thread_id=thread.id,
            turn_id=turn_id,
            target_ref=target_ref,
            session_path=str(session_path) if session_path else None,
            start_offset=start_offset,
        )
    delivery_pending = delivered_thread is None
    output = _make_output(
        thread=thread,
        target_ref=target_ref,
        method=method,
        turn_id=turn_id,
        delivered_thread=delivered_thread,
        delivery_pending=delivery_pending,
        bridge_module=bridge_module,
    )
    return AppServerDeliveryResult(
        0,
        output,
        thread_id=thread.id,
        turn_id=turn_id,
        target_ref=target_ref,
        session_path=str(session_path) if session_path else None,
        start_offset=start_offset,
        delivery_pending=delivery_pending,
    )


DEFAULT_CLIENT = PersistentCodexAppServer()
