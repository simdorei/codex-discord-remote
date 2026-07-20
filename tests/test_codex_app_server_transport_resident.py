from __future__ import annotations

import io
import subprocess
import threading
import unittest
from collections.abc import Callable
from typing import IO, Final, cast, override
from unittest import mock

import codex_app_server_transport_process as process_mod
import codex_app_server_transport_resident as resident_mod
import codex_app_server_transport as transport_mod
from codex_app_server_transport_replies import (
    CodexAppServerTransportError,
    JsonMapping,
    JsonObject,
)
from codex_app_server_transport_resident import ResidentCodexAppServerTransport


class ResidentAppServerProcessHelperTests(unittest.TestCase):
    def test_process_helper_preserves_popen_arguments(self) -> None:
        process = _process()
        popen_calls: list[tuple[list[str], dict[str, object]]] = []

        def popen(args: list[str], **kwargs: object) -> subprocess.Popen[str]:
            popen_calls.append((args, kwargs))
            return process

        with mock.patch.object(subprocess, "Popen", popen):
            started = process_mod.start_resident_app_server_process("codex.exe")

        self.assertIs(started, process)
        self.assertEqual(len(popen_calls), 1)
        args, kwargs = popen_calls[0]
        self.assertEqual(args, ["codex.exe", "app-server"])
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        self.assertEqual(kwargs["bufsize"], 1)
        self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_process_helper_wraps_oserror_with_executable_detail(self) -> None:
        def popen(*_args: object, **_kwargs: object) -> subprocess.Popen[str]:
            raise OSError("missing")

        with mock.patch.object(subprocess, "Popen", popen):
            with self.assertRaisesRegex(
                CodexAppServerTransportError,
                "Failed to start resident Codex app-server. executable='missing.exe'",
            ):
                _ = process_mod.start_resident_app_server_process("missing.exe")

    def test_stdio_helper_detects_missing_pipes(self) -> None:
        self.assertTrue(process_mod.has_resident_app_server_stdio(_process()))
        self.assertFalse(process_mod.has_resident_app_server_stdio(_process(stdin=None)))
        self.assertFalse(process_mod.has_resident_app_server_stdio(_process(stdout=None)))

    def test_close_helper_closes_stdin_and_terminates_running_process(self) -> None:
        process = _FakeProcess()
        logs: list[str] = []

        process_mod.close_resident_app_server_process(_as_popen(process), logs.append)

        self.assertTrue(cast(io.StringIO, process.stdin).closed)
        self.assertTrue(process.terminated)
        self.assertEqual(process.wait_timeout, 1.5)
        self.assertFalse(process.killed)
        self.assertEqual(logs, [])

    def test_close_helper_skips_terminate_for_exited_process(self) -> None:
        process = _FakeProcess(poll_result=0)

        process_mod.close_resident_app_server_process(_as_popen(process), lambda _text: None)

        self.assertTrue(cast(io.StringIO, process.stdin).closed)
        self.assertFalse(process.terminated)
        self.assertIsNone(process.wait_timeout)
        self.assertFalse(process.killed)

    def test_close_helper_logs_close_terminate_and_kill_failures(self) -> None:
        process = _FakeProcess(
            stdin=_FailingCloseStdin(),
            terminate_error=RuntimeError("terminate boom"),
            kill_error=RuntimeError("kill boom"),
        )
        logs: list[str] = []

        process_mod.close_resident_app_server_process(_as_popen(process), logs.append)

        self.assertEqual(
            logs,
            [
                "app_server_transport_stdin_close_failed error_type=OSError error=close boom",
                "app_server_transport_terminate_failed error_type=RuntimeError error=terminate boom",
                "app_server_transport_kill_failed error_type=RuntimeError error=kill boom",
            ],
        )


