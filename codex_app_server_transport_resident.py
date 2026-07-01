from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from typing import Callable, IO

from codex_app_server_transport_messages import classify_app_server_transport_line
from codex_app_server_transport_process import (
    close_resident_app_server_process,
    has_resident_app_server_stdio,
    start_resident_app_server_process,
)
import codex_desktop_bridge as bridge
from codex_app_server_transport_pending import PendingRequestState
from codex_app_server_transport_replies import (
    CodexAppServerTransportError,
    JsonMapping,
    JsonObject,
    JsonValue,
    extract_response_result,
)


LogFunc = Callable[[str], None]
_decode_json_value: Callable[[str], JsonValue] = json.loads


class ResidentCodexAppServerTransport:
    def __init__(
        self,
        *,
        executable_resolver: Callable[[], str] = bridge.resolve_codex_app_server_executable,
        log_func: LogFunc | None = None,
    ) -> None:
        self.executable_resolver: Callable[[], str] = executable_resolver
        self.log_func: LogFunc | None = log_func
        self.process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._lock: threading.RLock = threading.RLock()
        self._write_lock: threading.Lock = threading.Lock()
        self._request_lock: threading.Lock = threading.Lock()
        self._condition: threading.Condition = threading.Condition(self._lock)
        self._responses: dict[str, JsonObject] = {}
        self._pending: PendingRequestState = PendingRequestState()
        self._closed_error: str | None = None
        self._initialized: bool = False
        self._started_at: float = 0.0

    def _log(self, text: str) -> None:
        if self.log_func is not None:
            self.log_func(text)

    def start(self) -> None:
        with self._lock:
            if self._is_running() and self._initialized:
                return
            self.close_locked()
            executable = self.executable_resolver()
            self.process = start_resident_app_server_process(executable)
            if not has_resident_app_server_stdio(self.process):
                self.close_locked()
                raise CodexAppServerTransportError("Resident Codex app-server stdio is unavailable.")

            self._responses.clear()
            self._pending.clear()
            self._closed_error = None
            self._initialized = False
            self._started_at = time.time()
            self._stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
            self._stdout_thread.start()

        _ = self._request_started(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-discord-remote",
                    "title": "Codex Discord Remote",
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
            stdout: IO[str] | None = process.stdout if process is not None else None
            if stdout is None:
                return
            while True:
                raw_line = stdout.readline()
                if raw_line == "":
                    break
                self._handle_raw_line(raw_line)
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - reader thread reports failures through _closed_error.
            with self._condition:
                self._closed_error = f"reader failed: {exc}"
                self._condition.notify_all()
        finally:
            with self._condition:
                if self._closed_error is None:
                    self._closed_error = "app-server exited"
                self._condition.notify_all()

    def _handle_raw_line(self, raw_line: str) -> None:
        classified = classify_app_server_transport_line(raw_line, decode_json_value=_decode_json_value)
        if classified.kind == "empty":
            return
        if classified.kind == "invalid-json":
            self._log(f"app_server_transport_non_json line={classified.invalid_preview!r}")
            return
        message = classified.message
        if message is None:
            return
        message_id = classified.message_id
        with self._condition:
            if classified.kind == "server-request" and message_id is not None:
                self._pending.record_server_request(message_id, message, self._log)
                self._condition.notify_all()
            elif classified.kind == "response" and message_id is not None:
                self._responses[message_id] = message
                self._condition.notify_all()
            else:
                self._pending.record_notification(message, self._log)

    def close_locked(self) -> None:
        process = self.process
        self.process = None
        self._initialized = False
        if process is None:
            return
        close_resident_app_server_process(process, self._log)

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

    def request(self, method: str, params: JsonMapping | None = None, *, timeout_sec: float = 10.0) -> JsonObject:
        self.start()
        return self._request_started(method, params or {}, timeout_sec=timeout_sec)

    def _request_started(self, method: str, params: JsonMapping, *, timeout_sec: float) -> JsonObject:
        with self._request_lock:
            if not self._is_running():
                raise CodexAppServerTransportError("Resident Codex app-server is not running.")
            request_id = str(uuid.uuid4())
            payload: JsonObject = {
                "id": request_id,
                "method": method,
                "params": dict(params),
            }
            self._write_message(payload)
            deadline = time.time() + max(timeout_sec, 0.0)
            with self._condition:
                while True:
                    response = self._responses.pop(request_id, None)
                    if response is not None:
                        return extract_response_result(method, response)
                    if self._closed_error and not self._is_running():
                        raise CodexAppServerTransportError(
                            f"Codex app-server exited while waiting for {method}: {self._closed_error}"
                        )
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise TimeoutError(f"Timed out waiting for app-server response to {method}.")
                    _ = self._condition.wait(timeout=min(remaining, 0.5))

    def notify(self, method: str, params: JsonMapping | None = None) -> None:
        self._write_message({"method": method, "params": dict(params or {})})

    def _write_message(self, payload: JsonMapping) -> None:
        process = self.process
        stdin = process.stdin if process is not None else None
        if stdin is None or stdin.closed:
            raise CodexAppServerTransportError("Resident Codex app-server stdin is closed.")
        with self._write_lock:
            _ = stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stdin.flush()

    def respond_to_server_request(self, request_id: str, result: JsonMapping) -> None:
        if not self._is_running():
            raise CodexAppServerTransportError("Cannot answer app-server request because the server is not running.")
        self._write_message({"id": request_id, "result": dict(result)})
        with self._condition:
            self._pending.resolve_request(request_id)
        self._log(f"app_server_request_resolved id={request_id}")

    def get_pending_server_requests(self, thread_id: str | None = None) -> list[JsonObject]:
        with self._lock:
            return self._pending.pending_requests(thread_id)

    def get_latest_pending_approval_request(self, thread_id: str) -> JsonObject | None:
        with self._lock:
            return self._pending.latest_approval_request(thread_id)

    def get_latest_pending_input_request(self, thread_id: str) -> JsonObject | None:
        with self._lock:
            return self._pending.latest_input_request(thread_id)
