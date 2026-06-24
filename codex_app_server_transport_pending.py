from __future__ import annotations

from collections import deque
from typing import Callable, Final, final

from codex_app_server_transport_replies import JsonMapping, JsonObject
from codex_app_server_transport_threads import extract_thread_id, extract_turn_id


LogFunc = Callable[[str], None]

APPROVAL_REQUEST_METHODS: Final = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "execCommandApproval",
        "applyPatchApproval",
    }
)
INPUT_REQUEST_METHOD: Final = "item/tool/requestUserInput"


def _params(message: JsonObject) -> JsonMapping | None:
    params = message.get("params")
    return params if isinstance(params, dict) else None


@final
class PendingRequestState:
    __slots__ = (
        "active_turns",
        "notifications",
        "server_request_order",
        "server_requests",
    )

    def __init__(self) -> None:
        self.notifications: deque[JsonObject] = deque(maxlen=1000)
        self.active_turns: dict[str, str] = {}
        self.server_requests: dict[str, JsonObject] = {}
        self.server_request_order: deque[str] = deque(maxlen=500)

    def clear(self) -> None:
        self.notifications.clear()
        self.active_turns.clear()
        self.server_requests.clear()
        self.server_request_order.clear()

    def record_server_request(self, request_id: str, message: JsonObject, log: LogFunc) -> None:
        self.server_requests[request_id] = message
        self.server_request_order.append(request_id)
        method = str(message.get("method") or "")
        params = _params(message)
        thread_id = extract_thread_id(params) if params is not None else ""
        log(
            f"app_server_request_pending id={request_id} method={method or '-'} "
            + f"target={thread_id or '-'}"
        )

    def record_notification(self, message: JsonObject, log: LogFunc) -> None:
        self.notifications.append(message)
        method = str(message.get("method") or "")
        params = _params(message)
        if params is None:
            return
        if method == "turn/started":
            thread_id = extract_thread_id(params)
            turn_id = extract_turn_id(params)
            if thread_id and turn_id:
                self.active_turns[thread_id] = turn_id
                log(f"app_server_turn_started target={thread_id} turn={turn_id}")
        elif method == "turn/completed":
            thread_id = extract_thread_id(params)
            turn_id = extract_turn_id(params)
            if thread_id and self.active_turns.get(thread_id) == turn_id:
                _ = self.active_turns.pop(thread_id, None)
                log(f"app_server_turn_completed target={thread_id} turn={turn_id or '-'}")

    def resolve_request(self, request_id: str) -> None:
        _ = self.server_requests.pop(request_id, None)

    def pending_requests(self, thread_id: str | None = None) -> list[JsonObject]:
        requests = [
            self.server_requests[request_id]
            for request_id in self.server_request_order
            if request_id in self.server_requests
        ]
        if not thread_id:
            return list(requests)
        result: list[JsonObject] = []
        for request in requests:
            params = _params(request)
            if params is not None and extract_thread_id(params) == thread_id:
                result.append(request)
        return result

    def latest_approval_request(self, thread_id: str) -> JsonObject | None:
        for request in reversed(self.pending_requests(thread_id)):
            if str(request.get("method") or "") in APPROVAL_REQUEST_METHODS:
                return request
        return None

    def latest_input_request(self, thread_id: str) -> JsonObject | None:
        for request in reversed(self.pending_requests(thread_id)):
            if str(request.get("method") or "") == INPUT_REQUEST_METHOD:
                return request
        return None

    def active_turn_id(self, thread_id: str) -> str | None:
        return self.active_turns.get(thread_id)
