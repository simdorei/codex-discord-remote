"""Active mapping lease for race-safe session-mirror delivery."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import override

from codex_discord_gpt_ownership import (
    CodexThreadId,
    DiscordThreadId,
    MirrorThreadLifecycleState,
    MirrorThreadManagedBy,
)


@dataclass(frozen=True, slots=True)
class ActiveDeliveryIdentity:
    codex_thread_id: CodexThreadId
    discord_channel_id: int
    discord_thread_id: DiscordThreadId
    project_key: str
    managed_by: MirrorThreadManagedBy
    lifecycle_state: MirrorThreadLifecycleState
    updated_at: float

    @property
    def is_active_session_target(self) -> bool:
        active_owner = self.managed_by is MirrorThreadManagedBy.ORDINARY or (
            self.managed_by is MirrorThreadManagedBy.GPT_CHAT
            and self.project_key == "codex:chats"
        )
        return (
            active_owner
            and self.lifecycle_state is MirrorThreadLifecycleState.ACTIVE
            and int(self.discord_thread_id) > 0
        )


ActiveDeliveryIdentityReader = Callable[
    [str],
    Awaitable[ActiveDeliveryIdentity | None],
]
type ConfiguredChannelLock = AbstractAsyncContextManager[None]


@dataclass(frozen=True, slots=True)
class ConfiguredChannelLockMismatchError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Session delivery received a different configured-channel lock."


@dataclass(frozen=True, slots=True)
class ActiveDeliveryLeaseConfigurationError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Session delivery requires the configured-channel lock and active lease."


@dataclass(frozen=True, slots=True)
class ActiveDeliveryLeaseDeps:
    configured_channel_lock: ConfiguredChannelLock
    reread_active_delivery_identity: ActiveDeliveryIdentityReader


def resolve_active_delivery_lease_configuration(
    configured_channel_lock: ConfiguredChannelLock | None,
    lease_deps: ActiveDeliveryLeaseDeps | None,
) -> tuple[ConfiguredChannelLock, ActiveDeliveryLeaseDeps] | None:
    if configured_channel_lock is None:
        if lease_deps is None:
            return None
        raise ActiveDeliveryLeaseConfigurationError()
    if lease_deps is None:
        raise ActiveDeliveryLeaseConfigurationError()
    require_configured_channel_lock(configured_channel_lock, lease_deps)
    return configured_channel_lock, lease_deps


def require_configured_channel_lock(
    configured_channel_lock: ConfiguredChannelLock,
    lease_deps: ActiveDeliveryLeaseDeps,
) -> None:
    if configured_channel_lock is not lease_deps.configured_channel_lock:
        raise ConfiguredChannelLockMismatchError()


async def reread_active_delivery_identity(
    codex_thread_id: str,
    discord_thread_id: int,
    *,
    deps: ActiveDeliveryLeaseDeps,
) -> ActiveDeliveryIdentity | None:
    identity = await deps.reread_active_delivery_identity(codex_thread_id)
    if identity is None or not identity.is_active_session_target:
        return None
    if (
        str(identity.codex_thread_id) != codex_thread_id
        or int(identity.discord_thread_id) != discord_thread_id
    ):
        return None
    return identity


@asynccontextmanager
async def active_delivery_lease(
    expected_identity: ActiveDeliveryIdentity,
    *,
    configured_channel_lock: ConfiguredChannelLock,
    deps: ActiveDeliveryLeaseDeps,
) -> AsyncGenerator[bool]:
    require_configured_channel_lock(configured_channel_lock, deps)
    async with configured_channel_lock:
        current_identity = await reread_active_delivery_identity(
            str(expected_identity.codex_thread_id),
            int(expected_identity.discord_thread_id),
            deps=deps,
        )
        yield current_identity == expected_identity
