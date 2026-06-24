from __future__ import annotations

import discord

from codex_discord_mirror_channel_store import MirrorChannelDeps
from codex_thread_models import ThreadInfo


def stored_mirror_thread_ids(
    row: tuple[int, int],
    codex_thread: ThreadInfo,
    project_channel: discord.TextChannel,
) -> tuple[int, int]:
    channel_id = int(row[0])
    thread_id = int(row[1])
    if channel_id != int(project_channel.id):
        raise RuntimeError(
            f"Stored mirror thread {thread_id} for {codex_thread.id} belongs to "
            + f"project channel {channel_id}, not current project channel {project_channel.id}."
        )
    return channel_id, thread_id


async def fetch_stored_discord_thread(
    codex_thread: ThreadInfo,
    project_channel: discord.TextChannel,
    thread_id: int,
    *,
    deps: MirrorChannelDeps,
) -> discord.Thread:
    cached = project_channel.guild.get_thread(thread_id)
    if isinstance(cached, discord.Thread):
        return cached
    try:
        fetched = await project_channel.guild.fetch_channel(thread_id)
    except deps.fetch_failure_types as exc:
        raise RuntimeError(
            f"Stored mirror thread {thread_id} for {codex_thread.id} "
            + f"is unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(fetched, discord.Thread):
        raise RuntimeError(
            f"Stored mirror thread {thread_id} for {codex_thread.id} "
            + f"is {type(fetched).__name__}, not Thread."
        )
    return fetched
