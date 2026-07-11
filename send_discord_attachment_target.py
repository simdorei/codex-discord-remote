from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import TypeAlias, assert_never

import codex_desktop_bridge_thread_store as thread_store
import codex_discord_gpt_creation_journal as gpt_creation_journal
import codex_discord_gpt_ownership as gpt_ownership
import codex_discord_project_runtime as project_runtime
from codex_thread_models import ThreadInfo
from send_discord_attachment_types import AttachmentCliArgs, AttachmentTargetError

SCRIPT_DIR = Path(__file__).resolve().parent
MIRROR_DB_ENV = "CODEX_DISCORD_MIRROR_DB"
DEFAULT_MIRROR_DB_PATH = SCRIPT_DIR / "discord_mirror.sqlite"


@unique
class AttachmentTargetKind(StrEnum):
    RAW_CHANNEL = "channel_id"
    THREAD_REF = "thread_ref"
    WORK_THREAD = "work_thread"


AttachmentMappingIdentity: TypeAlias = tuple[str, str, str, int, int, str, str]


@dataclass(frozen=True, slots=True)
class ResolvedAttachmentTarget:
    channel_id: str
    kind: AttachmentTargetKind
    codex_thread_id: str | None
    exact_safety: project_runtime.ExactChannelSafetyResult
    mapping_identity: AttachmentMappingIdentity | None
    mirror_target: bool


def _mapping_identity(
    owner: gpt_ownership.MirrorThreadOwnership,
) -> AttachmentMappingIdentity:
    return (
        str(owner.codex_thread_id),
        owner.project_key,
        owner.thread_title,
        int(owner.discord_channel_id),
        int(owner.discord_thread_id),
        owner.managed_by.value,
        owner.lifecycle_state.value,
    )


def _load_owner_by_codex_thread_id(
    db_path: Path,
    codex_thread_id: str,
) -> gpt_ownership.MirrorThreadOwnership | None:
    return gpt_ownership.get_mirror_thread_owner_by_codex_thread_id(
        db_path,
        codex_thread_id,
    )


def _load_owner_by_discord_thread_id(
    db_path: Path,
    discord_thread_id: int,
) -> gpt_ownership.MirrorThreadOwnership | None:
    try:
        return gpt_ownership.get_mirror_thread_owner_by_discord_thread_id(
            db_path,
            discord_thread_id,
        )
    except gpt_ownership.DiscordOwnershipConflictError as exc:
        raise AttachmentTargetError(
            "attachment target has conflicting mapping identities"
        ) from exc


