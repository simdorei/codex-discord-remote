from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChatGptRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatGptConversation:
    conversation_id: str
    title: str


@dataclass(frozen=True, slots=True)
class ChatGptTurn:
    message_id: str
    role: ChatGptRole
    text: str
    complete: bool = True


@dataclass(frozen=True, slots=True)
class ChatGptSnapshot:
    recent_conversations: tuple[ChatGptConversation, ...]
    active_conversation_id: str | None
    turns: tuple[ChatGptTurn, ...]


@dataclass(frozen=True, slots=True)
class ChatGptMirrorSlot:
    slot_index: int
    conversation_id: str
    discord_thread_id: int


@dataclass(frozen=True, slots=True)
class ChatGptMirrorDelivery:
    conversation_id: str
    discord_thread_id: int
    turn: ChatGptTurn


@dataclass(frozen=True, slots=True)
class ChatGptMirrorCyclePlan:
    deliveries: tuple[ChatGptMirrorDelivery, ...]
    active_mapped: bool
    primed: bool
