from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from types import ModuleType
from typing import Protocol, TypeAlias, cast

import aiohttp

import chatgpt_app_cdp
import chatgpt_app_cdp_client
import chatgpt_app_mirror_cycle
import chatgpt_app_mirror_runtime
import chatgpt_app_mirror_store
from chatgpt_app_mirror_config import load_chatgpt_app_mirror_config
from chatgpt_app_mirror_models import (
    ChatGptMirrorCyclePlan,
    ChatGptMirrorDelivery,
    ChatGptSnapshot,
)


ModuleValue: TypeAlias = object


class ChatGptMirrorChannelUnavailable(RuntimeError):
    pass


class ChatGptMirrorBotOwner(Protocol):
    def is_closed(self) -> bool: ...

    async def resolve_session_mirror_channel(
        self,
        discord_thread_id: int,
    ) -> ModuleValue | None: ...

    async def send_chatgpt_mirror_delivery(
        self,
        delivery: ChatGptMirrorDelivery,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class BotChatGptMirrorAdapterRuntime:
    module: ModuleType

    def make_runtime(self) -> chatgpt_app_mirror_runtime.ChatGptAppMirrorRuntime:
        config = load_chatgpt_app_mirror_config(os.environ)

        async def read_snapshot() -> ChatGptSnapshot:
            return await chatgpt_app_cdp_client.read_chatgpt_app_snapshot(
                config.cdp_http_url
            )

        async def prepare_cycle(snapshot: ChatGptSnapshot) -> ChatGptMirrorCyclePlan:
            return await asyncio.to_thread(
                chatgpt_app_mirror_cycle.prepare_mirror_cycle,
                self._db_path(),
                snapshot,
                config.discord_thread_ids,
            )

        async def mark_delivery(delivery: ChatGptMirrorDelivery) -> bool:
            return await asyncio.to_thread(
                chatgpt_app_mirror_cycle.mark_mirror_delivery,
                self._db_path(),
                delivery,
            )

        discord_exceptions = cast(
            tuple[type[BaseException], ...],
            getattr(self.module, "DISCORD_DELIVERY_EXCEPTIONS"),
        )
        expected_exceptions = (
            *discord_exceptions,
            aiohttp.ClientError,
            TimeoutError,
            sqlite3.Error,
            chatgpt_app_cdp.CdpContractError,
            chatgpt_app_mirror_store.ChatGptMirrorStoreConfigError,
            ChatGptMirrorChannelUnavailable,
        )
        return chatgpt_app_mirror_runtime.ChatGptAppMirrorRuntime(
            chatgpt_app_mirror_runtime.ChatGptAppMirrorRuntimeDeps(
                enabled=config.enabled,
                poll_seconds=config.poll_seconds,
                read_snapshot=read_snapshot,
                prepare_cycle=prepare_cycle,
                mark_delivery=mark_delivery,
                create_task=lambda action: asyncio.create_task(action),
                sleep=lambda seconds: asyncio.sleep(seconds),
                expected_exceptions=expected_exceptions,
                log=self._log,
            )
        )

    async def send_delivery(
        self,
        owner: ChatGptMirrorBotOwner,
        delivery: ChatGptMirrorDelivery,
    ) -> None:
        channel = await owner.resolve_session_mirror_channel(
            delivery.discord_thread_id
        )
        if channel is None:
            raise ChatGptMirrorChannelUnavailable(
                f"Discord thread {delivery.discord_thread_id} is unavailable"
            )
        _ = await cast(
            Callable[..., Awaitable[ModuleValue]],
            self._module_func("send_prompt_chunks"),
        )(
            channel,
            chatgpt_app_mirror_runtime.format_chatgpt_mirror_delivery(delivery),
            context="chatgpt_app_mirror",
        )

    def _db_path(self) -> Path:
        return cast(Path, getattr(self.module, "MIRROR_DB_PATH"))

    def _module_func(self, name: str) -> ModuleValue:
        return cast(ModuleValue, getattr(self.module, name))

    def _log(self, message: str) -> None:
        cast(Callable[[str], None], self._module_func("log_line"))(message)