class ResidentTransportStartTests(unittest.TestCase):
    def test_start_preserves_state_reset_handshake_thread_and_log(self) -> None:
        logs: list[str] = []
        process = _process()
        transport = _StartProbeTransport(executable="codex.exe", log_func=logs.append)
        transport.seed_stale_state(logs.append)

        with (
            mock.patch.object(resident_mod, "start_resident_app_server_process", return_value=process),
            mock.patch.object(threading, "Thread", _FakeThread),
        ):
            transport.start()

        self.assertIs(transport.process, process)
        self.assertEqual(transport.responses_snapshot(), {})
        self.assertEqual(transport.get_pending_server_requests(), [])
        self.assertEqual(
            transport.requests,
            [
                (
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex-discord-remote",
                            "title": "Codex Discord Remote",
                            "version": "1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                    8.0,
                )
            ],
        )
        self.assertEqual(transport.notifications, [("initialized", {})])
        self.assertTrue(transport.initialized)
        self.assertTrue(transport.stdout_thread_started)
        self.assertIn("app_server_transport_started executable=codex.exe", logs)

    def test_start_closes_process_when_stdio_is_unavailable(self) -> None:
        process = _FakeProcess(stdin=None, stdout=io.StringIO())
        transport = _StartProbeTransport(executable="codex.exe")

        with mock.patch.object(
            resident_mod,
            "start_resident_app_server_process",
            return_value=_as_popen(process),
        ):
            with self.assertRaisesRegex(
                CodexAppServerTransportError,
                "Resident Codex app-server stdio is unavailable.",
            ):
                transport.start()

        self.assertIsNone(transport.process)
        self.assertTrue(process.terminated)
        self.assertEqual(process.wait_timeout, 1.5)

    def test_close_locked_resets_owner_state_and_delegates_process_close(self) -> None:
        process = _process()
        calls: list[subprocess.Popen[str]] = []
        transport = _StartProbeTransport(executable="codex.exe")
        transport.install_process(process, initialized=True)

        def close_process(process_to_close: subprocess.Popen[str], _log: Callable[[str], None]) -> None:
            calls.append(process_to_close)

        with mock.patch.object(resident_mod, "close_resident_app_server_process", close_process):
            transport.close_locked()

        self.assertEqual(calls, [process])
        self.assertIsNone(transport.process)
        self.assertFalse(transport.initialized)


class ResidentTransportTimeoutBoundaryTests(unittest.TestCase):
    def test_request_slot_wait_uses_the_request_timeout_budget(self) -> None:
        transport = _RequestBoundaryProbe()
        transport.hold_request_slot()
        try:
            with self.assertRaisesRegex(TimeoutError, "request slot for thread/read"):
                _ = transport.request_started("thread/read", timeout_sec=0.01)
        finally:
            transport.release_request_slot()

        self.assertEqual(transport.written_messages, [])

    def test_response_for_active_request_is_recorded(self) -> None:
        transport = _RequestBoundaryProbe()
        transport.seed_active_request("active-request")

        transport.handle_raw_line('{"id":"active-request","result":{"thread":{"id":"thread-1"}}}')

        self.assertEqual(
            transport.responses_snapshot(),
            {"active-request": {"id": "active-request", "result": {"thread": {"id": "thread-1"}}}},
        )

    def test_unmatched_response_is_discarded_without_a_timeout_tombstone(self) -> None:
        logs: list[str] = []
        transport = _RequestBoundaryProbe(log_func=logs.append)

        transport.handle_raw_line('{"id":"late-request","result":{"thread":{"id":"thread-1"}}}')

        self.assertEqual(transport.responses_snapshot(), {})
        self.assertEqual(logs, ["app_server_transport_late_response_discarded id=late-request"])


class ResidentThreadResumeRetryTests(unittest.TestCase):
    def test_resume_retries_once_with_remaining_budget_after_first_timeout(self) -> None:
        clock_values = iter((100.0, 110.0))
        logs: list[str] = []
        requests: list[tuple[str, JsonMapping, float]] = []
        transport = transport_mod.PersistentCodexAppServer(
            executable_resolver=lambda: "codex.exe",
            log_func=logs.append,
            monotonic_func=lambda: next(clock_values),
        )

        def request(
            method: str,
            params: JsonMapping | None = None,
            *,
            timeout_sec: float = 10.0,
        ) -> JsonObject:
            requests.append((method, dict(params or {}), timeout_sec))
            if len(requests) == 1:
                raise TimeoutError("first resume timed out")
            return {"thread": {"id": "thread-1"}}

        with mock.patch.object(transport, "request", request):
            result = transport.resume_thread("thread-1", timeout_sec=32.0)

        self.assertEqual(result["thread"], {"id": "thread-1"})
        self.assertEqual(
            requests,
            [
                ("thread/resume", {"threadId": "thread-1"}, 10.0),
                ("thread/resume", {"threadId": "thread-1"}, 22.0),
            ],
        )
        self.assertEqual(len(logs), 1)
        self.assertIn("app_server_thread_resume_retry", logs[0])
        self.assertIn("thread=thread-1", logs[0])


