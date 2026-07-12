# Pyright flags mandatory assert_never defaults after proving every enum case.
# pyright: reportUnnecessaryComparison=false

import asyncio  # noqa: F401  # noqa: ANYIO_OK -- discord.py is asyncio-native.
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, assert_never, override

import discord

import codex_discord_gpt_creation_journal as journal
import codex_discord_gpt_ownership as own
import codex_discord_runtime_config as cfg
from codex_discord_gpt_creation_journal import (
    GptCreationRecoveryRequest as GptCreationRecoveryRequest,
)
from codex_discord_gpt_lifecycle import GptLifecycleOperation as LifecycleOp
from codex_discord_gpt_lifecycle import transition_gpt_lifecycle as transition
from codex_discord_gpt_migration import GPT_PROJECT_KEY
from codex_discord_gpt_ownership import (
    DiscordChannelId,
    DiscordThreadId,
    MirrorThreadLifecycleState,
)


class DiscordChannelLike(Protocol):
    id: int

    @property
    def guild(self) -> "Guild": ...


class Guild(Protocol):
    id: int

    def get_channel(self, channel_id: int, /) -> DiscordChannelLike | None: ...

    async def fetch_channel(self, channel_id: int, /) -> DiscordChannelLike: ...


class DiscordClient(Protocol):
    def get_guild(self, guild_id: int, /) -> Guild | None: ...

    async def fetch_guild(self, guild_id: int, /) -> Guild: ...


class GptDiscordError(RuntimeError):
    @override
    def __str__(self) -> str:
        return type(self).__doc__ or "GPT Discord operation failed."


class GptDiscordConfigError(GptDiscordError):
    """GPT Discord configuration is missing."""


class GptDiscordAccessError(GptDiscordError):
    """Configured Discord server or channel is inaccessible."""


class GptDiscordChannelTypeError(GptDiscordError):
    """The configured Discord channel is not a text channel."""


class GptDiscordChannelNotAllowedError(GptDiscordError):
    """The configured Discord channel is not allowed."""


class GptDiscordScanError(GptDiscordError):
    """Discord thread scan did not complete."""


class GptDiscordRecoveryError(GptDiscordError):
    """GPT Discord recovery cannot continue."""


class GptDiscordRecoveryAmbiguityError(GptDiscordRecoveryError):
    """GPT recovery found conflicting identity or markers."""


class GptDiscordRetainedThreadError(GptDiscordError):
    """Retained Discord thread is inaccessible or has conflicting identity."""


class GptDiscordCreateError(GptDiscordError):
    """Discord could not create the GPT thread; retry recovery."""


class GptDiscordRenameError(GptDiscordError):
    """Discord could not finalize the GPT thread; retry recovery."""


class GptDiscordUnarchiveError(GptDiscordError):
    """Discord could not restore the retained GPT thread; retry recovery."""


type AllowedIds = Callable[[], set[int]]
type StartupId = Callable[[set[int]], int | None]


@dataclass(frozen=True, slots=True)
class GptDiscordAdapterDeps:
    get_allowed_channel_ids: AllowedIds = cfg.get_discord_allowed_channel_ids
    get_startup_channel_id: StartupId = cfg.get_startup_channel_id
    get_guild_id: Callable[[], int | None] = cfg.get_discord_guild_id
    discord_failure_types: tuple[type[Exception], ...] = (discord.DiscordException,)
    scan_timeout_seconds: float = 5.0


type Deps = GptDiscordAdapterDeps


@dataclass(frozen=True, slots=True)
class ThreadRef:
    thread_id: DiscordThreadId
    parent_channel_id: DiscordChannelId


DEFAULT_DEPS = GptDiscordAdapterDeps()
_handoff = journal.handoff_gpt_creation


@contextmanager
def _translate_discord_errors(deps: Deps, error: GptDiscordError) -> Generator[None]:
    try:
        yield
    except deps.discord_failure_types as exc:
        raise error from exc


