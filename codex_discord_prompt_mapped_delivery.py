from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


ChannelContraT = TypeVar("ChannelContraT", contravariant=True)
ChannelT = TypeVar("ChannelT")
LogFunc = Callable[[str], None]
TextLenFunc = Callable[[str | None], int]
OutputPredicate = Callable[[str], bool]
BusyPredicate = Callable[[int, str], bool]
PendingFormatter = Callable[[str], str]
OutputTargetDeactivator = Callable[[str | None], None]
SelectedThreadSetter = Callable[[str], None]


class PrepareMappedSessionMirrorOutput(Protocol[ChannelContraT]):
    def __call__(self, channel: ChannelContraT, target_thread_id: str | None) -> Awaitable[bool]: ...


class ChannelTyping(Protocol[ChannelContraT]):
    def __call__(
        self,
        channel: ChannelContraT,
        *,
        context: str,
    ) -> AbstractAsyncContextManager[None]: ...


class TransportNoWait(Protocol):
    def __call__(self, prompt: str, target_thread_id: str | None) -> Awaitable[tuple[int, str]]: ...


class ChunkSender(Protocol[ChannelContraT]):
    def __call__(
        self,
        channel: ChannelContraT,
        content: str,
        *,
        context: str | None = None,
    ) -> Awaitable[None]: ...


class AppMenuSender(Protocol[ChannelContraT]):
    def __call__(
        self,
        channel: ChannelContraT,
        target_thread_id: str | None,
        output: str,
        *,
        reason: str,
    ) -> Awaitable[bool]: ...


@dataclass(frozen=True, slots=True)
class MappedPromptDeliveryDeps(Generic[ChannelT]):
    prepare_mapped_session_mirror_output: PrepareMappedSessionMirrorOutput[ChannelT]
    set_selected_thread_id: SelectedThreadSetter
    channel_typing: ChannelTyping[ChannelT]
    run_transport_prompt_no_wait: TransportNoWait
    send_chunks: ChunkSender[ChannelT]
    is_delivery_confirmation_timeout: OutputPredicate
    format_pending_ask_delivery_output: PendingFormatter
    deactivate_session_mirror_output_target: OutputTargetDeactivator
    is_selected_thread_busy_error: BusyPredicate
    send_codex_app_menu_if_available: AppMenuSender[ChannelT]
    format_log_text_len: TextLenFunc
    log: LogFunc


@dataclass(frozen=True, slots=True)
class MappedPromptDeliveryResult:
    handled: bool


def format_mapped_transport_failure(exit_code: int, output: str) -> str:
    if "Prompt landed in a different thread" in output:
        return "Ask failed: Codex recorded this message in a different thread. I did not resend it here."
    return f"Ask failed (transport exit {exit_code})\n\n{output or '(no output)'}"


async def handle_mapped_prompt_delivery(
    channel: ChannelT,
    prompt: str,
    target_thread_id: str | None,
    *,
    deps: MappedPromptDeliveryDeps[ChannelT],
) -> MappedPromptDeliveryResult:
    if not await deps.prepare_mapped_session_mirror_output(channel, target_thread_id):
        return MappedPromptDeliveryResult(handled=False)
    if target_thread_id:
        deps.set_selected_thread_id(target_thread_id)
        deps.log(f"mapped_prompt_selected_thread_synced target={target_thread_id}")

    async with deps.channel_typing(channel, context="ask_transport_no_wait"):
        exit_code, output = await deps.run_transport_prompt_no_wait(prompt, target_thread_id)
    deps.log(
        f"ask_transport_no_wait_done exit={exit_code} target={target_thread_id or '-'} "
        + f"output_len={deps.format_log_text_len(output)}"
    )
    if deps.is_delivery_confirmation_timeout(output):
        await deps.send_chunks(channel, deps.format_pending_ask_delivery_output(output))
        return MappedPromptDeliveryResult(handled=True)
    if exit_code == 0:
        return MappedPromptDeliveryResult(handled=True)
    deps.deactivate_session_mirror_output_target(target_thread_id)
    if deps.is_selected_thread_busy_error(exit_code, output) and await deps.send_codex_app_menu_if_available(
        channel,
        target_thread_id,
        output,
        reason="ask_transport_no_wait_busy",
    ):
        return MappedPromptDeliveryResult(handled=True)
    await deps.send_chunks(channel, format_mapped_transport_failure(exit_code, output))
    return MappedPromptDeliveryResult(handled=True)