class _StartProbeTransport(ResidentCodexAppServerTransport):
    def __init__(self, *, executable: str, log_func: Callable[[str], None] | None = None) -> None:
        super().__init__(executable_resolver=lambda: executable, log_func=log_func)
        self.requests: list[tuple[str, JsonMapping, float]] = []
        self.notifications: list[tuple[str, JsonMapping]] = []

    def seed_stale_state(self, log: Callable[[str], None]) -> None:
        self._responses["old"] = {"stale": True}
        self._pending.record_server_request(
            "old",
            {"method": "item/tool/requestUserInput", "params": {"threadId": "thread-1"}},
            log,
        )

    def responses_snapshot(self) -> dict[str, JsonObject]:
        return dict(self._responses)

    def install_process(self, process: subprocess.Popen[str], *, initialized: bool) -> None:
        self.process: subprocess.Popen[str] | None = process
        self._initialized: bool = initialized

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def stdout_thread_started(self) -> bool:
        return isinstance(self._stdout_thread, _FakeThread) and self._stdout_thread.started

    @override
    def _request_started(self, method: str, params: JsonMapping, *, timeout_sec: float) -> JsonObject:
        self.requests.append((method, dict(params), timeout_sec))
        return {}

    @override
    def notify(self, method: str, params: JsonMapping | None = None) -> None:
        self.notifications.append((method, dict(params or {})))


class _RequestBoundaryProbe(ResidentCodexAppServerTransport):
    def __init__(self, *, log_func: Callable[[str], None] | None = None) -> None:
        super().__init__(executable_resolver=lambda: "codex.exe", log_func=log_func)
        self.written_messages: list[JsonObject] = []

    def hold_request_slot(self) -> None:
        _ = self._request_lock.acquire()

    def release_request_slot(self) -> None:
        self._request_lock.release()

    def request_started(self, method: str, *, timeout_sec: float) -> JsonObject:
        return self._request_started(method, {}, timeout_sec=timeout_sec)

    def seed_active_request(self, request_id: str) -> None:
        self._active_request_id = request_id

    def handle_raw_line(self, raw_line: str) -> None:
        self._handle_raw_line(raw_line)

    def responses_snapshot(self) -> dict[str, JsonObject]:
        return dict(self._responses)

    @override
    def _write_message(self, payload: JsonMapping) -> None:
        self.written_messages.append(dict(payload))


class _FakeThread:
    def __init__(self, *, target: Callable[[], None], daemon: bool) -> None:
        self.target: Callable[[], None] = target
        self.daemon: bool = daemon
        self.started: bool = False

    def start(self) -> None:
        self.started = True


_DEFAULT_PIPE: Final = object()


class _FakeProcess:
    def __init__(
        self,
        *,
        stdin: IO[str] | None | object = _DEFAULT_PIPE,
        stdout: IO[str] | None | object = _DEFAULT_PIPE,
        poll_result: int | None = None,
        terminate_error: Exception | None = None,
        wait_error: Exception | None = None,
        kill_error: Exception | None = None,
    ) -> None:
        self.stdin: IO[str] | None = io.StringIO() if stdin is _DEFAULT_PIPE else cast(IO[str] | None, stdin)
        self.stdout: IO[str] | None = io.StringIO() if stdout is _DEFAULT_PIPE else cast(IO[str] | None, stdout)
        self.poll_result: int | None = poll_result
        self.terminate_error: Exception | None = terminate_error
        self.wait_error: Exception | None = wait_error
        self.kill_error: Exception | None = kill_error
        self.terminated: bool = False
        self.killed: bool = False
        self.wait_timeout: float | None = None

    def poll(self) -> int | None:
        if self.terminated:
            return 0
        return self.poll_result

    def terminate(self) -> None:
        if self.terminate_error is not None:
            raise self.terminate_error
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeout = timeout
        if self.wait_error is not None:
            raise self.wait_error
        return 0

    def kill(self) -> None:
        self.killed = True
        if self.kill_error is not None:
            raise self.kill_error


class _FailingCloseStdin(io.StringIO):
    @override
    def close(self) -> None:
        raise OSError("close boom")


def _process(
    *,
    stdin: IO[str] | None | object = _DEFAULT_PIPE,
    stdout: IO[str] | None | object = _DEFAULT_PIPE,
) -> subprocess.Popen[str]:
    return _as_popen(_FakeProcess(stdin=stdin, stdout=stdout))


def _as_popen(process: _FakeProcess) -> subprocess.Popen[str]:
    return cast(subprocess.Popen[str], cast(object, process))


if __name__ == "__main__":
    _ = unittest.main()
