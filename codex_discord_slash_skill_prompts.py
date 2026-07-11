from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar, assert_never

import codex_discord_project_runtime as project_runtime


SourceMessageT = TypeVar("SourceMessageT")
SourceMessageT_contra = TypeVar("SourceMessageT_contra", contravariant=True)
SkillTargetSource = Literal["gpt", "mirror", "selected"]


class PromptUser(Protocol):
    @property
    def id(self) -> int: ...


class PromptChannel(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def name(self) -> str: ...


class SkillSlashInteraction(Protocol):
    @property
    def channel_id(self) -> int | None: ...

    @property
    def channel(self) -> PromptChannel | None: ...

    @property
    def user(self) -> PromptUser: ...


class InteractionChunksSender(Protocol):
    def __call__(
        self, interaction: SkillSlashInteraction, text: str, /, *, title: str
    ) -> Awaitable[None]: ...


class DirectFollowupSender(Protocol):
    def __call__(
        self,
        interaction: SkillSlashInteraction,
        text: str,
        /,
        *,
        ephemeral: bool,
        log_prefix: str,
        context: str,
    ) -> Awaitable[None]: ...


class PlainAskHandler(Protocol[SourceMessageT_contra]):
    def __call__(
        self,
        source_message: SourceMessageT_contra,
        prompt: str,
        /,
        *,
        target_thread_id: str | None,
    ) -> Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class SkillSlashPromptSpec:
    title: str
    log_name: str
    ack_message: str
    ack_context: str
    build_prompt: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class SkillSlashPromptDeps(Generic[SourceMessageT]):
    send_interaction_chunks: InteractionChunksSender
    send_direct_followup: DirectFollowupSender
    handle_plain_ask: PlainAskHandler[SourceMessageT]
    get_mirrored_codex_thread_id: Callable[[int | None], str | None]
    describe_mirrored_project_channel: Callable[[int | None], str]
    get_interaction_command_name: Callable[[SkillSlashInteraction], str]
    format_log_text_len: Callable[[str], str]
    make_source_message: Callable[[PromptChannel, PromptUser], SourceMessageT]
    log_line: Callable[[str], None]
    resolve_exact_channel_decision: (
        Callable[
            [int | None, str | None],
            project_runtime.ExactChannelDecision,
        ]
        | None
    ) = None


async def _dispatch_skill_slash_prompt(
    interaction: SkillSlashInteraction,
    prompt: str,
    wrapped_prompt: str,
    channel: PromptChannel,
    command_name: str,
    target_thread_id: str | None,
    target_source: SkillTargetSource,
    spec: SkillSlashPromptSpec,
    deps: SkillSlashPromptDeps[SourceMessageT],
) -> None:
    deps.log_line(
        f"{spec.log_name}_dispatch command={command_name} "
        + f"channel={interaction.channel_id} user={interaction.user.id} "
        + f"target_source={target_source} target={target_thread_id or '-'} "
        + f"prompt_len={deps.format_log_text_len(prompt)}"
    )
    await deps.send_direct_followup(
        interaction,
        spec.ack_message,
        ephemeral=True,
        log_prefix="slash_ack",
        context=spec.ack_context,
    )
    deps.log_line(
        f"{spec.log_name}_ack_sent command={command_name} channel={interaction.channel_id}"
    )
    await deps.handle_plain_ask(
        deps.make_source_message(channel, interaction.user),
        wrapped_prompt,
        target_thread_id=target_thread_id,
    )


async def handle_skill_slash_prompt(
    interaction: SkillSlashInteraction,
    prompt: str,
    *,
    spec: SkillSlashPromptSpec,
    deps: SkillSlashPromptDeps[SourceMessageT],
) -> None:
    channel = interaction.channel
    command_name = deps.get_interaction_command_name(interaction)
    if channel is None or not hasattr(channel, "send"):
        await deps.send_interaction_chunks(
            interaction,
            "This Discord interaction has no messageable channel.",
            title=spec.title,
        )
        return

    wrapped_prompt = spec.build_prompt(prompt)
    resolver = deps.resolve_exact_channel_decision
    safety: project_runtime.ExactChannelDecision
    if resolver is None:
        safety = project_runtime.ExactChannelUnknown()
    else:
        safety = resolver(interaction.channel_id, channel.name)

    match safety:
        case project_runtime.ExactChannelActive(codex_thread_id=target_thread_id):
            await _dispatch_skill_slash_prompt(
                interaction,
                prompt,
                wrapped_prompt,
                channel,
                command_name,
                target_thread_id,
                "gpt",
                spec,
                deps,
            )
            return
        case project_runtime.ExactChannelBlocked(reason=reason):
            deps.log_line(
                f"{spec.log_name}_blocked command={command_name} "
                + f"channel={interaction.channel_id} user={interaction.user.id} "
                + f"reason={reason} prompt_len={deps.format_log_text_len(prompt)}"
            )
            await deps.send_interaction_chunks(
                interaction,
                f"This Discord channel is blocked for GPT routing: {reason}.",
                title=spec.title,
            )
            return
        case project_runtime.ExactChannelUnknown():
            target_thread_id = deps.get_mirrored_codex_thread_id(interaction.channel_id)
            if target_thread_id is None:
                project_message = deps.describe_mirrored_project_channel(
                    interaction.channel_id
                )
                if project_message:
                    deps.log_line(
                        f"{spec.log_name}_blocked command={command_name} "
                        + f"channel={interaction.channel_id} user={interaction.user.id} "
                        + f"reason=project_parent prompt_len={deps.format_log_text_len(prompt)}"
                    )
                    await deps.send_interaction_chunks(
                        interaction, project_message, title=spec.title
                    )
                    return
            target_source: SkillTargetSource = (
                "mirror" if target_thread_id else "selected"
            )
            await _dispatch_skill_slash_prompt(
                interaction,
                prompt,
                wrapped_prompt,
                channel,
                command_name,
                target_thread_id,
                target_source,
                spec,
                deps,
            )
            return
        case _:
            assert_never(safety)