async def resolve_configured_text_channel(
    client: DiscordClient, deps: Deps = DEFAULT_DEPS
) -> discord.TextChannel:
    allowed_ids = deps.get_allowed_channel_ids()
    guild_id = deps.get_guild_id()
    channel_id = deps.get_startup_channel_id(allowed_ids)
    if guild_id is None or channel_id is None:
        raise GptDiscordConfigError()
    if channel_id not in allowed_ids:
        raise GptDiscordChannelNotAllowedError()
    guild = client.get_guild(guild_id)
    if guild is None:
        with _translate_discord_errors(deps, GptDiscordAccessError()):
            guild = await client.fetch_guild(guild_id)
    if guild.id != guild_id:
        raise GptDiscordAccessError()
    channel = guild.get_channel(channel_id)
    if channel is None:
        with _translate_discord_errors(deps, GptDiscordAccessError()):
            channel = await guild.fetch_channel(channel_id)
    if channel.id != channel_id or channel.guild.id != guild_id:
        raise GptDiscordAccessError()
    if not isinstance(channel, discord.TextChannel):
        raise GptDiscordChannelTypeError()
    return channel


async def scan_exact_creation_marker(
    channel: discord.TextChannel,
    operation: journal.GptCreationOperation,
    deps: Deps = DEFAULT_DEPS,
) -> tuple[discord.Thread, ...]:
    try:
        threads = list[DiscordChannelLike](channel.threads)
        async with asyncio.timeout(deps.scan_timeout_seconds):
            async for archived in channel.archived_threads(limit=None):
                threads.append(archived)
    except (TimeoutError, TypeError, *deps.discord_failure_types) as exc:
        raise GptDiscordScanError() from exc
    by_id: dict[int, discord.Thread] = {}
    for thread in threads:
        if not isinstance(thread, discord.Thread):
            raise GptDiscordScanError()
        if thread.guild.id != channel.guild.id or thread.parent_id != channel.id:
            raise GptDiscordScanError()
        _ = by_id.setdefault(int(thread.id), thread)
    return tuple(
        thread
        for thread in by_id.values()
        if journal.parse_gpt_creation_thread_name(thread.name) == operation.nonce
    )


async def _fetch_thread(guild: Guild, ref: ThreadRef, deps: Deps) -> discord.Thread:
    if (channel := guild.get_channel(ref.thread_id)) is None:
        with _translate_discord_errors(deps, GptDiscordRetainedThreadError()):
            channel = await guild.fetch_channel(ref.thread_id)
    if not isinstance(channel, discord.Thread):
        raise GptDiscordRetainedThreadError()
    actual = channel.id, channel.guild.id, channel.parent_id
    if actual != (ref.thread_id, guild.id, ref.parent_channel_id):
        raise GptDiscordRetainedThreadError()
    return channel


async def revive_retained_gpt_thread(
    client: DiscordClient,
    mapping: own.MirrorThreadOwnership | None,
    deps: Deps = DEFAULT_DEPS,
) -> discord.Thread:
    configured = await resolve_configured_text_channel(client, deps=deps)
    if mapping is None or (
        mapping.project_key != GPT_PROJECT_KEY
        or mapping.managed_by is not own.MirrorThreadManagedBy.GPT_CHAT
    ):
        raise GptDiscordRetainedThreadError()
    match mapping.lifecycle_state:
        case MirrorThreadLifecycleState.ACTIVE:
            raise GptDiscordRetainedThreadError()
        case MirrorThreadLifecycleState.REACTIVATING:
            pass
        case MirrorThreadLifecycleState.DEACTIVATING:
            raise GptDiscordRetainedThreadError()
        case MirrorThreadLifecycleState.INACTIVE:
            pass
        case _ as unreachable:
            assert_never(unreachable)
    ref = ThreadRef(mapping.discord_thread_id, mapping.discord_channel_id)
    thread = await _fetch_thread(configured.guild, ref, deps)
    if thread.archived or thread.locked:
        edit = thread.edit
        with _translate_discord_errors(deps, GptDiscordUnarchiveError()):
            thread = await edit(archived=False, locked=False, reason="Restore GPT")
        if thread.archived or thread.locked:
            raise GptDiscordUnarchiveError()
    return thread


