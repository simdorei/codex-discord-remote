"""SQLite-backed Discord adapter persistence helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from codex_discord_gpt_ownership import (
    CodexThreadId as CodexThreadId,
    DiscordChannelId as DiscordChannelId,
    DiscordOwnershipConflictError as DiscordOwnershipConflictError,
    DiscordThreadId as DiscordThreadId,
    GptOwnershipOverwriteError as GptOwnershipOverwriteError,
    MirrorThreadLifecycleState as MirrorThreadLifecycleState,
    MirrorThreadManagedBy as MirrorThreadManagedBy,
    MirrorThreadOwnership as MirrorThreadOwnership,
    get_active_gpt_mirror_thread_by_discord_thread_id as get_active_gpt_mirror_thread_by_discord_thread_id,
    get_mirror_thread_owner_by_codex_thread_id as get_mirror_thread_owner_by_codex_thread_id,
    get_mirror_thread_owner_by_discord_thread_id as get_mirror_thread_owner_by_discord_thread_id,
    list_ordinary_mirror_threads as list_ordinary_mirror_threads,
)
from codex_discord_gpt_lifecycle import (
    GPT_CHAT_CAPACITY as GPT_CHAT_CAPACITY,
    GptCapacityAudit as GptCapacityAudit,
    GptCapacityExceededError as GptCapacityExceededError,
    GptCapacityRequestError as GptCapacityRequestError,
    GptLifecycleError as GptLifecycleError,
    GptLifecycleOperation as GptLifecycleOperation,
    GptLifecycleOwnerError as GptLifecycleOwnerError,
    GptLifecycleProjectError as GptLifecycleProjectError,
    GptLifecycleStateError as GptLifecycleStateError,
    GptLifecycleTransition as GptLifecycleTransition,
    GptLifecycleTransitionError as GptLifecycleTransitionError,
    GptMappingNotFoundError as GptMappingNotFoundError,
    audit_gpt_capacity as audit_gpt_capacity,
    transition_gpt_lifecycle as transition_gpt_lifecycle,
)
from codex_discord_store_busy import (
    claim_busy_choice_record as claim_busy_choice_record,
    claim_persistent_component_interaction as claim_persistent_component_interaction,
    cleanup_expired_busy_choices as cleanup_expired_busy_choices,
    cleanup_expired_persistent_component_claims as cleanup_expired_persistent_component_claims,
    create_busy_choice_record as create_busy_choice_record,
    get_busy_choice_counts as get_busy_choice_counts,
    get_busy_choice_record as get_busy_choice_record,
    get_persistent_component_claim_counts as get_persistent_component_claim_counts,
)
from codex_discord_store_mirror_cleanup import (
    delete_archived_mirror_state as delete_archived_mirror_state,
    delete_stale_mirror_rows as delete_stale_mirror_rows,
    get_remaining_mirror_discord_ids as get_remaining_mirror_discord_ids,
    get_stale_mirror_project_rows as get_stale_mirror_project_rows,
    get_stale_mirror_thread_rows as get_stale_mirror_thread_rows,
    is_mirrored_channel_id as is_mirrored_channel_id,
)
from codex_discord_store_mirror_map import (
    ProjectKeysMatchFunc as ProjectKeysMatchFunc,
    describe_mirrored_project_channel as describe_mirrored_project_channel,
    find_mirror_project_row_by_key as find_mirror_project_row_by_key,
    get_mirror_project_for_channel as get_mirror_project_for_channel,
    merge_mirror_project_key_aliases as merge_mirror_project_key_aliases,
    upsert_mirror_project as upsert_mirror_project,
)
from codex_discord_store_mirror_threads import (
    get_mirror_thread_row_by_codex_thread_id as get_mirror_thread_row_by_codex_thread_id,
    get_mirrored_codex_thread_id as get_mirrored_codex_thread_id,
    get_ordinary_mirror_thread_row_by_codex_thread_id as get_ordinary_mirror_thread_row_by_codex_thread_id,
    get_ordinary_mirrored_codex_thread_id as get_ordinary_mirrored_codex_thread_id,
    update_mirror_thread_discord_thread_id as update_mirror_thread_discord_thread_id,
    upsert_ordinary_mirror_thread as upsert_ordinary_mirror_thread,
    upsert_mirror_thread as upsert_mirror_thread,
)
from codex_discord_store_processed_messages import (
    claim_persistent_discord_message_id as claim_persistent_discord_message_id,
    cleanup_processed_discord_messages as cleanup_processed_discord_messages,
    is_processed_discord_message_id as is_processed_discord_message_id,
    mark_processed_discord_message_id as mark_processed_discord_message_id,
)
from codex_discord_store_schema import init_store_schema
from codex_discord_store_session_mirror import (
    claim_session_mirror_event as claim_session_mirror_event,
    cleanup_session_mirror_events as cleanup_session_mirror_events,
    get_or_init_session_mirror_cursor as get_or_init_session_mirror_cursor,
    get_session_mirror_offset as get_session_mirror_offset,
    get_session_mirror_targets as get_session_mirror_targets,
    has_session_mirror_event as has_session_mirror_event,
    update_session_mirror_cursor as update_session_mirror_cursor,
)
from codex_discord_store_startup_probe import (
    get_startup_probe_targets as get_startup_probe_targets,
)

def init_mirror_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        init_store_schema(conn)
