from __future__ import annotations

from codex_discord_attachment_metadata import sanitize_attachment_filename
from codex_discord_delivery_runtime import is_attachment_source_allowed
import codex_discord_session_mirror_item_builders as item_builders
import codex_discord_session_mirror_item_events as item_events
from codex_discord_session_mirror_item_append import (
    BuildInteractiveNoticeFunc,
    CollectionContext,
    ExtractMessageTextFunc,
    SessionPayload,
    SkipDiscordOriginPromptFunc,
    append_agent_if_new as _append_agent_if_new,
    append_item as _append_item,
    append_user_if_new as _append_user_if_new,
)
from codex_session_events import JsonValue
from codex_discord_session_mirror_item_builders import (
    SessionEvent,
    SessionMirrorItem,
    TextDigestFunc,
)

INTERNAL_RESPONSE_USER_PREFIXES = (
    "# AGENTS.md instructions",
    "<INSTRUCTIONS>",
    "<environment_context",
    "<codex_internal_context",
)
CODEX_IMAGE_OUTPUT_TEXT = "Codex image output"
CODEX_IMAGE_OUTPUT_FILENAME = "codex-image-output.png"
CODEX_FILE_OUTPUT_TEXT = "Codex file output"
CODEX_FILE_OUTPUT_FILENAME = "codex-file-output.bin"
CODEX_FILE_OUTPUT_PART_TYPES = frozenset({"file", "input_file", "output_file"})
CODEX_FILE_DATA_FIELDS = ("file_data", "data_url", "file_url", "url")
CODEX_FILE_PATH_FIELDS = ("file_path", "path")
CODEX_FILE_NAME_FIELDS = ("filename", "download_name", "name")


def _first_string_field(payload: dict[str, JsonValue], field_names: tuple[str, ...]) -> str:
    for field_name in field_names:
        value = payload.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _append_image_item(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    image_url: str,
) -> None:
    _append_item(
        ctx,
        items,
        event,
        kind="image",
        role="assistant",
        phase="tool_image",
        text=CODEX_IMAGE_OUTPUT_TEXT,
    )
    item = items[-1]
    item["attachment_url"] = image_url
    item["attachment_filename"] = CODEX_IMAGE_OUTPUT_FILENAME


def _safe_attachment_filename(filename: str) -> str:
    return sanitize_attachment_filename(filename or CODEX_FILE_OUTPUT_FILENAME, 1)


def _file_attachment_source(payload: dict[str, JsonValue]) -> str:
    attachment_source = _first_string_field(payload, CODEX_FILE_DATA_FIELDS)
    if attachment_source and is_attachment_source_allowed(attachment_source):
        return attachment_source
    if attachment_source:
        return ""
    attachment_source = _first_string_field(payload, CODEX_FILE_PATH_FIELDS)
    if attachment_source and is_attachment_source_allowed(attachment_source):
        return attachment_source
    return ""


def _append_file_item(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    attachment_source: str,
    filename: str,
) -> None:
    source_filename = filename
    if not source_filename and not attachment_source.startswith("data:"):
        source_filename = attachment_source
    safe_filename = _safe_attachment_filename(source_filename)
    _append_item(
        ctx,
        items,
        event,
        kind="file",
        role="assistant",
        phase="tool_file",
        text=f"{CODEX_FILE_OUTPUT_TEXT}: {safe_filename}",
    )
    item = items[-1]
    item["attachment_url"] = attachment_source
    item["attachment_filename"] = safe_filename


def _collect_function_output_attachments(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    output: JsonValue,
) -> None:
    if not isinstance(output, list):
        return
    for part in output:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "input_image":
            part_type = str(part.get("type") or "")
            if part_type not in CODEX_FILE_OUTPUT_PART_TYPES:
                continue
            attachment_source = _file_attachment_source(part)
            if attachment_source:
                _append_file_item(
                    ctx,
                    items,
                    event,
                    attachment_source,
                    _first_string_field(part, CODEX_FILE_NAME_FIELDS),
                )
            continue
        image_url = part.get("image_url")
        if isinstance(image_url, str) and image_url.startswith("data:image/"):
            _append_image_item(ctx, items, event, image_url)


def _collect_function_item(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
    payload_type: str,
) -> bool:
    if payload_type == "function_call":
        notice = ctx.build_interactive_notice(payload)
        if notice:
            _append_item(ctx, items, event, kind="interactive", role="assistant", phase="interactive", text=notice)
        return True
    if payload_type == "function_call_output":
        _collect_function_output_attachments(ctx, items, event, payload.get("output"))
        output_text = str(payload.get("output") or "").strip()
        if output_text and "rejected by user" in output_text.lower():
            _append_item(
                ctx,
                items,
                event,
                kind="commentary",
                role="assistant",
                phase="approval_rejected",
                text="[approval_rejected]\nCommand approval was rejected by user.",
            )
        return True
    return False


def _collect_response_message(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
) -> None:
    text = ctx.extract_message_text(payload)
    if not text:
        return
    role = str(payload.get("role") or "?")
    phase = str(payload.get("phase") or "")
    if role == "assistant" and phase == "commentary":
        _append_agent_if_new(ctx, items, event, text, kind="commentary", phase=phase)
        return
    if role == "assistant" and phase == "final_answer":
        _append_agent_if_new(ctx, items, event, text, kind="final", phase=phase)
        return
    if role == "user" and not text.lstrip().startswith(INTERNAL_RESPONSE_USER_PREFIXES):
        _append_user_if_new(ctx, items, event, text, phase)


def _collect_response_item(
    ctx: CollectionContext,
    items: list[SessionMirrorItem],
    event: SessionEvent,
    payload: SessionPayload,
) -> None:
    payload_type = str(payload.get("type") or "")
    if _collect_function_item(ctx, items, event, payload, payload_type):
        return
    if payload_type == "message":
        _collect_response_message(ctx, items, event, payload)


def collect_session_mirror_items(
    codex_thread_id: str,
    events: list[SessionEvent],
    *,
    seen_agent_messages: dict[str, float],
    seen_user_messages: dict[str, float],
    should_skip_discord_origin_prompt_func: SkipDiscordOriginPromptFunc,
    build_interactive_notice_func: BuildInteractiveNoticeFunc,
    extract_message_text_func: ExtractMessageTextFunc,
    recent_text_ttl_seconds: float,
    make_text_digest_func: TextDigestFunc = item_builders.make_text_digest,
) -> list[SessionMirrorItem]:
    ctx = CollectionContext(
        codex_thread_id=codex_thread_id,
        seen_agent_messages=seen_agent_messages,
        seen_user_messages=seen_user_messages,
        should_skip_discord_origin_prompt=should_skip_discord_origin_prompt_func,
        build_interactive_notice=build_interactive_notice_func,
        extract_message_text=extract_message_text_func,
        recent_text_ttl_seconds=recent_text_ttl_seconds,
        make_text_digest=make_text_digest_func,
    )
    items: list[SessionMirrorItem] = []
    for event in events:
        payload = item_events.event_payload(event)
        if payload is None:
            continue
        event_type = str(event.get("type") or "")
        if event_type == "event_msg":
            item_events.collect_event_message(ctx, items, event, payload)
        elif event_type == "response_item":
            _collect_response_item(ctx, items, event, payload)
    return items
