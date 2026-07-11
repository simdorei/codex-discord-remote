from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol, runtime_checkable

import codex_discord_bot_session_mirror_factory as discord_bot_session_mirror_factory
import codex_discord_bot_session_mirror_runtime as discord_bot_session_mirror_runtime
import codex_discord_session_mirror_delivery_flow as discord_session_mirror_delivery_flow
import codex_discord_session_mirror_item_delivery as discord_session_mirror_item_delivery
import codex_discord_store as discord_store
import codex_discord_typing_pulse as discord_typing_pulse
from codex_session_events import JsonEvent


class MessageableChannel(Protocol):
    @property
    def id(self) -> int | None: ...


class DiscordAbcModule(Protocol):
    Messageable: type[MessageableChannel]


class DiscordModule(Protocol):
    DiscordException: type[Exception]
    abc: DiscordAbcModule


class SessionMirrorTypingPulseStarter(Protocol):
    def __call__(
        self,
        channel: MessageableChannel,
        target_thread_id: str | None,
        context: str,
        *,
        channel_typing: discord_typing_pulse.ChannelTypingFactory,
        log: Callable[[str], None],
        on_start_error: Callable[[str | None], None] | None = None,
    ) -> None: ...


@runtime_checkable
class SessionMirrorAdapterModule(Protocol):
    SESSION_MIRROR_TARGET_LIMIT: int
    SESSION_MIRROR_ARCHIVE_BACKLOG_MAX_EVENTS_DEFAULT: int
    DISCORD_DELIVERY_EXCEPTIONS: tuple[type[BaseException], ...]
    MIRROR_DB_PATH: Path
    BRIDGE_SESSION_MIRROR_EVENTS: (
        discord_bot_session_mirror_factory.SessionMirrorEventsBridge
    )
    discord: DiscordModule
    parse_interactive_notice: (
        discord_session_mirror_item_delivery.ParseInteractiveNotice
    )
    send_interactive_prompt: (
        discord_session_mirror_item_delivery.SessionMirrorInteractiveSender[
            MessageableChannel
        ]
    )
    send_prompt_chunks: discord_session_mirror_item_delivery.SessionMirrorChunkSender[
        MessageableChannel
    ]
    send_session_mirror_attachment: (
        discord_session_mirror_item_delivery.SessionMirrorAttachmentSender[
            MessageableChannel
        ]
    )
    collect_session_mirror_items: (
        discord_session_mirror_delivery_flow.SessionMirrorItemCollector[JsonEvent]
    )
    start_session_mirror_typing_pulse: SessionMirrorTypingPulseStarter
    channel_typing: discord_typing_pulse.ChannelTypingFactory
    log_line: Callable[[str], None]
    resolve_target_ref: Callable[[str], tuple[str | None, str]]
    is_active_session_mirror_output_target: Callable[[str], bool]
    is_pending_session_mirror_cursor_target: Callable[[str], bool]
    clear_pending_session_mirror_cursor_target: Callable[[str], None]
    update_session_mirror_cursor: Callable[[str, str, int], None]
    get_or_init_session_mirror_cursor: Callable[[str, str, int], int]
    has_session_mirror_event: Callable[[str, str], bool]
    claim_session_mirror_event: Callable[[str, str], bool]
    deactivate_session_mirror_output_target: Callable[[str | None], None]


class SessionMirrorAdapterContractError(RuntimeError):
    """The bot module is missing a required session-mirror adapter member."""


