from __future__ import annotations

from pathlib import Path

from chatgpt_app_mirror_models import (
    ChatGptMirrorCyclePlan,
    ChatGptMirrorDelivery,
    ChatGptSnapshot,
)
from chatgpt_app_mirror_store import (
    claim_seen_chatgpt_turn,
    ensure_chatgpt_mirror_slots,
    has_seen_chatgpt_turn,
    is_chatgpt_conversation_primed,
    prime_chatgpt_conversation,
)


def prepare_mirror_cycle(
    db_path: Path,
    snapshot: ChatGptSnapshot,
    discord_thread_ids: tuple[int, ...],
) -> ChatGptMirrorCyclePlan:
    slots = ensure_chatgpt_mirror_slots(
        db_path,
        snapshot.recent_conversations,
        discord_thread_ids,
    )
    active_conversation_id = snapshot.active_conversation_id
    active_slot = next(
        (
            slot
            for slot in slots
            if slot.conversation_id == active_conversation_id
        ),
        None,
    )
    if active_slot is None or active_conversation_id is None:
        return ChatGptMirrorCyclePlan(
            deliveries=(),
            active_mapped=False,
            primed=False,
        )
    if not is_chatgpt_conversation_primed(db_path, active_conversation_id):
        prime_chatgpt_conversation(
            db_path,
            active_conversation_id,
            snapshot.turns,
        )
        return ChatGptMirrorCyclePlan(
            deliveries=(),
            active_mapped=True,
            primed=True,
        )
    deliveries = tuple(
        ChatGptMirrorDelivery(
            conversation_id=active_conversation_id,
            discord_thread_id=active_slot.discord_thread_id,
            turn=turn,
        )
        for turn in snapshot.turns
        if turn.complete
        and not has_seen_chatgpt_turn(
            db_path,
            active_conversation_id,
            turn.message_id,
        )
    )
    return ChatGptMirrorCyclePlan(
        deliveries=deliveries,
        active_mapped=True,
        primed=False,
    )


def mark_mirror_delivery(db_path: Path, delivery: ChatGptMirrorDelivery) -> bool:
    return claim_seen_chatgpt_turn(
        db_path,
        delivery.conversation_id,
        delivery.turn,
    )
