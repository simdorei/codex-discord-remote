from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import codex_desktop_bridge_desktop_process as desktop_process

try:
    import winreg
except ImportError:
    winreg = None


user32: ctypes.CDLL = ctypes.WinDLL("user32", use_last_error=True)


def normalize_executable_candidate(raw: str) -> Path | None:
    cleaned = str(raw or "").strip().strip('"').strip("'")
    if not cleaned:
        return None
    if cleaned.lower().endswith(".exe,0"):
        cleaned = cleaned[:-2]
    if "," in cleaned and cleaned.lower().endswith(".exe"):
        cleaned = cleaned.split(",", 1)[0].strip()
    path = Path(cleaned).expanduser()
    if path.exists() and path.is_file():
        return path
    return None


def read_registry_string(root: int, subkey: str, value_name: str = "") -> str:
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(root, subkey) as handle:
            query_result: tuple[str | bytes | int | list[str] | None, int] = winreg.QueryValueEx(handle, value_name)
    except OSError:
        return ""
    return str(query_result[0] or "").strip()


def iter_codex_desktop_registry_candidates() -> Iterator[tuple[str, Path]]:
    if winreg is None:
        return

    app_paths = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Codex.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Codex.exe"),
    )
    for root, subkey in app_paths:
        candidate = normalize_executable_candidate(read_registry_string(root, subkey))
        if candidate is not None:
            yield (f"registry:{subkey}", candidate)

    uninstall_roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for root, base_subkey in uninstall_roots:
        try:
            with winreg.OpenKey(root, base_subkey) as base_handle:
                subkey_count, _, _ = winreg.QueryInfoKey(base_handle)
                subkeys = [winreg.EnumKey(base_handle, index) for index in range(subkey_count)]
        except OSError:
            continue
        for child_name in subkeys:
            child_subkey = f"{base_subkey}\\{child_name}"
            display_name = read_registry_string(root, child_subkey, "DisplayName")
            if "codex" not in display_name.lower():
                continue
            display_icon = normalize_executable_candidate(read_registry_string(root, child_subkey, "DisplayIcon"))
            if display_icon is not None:
                yield (f"registry:{child_subkey}:DisplayIcon", display_icon)
            install_location = read_registry_string(root, child_subkey, "InstallLocation")
            install_path = normalize_executable_candidate(str(Path(install_location) / "Codex.exe")) if install_location else None
            if install_path is not None:
                yield (f"registry:{child_subkey}:InstallLocation", install_path)


def iter_default_codex_desktop_candidates() -> Iterator[tuple[str, Path]]:
    yield from desktop_process.iter_default_codex_desktop_candidates(os.environ)


def get_window_process_id(hwnd: int) -> int | None:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    if not pid.value:
        return None
    return int(pid.value)


def query_process_image_path(pid: int) -> Path | None:
    escaped_pid = str(int(pid))
    return normalize_executable_candidate(
        run_powershell_capture(f"(Get-Process -Id {escaped_pid} -ErrorAction SilentlyContinue).Path")
    )


def run_powershell_capture(command: str) -> str:
    powershell_exe = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    completed = subprocess.run(
        [powershell_exe, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def detect_codex_desktop_executable_via_powershell() -> tuple[Path | None, str]:
    process_path = normalize_executable_candidate(
        run_powershell_capture(
            " ".join(
                (
                    "Get-Process -Name Codex -ErrorAction SilentlyContinue",
                    "| Where-Object { $_.Path }",
                    "| Select-Object -First 1 -ExpandProperty Path",
                )
            )
        )
    )
    if process_path is not None:
        return (process_path, "powershell:Get-Process")

    install_root = run_powershell_capture(
        " ".join(
            (
                "Get-AppxPackage OpenAI.Codex -ErrorAction SilentlyContinue",
                "| Select-Object -First 1 -ExpandProperty InstallLocation",
            )
        )
    )
    if install_root:
        candidate = normalize_executable_candidate(str(Path(install_root) / "app" / "Codex.exe"))
        if candidate is not None:
            return (candidate, "powershell:Get-AppxPackage")

    return (None, "")


def persist_env_value(path: Path, key: str, value: str) -> bool:
    path = Path(path)
    newline = "\n"
    lines: list[str] = []
    if path.exists():
        text = path.read_text(encoding="utf-8")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
    found = False
    changed = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        existing_key, _, _ = raw_line.partition("=")
        if existing_key.strip() != key:
            continue
        found = True
        replacement = f"{key}={value}"
        if raw_line != replacement:
            lines[index] = replacement
            changed = True
        break
    if not found:
        lines.append(f"{key}={value}")
        changed = True
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.write_text(newline.join(lines) + newline, encoding="utf-8") > 0
