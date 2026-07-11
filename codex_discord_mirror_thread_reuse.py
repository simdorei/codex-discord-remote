from __future__ import annotations

import asyncio  # noqa: ANYIO_OK

import discord

import codex_discord_store as discord_store
from codex_discord_mirror_channel_store import MirrorChannelDeps


def _require_non_gpt_owner(
    thread: discord.Thread,
    *,
    deps: MirrorChannelDeps,
) -> discord.Thread:
    owner = discord_store.get_mirror_thread_owner_by_discord_thread_id(
        deps.db_path,
        int(thread.id),
    )
    if owner is not None and not owner.is_ordinary:
        raise discord_store.GptOwnershipOverwriteError(
            codex_thread_id=owner.codex_thread_id,
            discord_thread_id=owner.discord_thread_id,
        )
    return thread


async def find_existing_thread_channel(
    project_channel: discord.TextChannel,
    thread_name: str,
    *,
    deps: MirrorChannelDeps,
) -> discord.Thread | None:
    current_threads = list[object](getattr(project_channel, "threads", []))
    for thread in current_threads:
        if isinstance(thread, discord.Thread) and str(getattr(thread, "name", "") or "") == thread_name:
            return _require_non_gpt_owner(thread, deps=deps)

    try:
        archive_iter = project_channel.archived_threads(limit=100)
    except (AttributeError, TypeError):
        return None
    except deps.fetch_failure_types as exc:
        deps.log(
            f"mirror_thread_reuse_scan_failed channel={getattr(project_channel, 'id', '-')} "
            + f"error={str(exc)[:120]}"
        )
        return None
    try:
        async with asyncio.timeout(5):
            async for thread in archive_iter:
                if str(getattr(thread, "name", "") or "") == thread_name:
                    return _require_non_gpt_owner(thread, deps=deps)
    except (
        discord_store.DiscordOwnershipConflictError,
        discord_store.GptOwnershipOverwriteError,
    ):
        raise
    except TimeoutError:
        deps.log(f"mirror_thread_reuse_scan_timeout channel={getattr(project_channel, 'id', '-')}")
    except deps.fetch_failure_types as exc:
        deps.log(
            f"mirror_thread_reuse_scan_failed channel={getattr(project_channel, 'id', '-')} "
            + f"error={str(exc)[:120]}"
        )
    return None
