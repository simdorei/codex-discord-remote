from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class CategoryLike(Protocol):
    @property
    def name(self) -> str | None: ...


class ParentLike(Protocol):
    @property
    def category(self) -> CategoryLike | None: ...


class MessageChannelLike(Protocol):
    @property
    def id(self) -> int | None: ...

    @property
    def parent_id(self) -> int | None: ...

    @property
    def parent(self) -> ParentLike | None: ...

    @property
    def category(self) -> CategoryLike | None: ...


ChannelIdPredicate = Callable[[int | None], bool]


def is_allowed_message_channel(
    channel: MessageChannelLike,
    *,
    is_allowed_channel_func: ChannelIdPredicate,
    is_mirrored_channel_id_func: ChannelIdPredicate,
) -> bool:
    channel_id = channel.id
    parent_id = channel.parent_id
    if is_allowed_channel_func(channel_id) or is_allowed_channel_func(parent_id):
        return True
    if is_mirrored_channel_id_func(channel_id) or is_mirrored_channel_id_func(parent_id):
        return True
    category = channel.category
    if category is None and channel.parent is not None:
        category = channel.parent.category
    return category is not None and category.name == "Codex"
