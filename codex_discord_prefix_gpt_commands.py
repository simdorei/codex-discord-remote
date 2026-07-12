"""The five app-native GPT prefix commands."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable
from dataclasses import dataclass
from types import ModuleType
from typing import Final, Protocol, cast

import codex_discord_gpt_discord_adapter as discord_adapter
import codex_discord_gpt_read_service as read_service
import codex_discord_gpt_runtime as gpt_runtime

GPT_COMMAND: Final = "gpt"
GPT_USAGE: Final = "\n".join(
    (
        "Usage:",
        "!gpt list [limit]",
        "!gpt sync <csv>",
        "!gpt synced",
        "!gpt unsync <csv>",
        "!gpt sync_clear",
    )
)


class SendChunks(Protocol):
    def __call__(
        self,
        target: MessageIdentity,
        text: str,
        *,
        context: str = "send_chunks",
    ) -> Awaitable[int]: ...


class RuntimeClient(Protocol):
    pass


class MessageIdentity(Protocol):
    @property
    def id(self) -> int: ...


class GptMessage(Protocol):
    @property
    def guild(self) -> MessageIdentity | None: ...

    @property
    def author(self) -> MessageIdentity: ...

    @property
    def channel(self) -> MessageIdentity: ...


@dataclass(frozen=True, slots=True)
class PrefixGptCommandDeps:
    client: discord_adapter.DiscordClient
    runtime: gpt_runtime.GptRuntime
    send_chunks: SendChunks


def make_prefix_gpt_deps(
    module: ModuleType, client: RuntimeClient
) -> PrefixGptCommandDeps:
    return PrefixGptCommandDeps(
        cast(discord_adapter.DiscordClient, cast(object, client)),
        cast(gpt_runtime.GptRuntime, getattr(module, "GPT_RUNTIME")),
        cast(SendChunks, getattr(module, "send_prefix_chunks")),
    )


def _parse(arg: str) -> tuple[str, str | None] | None:
    parts = arg.strip().split()
    if not parts:
        return None
    action = parts[0]
    if action == "list" and len(parts) <= 2:
        return action, None if len(parts) == 1 else parts[1]
    if action in ("sync", "unsync") and len(parts) == 2:
        return action, parts[1]
    if action in ("synced", "sync_clear") and len(parts) == 1:
        return action, None
    return None


def _format_list(result: read_service.GptListResult) -> str:
    if not result.items:
        return "No app-native Codex chats are available."
    return "\n".join(
        ["App-native Codex chats:"]
        + [f"{item.index}. {item.thread.title}" for item in result.items]
    )


def _format_synced(result: read_service.GptSyncedResult) -> str:
    if not result.active:
        return "No app-native Codex chats are synced."
    lines = ["Synced app-native Codex chats:"]
    lines.extend(
        f"{item.index}. {item.mapping.thread_title} "
        + f"[{item.source_status.value}; {item.parent_status.value}]"
        for item in result.active
    )
    if result.audit:
        lines.append(f"Retained inactive or transitional chats: {len(result.audit)}")
    return "\n".join(lines)


async def _run(
    action: str,
    value: str | None,
    message: GptMessage,
    deps: PrefixGptCommandDeps,
) -> str:
    context = await deps.runtime.command_context(
        deps.client,
        None if message.guild is None else message.guild.id,
        message.author.id,
    )
    if action == "list":
        return _format_list(
            deps.runtime.read_service().list_candidates(context.key, value)
        )
    if action == "synced":
        return _format_synced(deps.runtime.read_service().list_synced(context.key))
    if action == "sync":
        await deps.runtime.sync(deps.client, context, value)
        return "GPT sync complete."
    workflow = deps.runtime.unsync_workflow(deps.client)
    if action == "unsync":
        await workflow.unsync(context.key, value)
        return "GPT unsync complete."
    await workflow.sync_clear()
    return "GPT sync clear complete."


async def handle_prefix_gpt_command(
    command: str,
    arg: str,
    message: GptMessage,
    *,
    deps: PrefixGptCommandDeps,
) -> bool:
    if command != GPT_COMMAND:
        return False
    parsed = _parse(arg)
    if parsed is None:
        _ = await deps.send_chunks(
            message.channel, GPT_USAGE, context="prefix_gpt_usage"
        )
        return True
    try:
        output = await _run(*parsed, message, deps)
    except RuntimeError as exc:
        output = f"GPT command failed: {exc}"
    except sqlite3.Error:
        output = "GPT command failed: local GPT state is unavailable."
    _ = await deps.send_chunks(message.channel, output, context="prefix_gpt")
    return True