async def create_gpt_marker_thread(
    client: DiscordClient,
    operation: journal.GptCreationOperation,
    deps: Deps = DEFAULT_DEPS,
) -> discord.Thread:
    channel = await resolve_configured_text_channel(client, deps=deps)
    if (
        operation.status is not journal.GptCreationStatus.CREATE_STARTED
        or operation.discord_thread_id is not None
    ):
        raise GptDiscordRecoveryError()
    if int(operation.discord_parent_channel_id) != int(channel.id):
        raise GptDiscordRecoveryError()
    if await scan_exact_creation_marker(channel, operation, deps=deps):
        raise GptDiscordRecoveryAmbiguityError()
    with _translate_discord_errors(deps, GptDiscordCreateError()):
        thread = await channel.create_thread(
            name=journal.format_gpt_creation_thread_name(operation),
            type=discord.ChannelType.public_thread,
            auto_archive_duration=10080,
        )
    if thread.guild.id != channel.guild.id or thread.parent_id != channel.id:
        raise GptDiscordCreateError()
    return thread


async def recover_gpt_creation(
    client: DiscordClient,
    request: GptCreationRecoveryRequest,
    deps: Deps = DEFAULT_DEPS,
) -> discord.Thread:
    final_name = request.final_name
    if not final_name or len(final_name) > 100 or "\n" in final_name:
        raise GptDiscordRecoveryError()
    channel = await resolve_configured_text_channel(client, deps=deps)
    operation = request.operation
    db_path = request.db_path
    if int(operation.discord_parent_channel_id) != int(channel.id):
        raise GptDiscordRecoveryAmbiguityError()
    match operation.status:
        case journal.GptCreationStatus.CREATE_STARTED:
            matches = await scan_exact_creation_marker(channel, operation, deps=deps)
            if len(matches) == 0:
                raise GptDiscordRecoveryError()
            if len(matches) != 1:
                raise GptDiscordRecoveryAmbiguityError()
            thread = matches[0]
            identified = _handoff(db_path, operation, DiscordThreadId(int(thread.id)))
        case journal.GptCreationStatus.DISCORD_IDENTIFIED:
            thread_id = operation.discord_thread_id
            if thread_id is None:
                raise GptDiscordRecoveryError()
            ref = ThreadRef(thread_id, operation.discord_parent_channel_id)
            thread = await _fetch_thread(channel.guild, ref, deps)
            identified = _handoff(db_path, operation, thread_id)
        case journal.GptCreationStatus.PREPARED:
            raise GptDiscordRecoveryError()
        case _ as unreachable:
            assert_never(unreachable)
    owner = identified.codex_thread_id
    mapping = own.get_mirror_thread_owner_by_codex_thread_id(db_path, owner)
    if mapping is None:
        raise GptDiscordRecoveryAmbiguityError()
    match mapping.lifecycle_state:
        case MirrorThreadLifecycleState.REACTIVATING:
            request.finalize_cursor(identified)
            _ = transition(db_path, owner, LifecycleOp.FINALIZE_REACTIVATION)
        case MirrorThreadLifecycleState.ACTIVE:
            pass
        case MirrorThreadLifecycleState.DEACTIVATING:
            raise GptDiscordRecoveryAmbiguityError()
        case MirrorThreadLifecycleState.INACTIVE:
            raise GptDiscordRecoveryAmbiguityError()
        case _ as unreachable:
            assert_never(unreachable)
    if thread.name != final_name or thread.archived or thread.locked:
        with _translate_discord_errors(deps, GptDiscordRenameError()):
            thread = await thread.edit(name=final_name, archived=False, locked=False)
        if thread.name != final_name or thread.archived or thread.locked:
            raise GptDiscordRenameError()
    journal.complete_gpt_creation(db_path, identified)
    return thread
