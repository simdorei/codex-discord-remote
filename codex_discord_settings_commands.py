from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Callable

SETTINGS_USAGE = (
    "Usage: !settings [ref] [--model <model>] "
    "[--reasoning <effort>] [--effort <effort>] [--speed <speed>]"
)


@dataclass(frozen=True, slots=True)
class SettingsBridgeAction:
    argv: list[str] | None
    title: str
    usage: str | None = None


def build_settings_option_argv(option: str) -> list[str]:
    if option == "--model":
        return ["settings_options", "--field", "model"]
    if option in {"--reasoning", "--effort"}:
        return ["settings_options", "--field", "effort"]
    if option == "--speed":
        return ["settings_options", "--field", "speed"]
    return ["settings_options", "--field", "all"]


def build_settings_argv(
    channel_id: int | None,
    raw_arg: str,
    *,
    resolve_target_args_func: Callable[[int | None, str | None], list[str]],
) -> SettingsBridgeAction:
    try:
        tokens = shlex.split(raw_arg or "")
    except ValueError as exc:
        return SettingsBridgeAction(None, "Settings", f"{SETTINGS_USAGE}\nERROR: {exc}")

    ref: str | None = None
    setting_args: list[str] = []
    option_map = {
        "--model": "--model",
        "--reasoning": "--reasoning",
        "--effort": "--reasoning",
        "--speed": "--speed",
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in option_map:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                return SettingsBridgeAction(build_settings_option_argv(token), "Settings options")
            setting_args.extend([option_map[token], tokens[index + 1]])
            index += 2
            continue
        if token.startswith("--"):
            return SettingsBridgeAction(None, "Settings", f"{SETTINGS_USAGE}\nUnknown option: {token}")
        if ref is not None:
            return SettingsBridgeAction(None, "Settings", SETTINGS_USAGE)
        ref = token
        index += 1

    if not setting_args:
        return SettingsBridgeAction(["settings_options", "--field", "all"], "Settings options")

    argv = ["settings"]
    argv.extend(resolve_target_args_func(channel_id, ref))
    argv.extend(setting_args)
    return SettingsBridgeAction(argv, "Settings")


def build_settings_values_argv(
    channel_id: int | None,
    ref: str | None,
    *,
    model: str = "",
    effort: str = "",
    speed: str = "",
    resolve_target_args_func: Callable[[int | None, str | None], list[str]],
) -> SettingsBridgeAction:
    setting_args: list[str] = []
    if model.strip():
        setting_args.extend(["--model", model.strip()])
    if effort.strip():
        setting_args.extend(["--reasoning", effort.strip()])
    if speed.strip():
        setting_args.extend(["--speed", speed.strip()])
    if not setting_args:
        return SettingsBridgeAction(["settings_options", "--field", "all"], "Settings options")

    argv = ["settings"]
    argv.extend(resolve_target_args_func(channel_id, ref or None))
    argv.extend(setting_args)
    return SettingsBridgeAction(argv, "Settings")