def mirror_db_path() -> Path:
    configured = os.environ.get(MIRROR_DB_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_MIRROR_DB_PATH


def resolve_thread_from_ref(thread_ref: str) -> ThreadInfo:
    try:
        return thread_store.resolve_thread_ref(thread_ref)
    except thread_store.ThreadStoreError as active_error:
        try:
            return thread_store.resolve_archived_thread_ref(thread_ref)
        except thread_store.ThreadStoreError as archived_error:
            raise AttachmentTargetError(
                f"not in active/archived threads: {thread_ref}; "
                + f"active={active_error}; archived={archived_error}"
            ) from archived_error


def resolve_mirrored_channel_id(thread_ref: str) -> str:
    return _resolve_ref_target(
        thread_ref,
        AttachmentTargetKind.THREAD_REF,
        mirror_db_path(),
    ).channel_id


def _raise_blocked(reason: project_runtime.ExactChannelBlockReason) -> None:
    raise AttachmentTargetError(f"attachment target blocked: {reason.value}")


def _journal_reason(
    operation: gpt_creation_journal.GptCreationOperation,
) -> project_runtime.ExactChannelBlockReason:
    if operation.discord_thread_id is None:
        return project_runtime.ExactChannelBlockReason.CREATION_JOURNAL_MARKER
    return project_runtime.ExactChannelBlockReason.CREATION_JOURNAL_ID


def _inactive_reason(
    state: gpt_ownership.MirrorThreadLifecycleState,
) -> project_runtime.ExactChannelBlockReason:
    match state:
        case gpt_ownership.MirrorThreadLifecycleState.DEACTIVATING:
            return project_runtime.ExactChannelBlockReason.DEACTIVATING
        case gpt_ownership.MirrorThreadLifecycleState.INACTIVE:
            return project_runtime.ExactChannelBlockReason.INACTIVE
        case gpt_ownership.MirrorThreadLifecycleState.REACTIVATING:
            return project_runtime.ExactChannelBlockReason.REACTIVATING
        case gpt_ownership.MirrorThreadLifecycleState.ACTIVE:
            raise AttachmentTargetError(
                "attachment target lifecycle check expected inactive state"
            )
        case _:
            assert_never(state)


def _validate_ref_safety(
    safety: project_runtime.ExactChannelSafetyResult,
    owner: gpt_ownership.MirrorThreadOwnership,
    expected_codex_thread_id: str,
) -> None:
    match safety:
        case project_runtime.ExactChannelBlocked(reason=reason):
            raise AttachmentTargetError(f"attachment target blocked: {reason}")
        case project_runtime.ExactChannelActive(codex_thread_id=codex_thread_id):
            if codex_thread_id != expected_codex_thread_id:
                raise AttachmentTargetError(
                    "attachment target mapping identity changed"
                )
            return
        case project_runtime.ExactChannelUnknown():
            if owner.managed_by is not gpt_ownership.MirrorThreadManagedBy.ORDINARY:
                raise AttachmentTargetError(
                    "attachment target mapping identity is unknown"
                )
            return
        case _:
            assert_never(safety)


def _raw_codex_thread_id(
    safety: project_runtime.ExactChannelSafetyResult,
    owner: gpt_ownership.MirrorThreadOwnership | None,
) -> str | None:
    match safety:
        case project_runtime.ExactChannelBlocked(reason=reason):
            raise AttachmentTargetError(f"attachment target blocked: {reason}")
        case project_runtime.ExactChannelActive(codex_thread_id=codex_thread_id):
            if owner is None or str(owner.codex_thread_id) != codex_thread_id:
                raise AttachmentTargetError(
                    "attachment target mapping identity changed"
                )
            return codex_thread_id
        case project_runtime.ExactChannelUnknown():
            if owner is not None and not owner.is_ordinary:
                raise AttachmentTargetError(
                    "attachment target mapping identity is unknown"
                )
            return None
        case _:
            assert_never(safety)


def _resolve_ref_target(
    thread_ref: str,
    kind: AttachmentTargetKind,
    db_path: Path,
) -> ResolvedAttachmentTarget:
    thread = resolve_thread_from_ref(thread_ref)
    protections = gpt_creation_journal.load_gpt_creation_protections(db_path)
    for operation in protections.unfinished:
        if str(operation.codex_thread_id) == thread.id:
            _raise_blocked(_journal_reason(operation))
    owner = _load_owner_by_codex_thread_id(db_path, thread.id)
    if owner is None:
        raise AttachmentTargetError(
            f"stale mirror mapping: no Discord thread mapping for {thread.id}; "
            + "run !mirror check, then !mirror sync"
        )
    if owner.lifecycle_state is not gpt_ownership.MirrorThreadLifecycleState.ACTIVE:
        _raise_blocked(_inactive_reason(owner.lifecycle_state))
    safety = project_runtime.resolve_exact_channel_safety(
        db_path,
        int(owner.discord_thread_id),
        None,
    )
    _validate_ref_safety(safety, owner, thread.id)
    return ResolvedAttachmentTarget(
        str(int(owner.discord_thread_id)),
        kind,
        thread.id,
        safety,
        _mapping_identity(owner),
        True,
    )


def _resolve_raw_target(channel_id: str, db_path: Path) -> ResolvedAttachmentTarget:
    try:
        normalized_id = int(channel_id)
    except ValueError:
        raise AttachmentTargetError(
            f"invalid Discord channel ID: {channel_id}"
        ) from None
    safety = project_runtime.resolve_exact_channel_safety(
        db_path,
        normalized_id,
        None,
    )
    owner = _load_owner_by_discord_thread_id(db_path, normalized_id)
    codex_thread_id = _raw_codex_thread_id(safety, owner)
    return ResolvedAttachmentTarget(
        str(normalized_id),
        AttachmentTargetKind.RAW_CHANNEL,
        codex_thread_id,
        safety,
        None if owner is None else _mapping_identity(owner),
        False,
    )


def resolve_attachment_target(
    args: AttachmentCliArgs,
    *,
    db_path: Path | None = None,
) -> ResolvedAttachmentTarget:
    resolved_db_path = mirror_db_path() if db_path is None else db_path
    if args.channel_id:
        return _resolve_raw_target(args.channel_id, resolved_db_path)
    if args.thread_ref:
        return _resolve_ref_target(
            args.thread_ref,
            AttachmentTargetKind.THREAD_REF,
            resolved_db_path,
        )
    if args.work_thread:
        return _resolve_ref_target(
            args.work_thread,
            AttachmentTargetKind.WORK_THREAD,
            resolved_db_path,
        )
    raise AttachmentTargetError(
        "missing Discord target: use --channel-id, --thread-ref, or --work-thread"
    )


def revalidate_attachment_target(
    args: AttachmentCliArgs,
    expected: ResolvedAttachmentTarget,
    *,
    db_path: Path | None = None,
) -> None:
    current = resolve_attachment_target(args, db_path=db_path)
    if current != expected:
        raise AttachmentTargetError("attachment target changed before Discord POST")


def resolve_target_channel_id(args: AttachmentCliArgs) -> tuple[str, bool]:
    target = resolve_attachment_target(args)
    return target.channel_id, target.mirror_target
