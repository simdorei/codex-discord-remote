from __future__ import annotations

from collections.abc import Callable
import json
from json import JSONDecodeError
import os
from pathlib import Path
import subprocess
import time
from typing import Final, cast

from codex_app_server_transport_reply_types import JsonObject, JsonValue
import codex_discord_prompt_mapped_delivery as mapped_delivery


LogFunc = Callable[[str], None]
HOOK_TIMEOUT_SECONDS: Final = 120.0
PROMPT_REWRITE_PLUGIN_CACHE_ROOT: Final = Path.home() / ".codex" / "plugins" / "cache"
PROMPT_REWRITE_PLUGIN_PARTS: Final = ("lazy-eng-study-codex-local", "lazy-eng-study-codex")


def rewrite_prompt(prompt: str, *, cwd: Path, log: LogFunc) -> mapped_delivery.PromptPreprocessResult:
    if not prompt_may_need_rewrite(prompt):
        return mapped_delivery.keep_prompt(prompt)

    started_at = time.monotonic()
    plugin_root = find_prompt_rewrite_plugin_root()
    if plugin_root is None:
        return hook_failure(prompt, "Lazy Eng Study Codex plugin root was not found", log)

    try:
        hook_stdout = run_prompt_rewrite_hook(plugin_root, prompt, cwd)
        if hook_stdout.strip() == "":
            if is_manual_rewrite_command(prompt):
                return hook_failure(prompt, "Lazy Eng Study Codex hook returned no output", log)
            log_lazy_result(log, "noop", prompt, "", started_at)
            return mapped_delivery.keep_prompt(prompt)
        hook_output = parse_json_object(hook_stdout)
        result = prompt_result_from_hook_output(prompt, hook_output)
    except (JSONDecodeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return hook_failure(prompt, f"{type(exc).__name__}: {exc}", log)

    log_lazy_result(log, hook_status(hook_output), prompt, result.visible_line, started_at)
    return result


def prompt_may_need_rewrite(prompt: str) -> bool:
    return is_manual_rewrite_command(prompt) or contains_hangul(prompt)


def is_manual_rewrite_command(prompt: str) -> bool:
    normalized = prompt.lstrip().lower()
    return normalized.startswith("$kor") or normalized.startswith("$gram")


def contains_hangul(text: str) -> bool:
    return any("\uac00" <= char <= "\ud7af" for char in text)


def find_prompt_rewrite_plugin_root() -> Path | None:
    root = PROMPT_REWRITE_PLUGIN_CACHE_ROOT.joinpath(*PROMPT_REWRITE_PLUGIN_PARTS)
    if not root.is_dir():
        return None
    try:
        candidates = [path for path in root.iterdir() if (path / "scripts" / "bootstrap.ps1").is_file()]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def run_prompt_rewrite_hook(plugin_root: Path, prompt: str, cwd: Path) -> str:
    payload: JsonObject = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": str(cwd),
        "session_id": "discord-harness-resident",
    }
    env = dict(os.environ)
    env["PLUGIN_ROOT"] = str(plugin_root)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(plugin_root / "scripts" / "bootstrap.ps1"),
        ],
        input=(json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"),
        capture_output=True,
        cwd=str(plugin_root),
        env=env,
        timeout=HOOK_TIMEOUT_SECONDS,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout = completed.stdout.decode("utf-8-sig", errors="replace")
    stderr = completed.stderr.decode("utf-8-sig", errors="replace").strip()
    if completed.returncode != 0:
        detail = stderr or stdout.strip()
        raise ValueError(f"hook exited {completed.returncode}: {detail}")
    return stdout


def parse_json_object(raw: str) -> JsonObject:
    value = cast(JsonValue, json.loads(raw, object_pairs_hook=json_object_pairs))
    if not isinstance(value, dict):
        raise ValueError("hook output JSON must be an object")
    return value


def json_object_pairs(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    return dict(pairs)


def prompt_result_from_hook_output(prompt: str, output: JsonObject) -> mapped_delivery.PromptPreprocessResult:
    system_message = string_field(output, "systemMessage")
    hook_specific = object_field(output, "hookSpecificOutput")
    context = string_field(hook_specific, "additionalContext")
    metadata = hook_specific.get("lazyEngStudyCodex")
    if isinstance(metadata, dict):
        status = string_field(metadata, "status")
        match status:
            case "success":
                visible_line = string_field(metadata, "visibleLine")
                result_prompt = visible_line
            case "failure":
                visible_line = system_message
                result_prompt = failed_hook_prompt(system_message, context, prompt)
            case _:
                raise ValueError(f"unsupported Lazy Eng hook status: {status}")
    else:
        visible_line = visible_line_from_system_message(system_message)
        result_prompt = (
            failed_hook_prompt(system_message, context, prompt)
            if is_prompt_rewrite_failure_message(system_message)
            else visible_line
        )
    return mapped_delivery.PromptPreprocessResult(
        prompt=result_prompt,
        visible_line=visible_line,
    )


def visible_line_from_system_message(system_message: str) -> str:
    prefix, separator, suffix = system_message.rpartition(" (")
    if separator and "/" in suffix and suffix.endswith(")"):
        return prefix
    return system_message


def is_prompt_rewrite_failure_message(system_message: str) -> bool:
    return system_message.startswith("Lazy Eng Study Codex ")


def failed_hook_prompt(system_message: str, context: str, prompt: str) -> str:
    parts = [system_message]
    if context:
        parts.extend(["", context])
    parts.extend(["", "Discord prompt:", "", prompt])
    return "\n".join(parts)


def object_field(data: JsonObject, key: str) -> JsonObject:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"hook output field {key!r} must be an object")
    return value


def string_field(data: JsonObject, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"hook output field {key!r} must be a string")
    return value


def hook_status(output: JsonObject) -> str:
    try:
        hook_specific = object_field(output, "hookSpecificOutput")
        metadata = hook_specific.get("lazyEngStudyCodex")
        if isinstance(metadata, dict):
            return string_field(metadata, "status")
        return "success"
    except ValueError:
        return "unknown"


def hook_failure(prompt: str, reason: str, log: LogFunc) -> mapped_delivery.PromptPreprocessResult:
    visible_line = f"Lazy Eng Study Codex hook failed: {reason}"
    log(f"prompt_rewrite_failed error={reason[:300]}")
    return mapped_delivery.PromptPreprocessResult(
        prompt="\n\n".join(
            [
                visible_line,
                "Do not assume a rewritten English prompt was available.",
                "Original Discord prompt:",
                prompt,
            ]
        ),
        visible_line=visible_line,
    )


def log_lazy_result(log: LogFunc, status: str, prompt: str, visible_line: str, started_at: float) -> None:
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log(
        f"prompt_rewrite_{status} "
        + f"prompt_len={len(prompt)} visible_len={len(visible_line)} elapsed_ms={elapsed_ms}"
    )
