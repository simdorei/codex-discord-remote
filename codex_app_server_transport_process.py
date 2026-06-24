from __future__ import annotations

import subprocess
from typing import Callable

from codex_app_server_transport_replies import CodexAppServerTransportError


LogFunc = Callable[[str], None]


def start_resident_app_server_process(executable: str) -> subprocess.Popen[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.Popen(
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
            "Failed to start resident Codex app-server. " + f"executable={executable!r}"
        ) from exc


def has_resident_app_server_stdio(process: subprocess.Popen[str]) -> bool:
    return process.stdin is not None and process.stdout is not None


def close_resident_app_server_process(process: subprocess.Popen[str], log: LogFunc) -> None:
    stdin = process.stdin
    if stdin is not None and not stdin.closed:
        try:
            stdin.close()
        except OSError as exc:
            log(f"app_server_transport_stdin_close_failed error_type={type(exc).__name__} error={exc}")
    if process.poll() is None:
        try:
            process.terminate()
            _ = process.wait(timeout=1.5)
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - close path logs and escalates to kill.
            log(f"app_server_transport_terminate_failed error_type={type(exc).__name__} error={exc}")
            try:
                process.kill()
            except Exception as kill_exc:  # noqa: BROAD_EXCEPT_OK - final close attempt can only be logged.
                log(f"app_server_transport_kill_failed error_type={type(kill_exc).__name__} error={kill_exc}")
