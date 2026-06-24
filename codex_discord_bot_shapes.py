from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Protocol, TypeGuard, cast

import discord

import codex_discord_busy_prompt as discord_busy_prompt
import codex_discord_component_view_state as discord_component_view_state
import codex_discord_slash_prompt_commands as discord_slash_prompt_commands

class BusyChoiceAuthor(discord_busy_prompt.BusyPromptAuthor, Protocol):
    @property
    def id(self) -> int: ...


class BusyChoiceSourceMessage(Protocol):
    @property
    def channel(self) -> discord.abc.Messageable: ...

    @property
    def author(self) -> BusyChoiceAuthor: ...


class BusyChoiceStoreIdLike(Protocol):
    @property
    def id(self) -> int | str | bytes | bytearray: ...


class BusyChoiceStoreMessageLike(Protocol):
    @property
    def author(self) -> BusyChoiceStoreIdLike: ...

    @property
    def channel(self) -> BusyChoiceStoreIdLike: ...


def require_busy_choice_store_message(
    message: BusyChoiceStoreMessageLike,
) -> BusyChoiceStoreMessageLike:
    return message


def require_component_view_children(
    children: Iterable[discord_component_view_state.ComponentViewChild],
) -> Iterable[discord_component_view_state.ComponentViewChild]:
    return children


DISCORD_UI_BUTTON_TYPE: Final[type[discord.ui.Button[discord.ui.View]]] = cast(
    type[discord.ui.Button[discord.ui.View]],
    discord.ui.Button,
)


def is_discord_button_item(
    item: discord_component_view_state.ComponentViewChild,
) -> TypeGuard[discord.ui.Button[discord.ui.View]]:
    return isinstance(item, DISCORD_UI_BUTTON_TYPE)


def require_interaction_message(interaction: discord.Interaction) -> discord.Message:
    return cast(discord.Message, cast(object, interaction.message))


@dataclass(frozen=True, slots=True)
class RuntimeBusyChoiceAuthor:
    id: int
    bot: bool


@dataclass(frozen=True, slots=True)
class RuntimeBusyChoiceSourceMessage:
    author: BusyChoiceAuthor
    channel: discord.abc.Messageable


class SessionMirrorOutputChannel(Protocol):
    pass


class ThreadContextUsageLike(Protocol):
    model_context_window: int
    peak_input_tokens: int
    last_total_tokens: int


class SyntheticQAView(Protocol):
    pass


class SyntheticQABot(Protocol):
    pass


class SyntheticQAChannel(Protocol):
    pass


class SyntheticQAMessage(Protocol):
    pass


class SyntheticQAUser(Protocol):
    pass


@dataclass(frozen=True, slots=True)
class SkillSlashSourceAuthor:
    user: discord_slash_prompt_commands.PromptUser

    @property
    def id(self) -> int:
        return int(self.user.id)

    @property
    def bot(self) -> bool:
        return bool(getattr(self.user, "bot", False))


@dataclass(frozen=True, slots=True)
class SlashAskSourceMessage:
    channel: discord.abc.Messageable
    author: BusyChoiceAuthor
