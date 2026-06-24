from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codex_desktop_bridge_desktop_process_scripts import build_stop_codex_app_server_script

GetOptionalEnvFilePath = Callable[[str], Path | None]
DiscoverExecutable = Callable[[], tuple[Path | None, str]]
IterCandidates = Callable[[], Iterable[tuple[str, Path]]]
PersistEnvValue = Callable[[Path, str, str], bool]
SetEnvironValue = Callable[[str, str], None]
WhichExecutable = Callable[[str], str | None]


class RunProcess(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        encoding: str,
        errors: str,
        creationflags: int,
    ) -> subprocess.CompletedProcess[str]: ...


class StartProcess(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        cwd: str,
        close_fds: bool,
        creationflags: int,
        text: bool,
    ) -> subprocess.Popen[str]: ...


@dataclass(frozen=True, slots=True)
class DesktopProcessDeps:
    get_optional_env_file_path: GetOptionalEnvFilePath
    detect_running_codex_desktop_executable: DiscoverExecutable
    detect_codex_desktop_executable_via_powershell: DiscoverExecutable
    iter_codex_desktop_registry_candidates: IterCandidates
    iter_default_codex_desktop_candidates: IterCandidates
    persist_env_value: PersistEnvValue
    set_environ_value: SetEnvironValue
    which: WhichExecutable
    run_process: RunProcess
    start_process: StartProcess
    create_no_window: int = 0
    create_new_process_group: int = 0


def iter_default_codex_desktop_candidates(environ: Mapping[str, str]) -> Iterator[tuple[str, Path]]:
    raw_candidates = [
        environ.get("LOCALAPPDATA", "").strip(),
        environ.get("ProgramFiles", "").strip(),
        environ.get("ProgramFiles(x86)", "").strip(),
    ]
    seen: set[str] = set()
    for base in raw_candidates:
        if not base:
            continue
        for candidate in (
            Path(base) / "Programs" / "Codex" / "Codex.exe",
            Path(base) / "Codex" / "Codex.exe",
        ):
            normalized = str(candidate).lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.exists() and candidate.is_file():
                yield (f"default:{candidate.parent}", candidate)


def discover_codex_desktop_executable(
    *,
    env_name: str,
    deps: DesktopProcessDeps,
) -> tuple[Path | None, str]:
    env_path = deps.get_optional_env_file_path(env_name)
    if env_path is not None:
        return (env_path, f"env:{env_name}")

    running_path, running_source = deps.detect_running_codex_desktop_executable()
    if running_path is not None:
        return (running_path, running_source)

    powershell_path, powershell_source = deps.detect_codex_desktop_executable_via_powershell()
    if powershell_path is not None:
        return (powershell_path, powershell_source)

    for source, candidate in deps.iter_codex_desktop_registry_candidates():
        return (candidate, source)

    for source, candidate in deps.iter_default_codex_desktop_candidates():
        return (candidate, source)

    return (None, "")


def ensure_codex_desktop_executable_configured(
    *,
    bridge_env_path: Path,
    env_name: str,
    deps: DesktopProcessDeps,
) -> tuple[Path, str, bool]:
    discovered_path, source = discover_codex_desktop_executable(env_name=env_name, deps=deps)
    if discovered_path is None:
        raise RuntimeError(
            "Codex Desktop executable could not be discovered. "
            + "Set CODEX_DESKTOP_EXE in .env or install Codex Desktop in the default Windows location."
        )
    updated = deps.persist_env_value(bridge_env_path, env_name, str(discovered_path))
    deps.set_environ_value(env_name, str(discovered_path))
    return (discovered_path, source or "discovered", updated)


def stop_codex_desktop_processes(
    executable_path: Path,
    *,
    deps: DesktopProcessDeps,
) -> tuple[bool, str]:
    taskkill = deps.which("taskkill") or "taskkill"
    completed = deps.run_process(
        [taskkill, "/IM", executable_path.name, "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=deps.create_no_window,
    )
    details = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
    stopped = completed.returncode == 0
    return (stopped, details or "-")


def stop_codex_app_server_processes() -> tuple[bool, str]:
    if os.name != "nt":
        return (False, "skipped: app-server process stop is only implemented on Windows")

    script = build_stop_codex_app_server_script()
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    details = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
    stopped = "stopped PID=" in details
    return (stopped, details or "-")


def start_codex_desktop_process(
    executable_path: Path,
    *,
    deps: DesktopProcessDeps,
) -> subprocess.Popen[str]:
    creationflags = 0
    creationflags |= deps.create_new_process_group
    return deps.start_process(
        [str(executable_path)],
        cwd=str(executable_path.parent),
        close_fds=True,
        creationflags=creationflags,
        text=True,
    )
