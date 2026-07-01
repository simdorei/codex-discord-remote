from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

import codex_desktop_bridge_macos_input as macos_input


def _click_named_ui(process_name: str, needles: Sequence[str]) -> str:
    tests = " or ".join(f"elementName contains {macos_input._quote(needle)}" for needle in needles)
    script = [
        'tell application "System Events"',
        f"tell process {macos_input._quote(process_name)}",
        "set frontmost to true",
        "repeat with win in windows",
        "repeat with uiElement in entire contents of win",
        "try",
        "set elementName to name of uiElement as text",
        f"if elementName is not missing value and ({tests}) then",
        "click uiElement",
        "return elementName",
        "end if",
        "end try",
        "end repeat",
        "end repeat",
        "end tell",
        "end tell",
        'error "CONTROL_NOT_FOUND"',
    ]
    return macos_input._osascript(script, timeout=15.0).strip()


def run_composer_focus_process(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    try:
        window = macos_input._find_codex_window()
        macos_input._raise_window(window.handle)
        x = int(window.left + ((window.right - window.left) * 0.5))
        y = max(window.top + 40, window.bottom - 88)
        macos_input._osascript(['tell application "System Events"', f"click at {{{x}, {y}}}", "end tell"])
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK\n", stderr="")
    except RuntimeError as exc:
        return subprocess.CompletedProcess(args=args, returncode=4, stdout=str(exc), stderr="")


def run_header_verification_process(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    env = kwargs.get("env")
    target = str(env.get("CODEX_THREAD_NAME", "") if isinstance(env, dict) else "").strip()
    if not target:
        return subprocess.CompletedProcess(args=args, returncode=2, stdout="NO_THREAD_NAME", stderr="")
    try:
        for window in macos_input._refresh_windows():
            if target in window.title and (
                window.process_name == "Codex" or window.title == "Codex" or window.title.startswith("Codex - ")
            ):
                macos_input._raise_window(window.handle)
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"OK:{window.title}", stderr="")
    except RuntimeError as exc:
        return subprocess.CompletedProcess(args=args, returncode=4, stdout=str(exc), stderr="")
    return subprocess.CompletedProcess(args=args, returncode=5, stdout="NO_HEADER_MATCH", stderr="")


def run_sidebar_activation_process(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    env = kwargs.get("env")
    thread_name = str(env.get("CODEX_THREAD_NAME", "") if isinstance(env, dict) else "").strip()
    project_name = str(env.get("CODEX_PROJECT_NAME", "") if isinstance(env, dict) else "").strip()
    if not thread_name:
        return subprocess.CompletedProcess(args=args, returncode=2, stdout="NO_THREAD_NAME", stderr="")
    needles = [thread_name] + ([project_name] if project_name else [])
    try:
        clicked = _click_named_ui("Codex", needles)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"OK:{clicked}", stderr="")
    except RuntimeError as exc:
        return subprocess.CompletedProcess(args=args, returncode=6, stdout=str(exc), stderr="")


def run_permission_approval_process(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    env = kwargs.get("env")
    action = str(env.get("CODEX_APPROVAL_DECISION", "") if isinstance(env, dict) else "").strip()
    labels = {
        "accept": ("Approve", "Allow", "Yes", "Run"),
        "accept-remember": ("Always allow", "Allow and remember", "Remember"),
        "cancel": ("Cancel", "Deny", "Reject"),
        "decline-message": ("Decline", "Send"),
    }.get(action)
    if labels is None:
        return subprocess.CompletedProcess(args=args, returncode=2, stdout=f"UNSUPPORTED_ACTION:{action}", stderr="")
    try:
        _ = _click_named_ui("Codex", labels)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"ACTION={action}", stderr="")
    except RuntimeError as exc:
        return subprocess.CompletedProcess(args=args, returncode=6, stdout=str(exc), stderr="")