@dataclass(frozen=True, slots=True)
class BotSessionMirrorAdapterRuntime:
    module: ModuleType
    configured_channel_lock: asyncio.Lock

    def make_session_mirror_runtime(
        self,
    ) -> discord_bot_session_mirror_runtime.SessionMirrorRuntime[MessageableChannel]:
        module = self._typed_module()
        return discord_bot_session_mirror_factory.make_session_mirror_runtime(
            configured_channel_lock=self.configured_channel_lock,
            target_limit=module.SESSION_MIRROR_TARGET_LIMIT,
            archive_backlog_max_events_default=(
                module.SESSION_MIRROR_ARCHIVE_BACKLOG_MAX_EVENTS_DEFAULT
            ),
            delivery_exceptions=module.DISCORD_DELIVERY_EXCEPTIONS,
            fetch_failure_types=(module.discord.DiscordException,),
            get_db_path=lambda: module.MIRROR_DB_PATH,
            load_targets_in_thread=self.load_targets_in_thread,
            create_task=lambda coro: asyncio.create_task(coro),
            sleep=lambda seconds: asyncio.sleep(seconds),
            is_messageable=self.is_messageable,
            parse_interactive_notice=module.parse_interactive_notice,
            send_interactive_prompt=module.send_interactive_prompt,
            send_chunks=self.send_chunks,
            send_attachment=self.send_attachment,
            collect_session_mirror_items=module.collect_session_mirror_items,
            get_archive_skip_logged=self.get_archive_skip_logged,
            resolve_target_ref=self.resolve_target_ref,
            is_active_output_target=self.is_active_output_target,
            is_pending_cursor_target=self.is_pending_cursor_target,
            clear_pending_cursor_target=self.clear_pending_cursor_target,
            update_session_mirror_cursor=self.update_session_mirror_cursor,
            get_or_init_session_mirror_cursor=self.get_or_init_session_mirror_cursor,
            has_session_mirror_event=self.has_session_mirror_event,
            claim_session_mirror_event=self.claim_session_mirror_event,
            deactivate_session_mirror_output_target=self.deactivate_session_mirror_output_target,
            send_typing_pulse=self.send_typing_pulse,
            events_bridge=module.BRIDGE_SESSION_MIRROR_EVENTS,
            log=module.log_line,
        )

    async def load_targets_in_thread(
        self,
        db_path: Path,
        limit: int,
    ) -> Sequence[discord_bot_session_mirror_runtime.SessionMirrorTargetMapping]:
        return await asyncio.to_thread(
            discord_store.get_session_mirror_targets,
            db_path,
            limit=limit,
        )

    async def send_chunks(
        self,
        channel: MessageableChannel,
        content: str,
        *,
        context: str,
    ) -> None:
        await self._typed_module().send_prompt_chunks(
            channel,
            content,
            context=context,
        )

    async def send_attachment(
        self,
        channel: MessageableChannel,
        content: str,
        attachment_url: str,
        filename: str,
        *,
        context: str,
    ) -> None:
        await self._typed_module().send_session_mirror_attachment(
            channel,
            content,
            attachment_url,
            filename,
            context=context,
        )

    async def send_typing_pulse(
        self, channel: MessageableChannel, target_thread_id: str, context: str
    ) -> None:
        module = self._typed_module()
        module.start_session_mirror_typing_pulse(
            channel,
            target_thread_id,
            context,
            channel_typing=module.channel_typing,
            log=module.log_line,
            on_start_error=module.deactivate_session_mirror_output_target,
        )

    def get_archive_skip_logged(
        self,
        owner: discord_bot_session_mirror_runtime.SessionMirrorOwner[
            MessageableChannel
        ],
    ) -> set[str]:
        return discord_bot_session_mirror_runtime.SessionMirrorArchiveOwner.session_mirror_archive_skip_logged(
            owner
        )

    def is_messageable(self, channel: MessageableChannel) -> bool:
        return isinstance(channel, self._typed_module().discord.abc.Messageable)

    def resolve_target_ref(self, target_thread_id: str) -> tuple[str | None, str]:
        return self._typed_module().resolve_target_ref(target_thread_id)

    def is_active_output_target(self, target_thread_id: str) -> bool:
        return self._typed_module().is_active_session_mirror_output_target(
            target_thread_id
        )

    def is_pending_cursor_target(self, target_thread_id: str) -> bool:
        return self._typed_module().is_pending_session_mirror_cursor_target(
            target_thread_id
        )

    def clear_pending_cursor_target(self, target_thread_id: str) -> None:
        self._typed_module().clear_pending_session_mirror_cursor_target(
            target_thread_id
        )

    def update_session_mirror_cursor(
        self, codex_thread_id: str, rollout_path: str, cursor: int
    ) -> None:
        self._typed_module().update_session_mirror_cursor(
            codex_thread_id,
            rollout_path,
            cursor,
        )

    def get_or_init_session_mirror_cursor(
        self,
        codex_thread_id: str,
        rollout_path: str,
        initial_cursor: int,
    ) -> int:
        return self._typed_module().get_or_init_session_mirror_cursor(
            codex_thread_id,
            rollout_path,
            initial_cursor,
        )

    def has_session_mirror_event(self, event_digest: str, codex_thread_id: str) -> bool:
        return self._typed_module().has_session_mirror_event(
            event_digest,
            codex_thread_id,
        )

    def claim_session_mirror_event(
        self, event_digest: str, codex_thread_id: str
    ) -> bool:
        return self._typed_module().claim_session_mirror_event(
            event_digest,
            codex_thread_id,
        )

    def deactivate_session_mirror_output_target(self, target_thread_id: str) -> None:
        self._typed_module().deactivate_session_mirror_output_target(target_thread_id)

    def _typed_module(self) -> SessionMirrorAdapterModule:
        if isinstance(self.module, SessionMirrorAdapterModule):
            return self.module
        raise SessionMirrorAdapterContractError(
            "bot module does not satisfy the session mirror adapter contract"
        )
