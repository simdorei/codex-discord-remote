from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypedDict

LogFunc = Callable[[str], None]
ExceptionTypes = tuple[type[BaseException], ...]


class ArchiveMirrorCleanupOwner(Protocol):
    _session_mirror_archive_skip_logged: set[str]
    _session_mirror_seen_agent_messages: dict[str, dict[str, float]]
    _session_mirror_seen_user_messages: dict[str, dict[str, float]]

    def archive_skip_logged(self) -> set[str]:
        return self._session_mirror_archive_skip_logged

    def seen_agent_messages(self) -> dict[str, dict[str, float]]:
        return self._session_mirror_seen_agent_messages

    def seen_user_messages(self) -> dict[str, dict[str, float]]:
        return self._session_mirror_seen_user_messages


class SessionMirrorStateLike(Protocol):
    active_output_targets: set[str]
    pending_cursor_targets: set[str]


class ArchivedSessionMirrorCleanupCounts(TypedDict):
    mirror_threads: int
    session_mirror_offsets: int
    active_output_targets: int
    pending_cursor_targets: int
    archive_skip_logged: int
    seen_agent_messages: int
    seen_user_messages: int


@dataclass(frozen=True, slots=True)
class ArchiveMirrorCleanupDeps:
    delete_archived_mirror_state: Callable[[str], Mapping[str, int]]
    get_session_mirror_state: Callable[[], SessionMirrorStateLike]
    normalize_runner_key: Callable[[str | None], str]
    deactivate_session_mirror_output_target: Callable[[str | None], None]
    parse_bridge_output_value: Callable[[str, str], str]
    format_log_argv: Callable[[list[str]], str]
    exception_types: ExceptionTypes
    format_exception: Callable[[], str]
    log: LogFunc


def resolve_session_mirror_archive_policy(
    codex_thread_id: str,
    *,
    archive_recommended: bool,
    active_output_target: bool,
    archive_skip_logged: set[str],
    log: LogFunc,
) -> bool:
    if archive_recommended and not active_output_target:
        if codex_thread_id not in archive_skip_logged:
            archive_skip_logged.add(codex_thread_id)
            log(
                f"session_mirror_archive_tail_only target={codex_thread_id} reason=archive_recommended"
            )
        return True
    if active_output_target and codex_thread_id in archive_skip_logged:
        log(
            f"session_mirror_archive_skip_overridden target={codex_thread_id} reason=active_ask"
        )
    archive_skip_logged.discard(codex_thread_id)
    return False


def cleanup_archived_session_mirror_state(
    owner: ArchiveMirrorCleanupOwner | None,
    codex_thread_id: str,
    *,
    deps: ArchiveMirrorCleanupDeps,
) -> ArchivedSessionMirrorCleanupCounts:
    counts = deps.delete_archived_mirror_state(codex_thread_id)
    if int(counts.get("destructive_cleanup_allowed", 1)) != 1:
        return {
            "mirror_threads": 0,
            "session_mirror_offsets": 0,
            "active_output_targets": 0,
            "pending_cursor_targets": 0,
            "archive_skip_logged": 0,
            "seen_agent_messages": 0,
            "seen_user_messages": 0,
        }
    state = deps.get_session_mirror_state()
    key = deps.normalize_runner_key(codex_thread_id)
    active_output_targets = int(key in state.active_output_targets)
    pending_cursor_targets = int(key in state.pending_cursor_targets)
    deps.deactivate_session_mirror_output_target(codex_thread_id)

    archive_skip_logged = 0
    seen_agent_messages = 0
    seen_user_messages = 0
    if owner is not None:
        skip_logged = ArchiveMirrorCleanupOwner.archive_skip_logged(owner)
        archive_skip_logged = int(codex_thread_id in skip_logged)
        skip_logged.discard(codex_thread_id)
        seen_agent_messages = int(
            ArchiveMirrorCleanupOwner.seen_agent_messages(owner).pop(
                codex_thread_id,
                None,
            )
            is not None
        )
        seen_user_messages = int(
            ArchiveMirrorCleanupOwner.seen_user_messages(owner).pop(
                codex_thread_id,
                None,
            )
            is not None
        )

    return {
        "mirror_threads": int(counts.get("mirror_threads", 0)),
        "session_mirror_offsets": int(counts.get("session_mirror_offsets", 0)),
        "active_output_targets": active_output_targets,
        "pending_cursor_targets": pending_cursor_targets,
        "archive_skip_logged": archive_skip_logged,
        "seen_agent_messages": seen_agent_messages,
        "seen_user_messages": seen_user_messages,
    }


def cleanup_archive_mirror_after_bridge_command(
    owner: ArchiveMirrorCleanupOwner | None,
    argv: list[str],
    exit_code: int,
    output: str,
    *,
    deps: ArchiveMirrorCleanupDeps,
) -> str | None:
    if exit_code != 0 or not argv or argv[0] != "archive":
        return None
    archived_thread_id = deps.parse_bridge_output_value(output, "archived_thread")
    if not archived_thread_id:
        deps.log(
            "archive_mirror_cleanup_skipped reason=no_archived_thread "
            + f"argv={deps.format_log_argv(argv)}"
        )
        return None
    try:
        counts = cleanup_archived_session_mirror_state(
            owner, archived_thread_id, deps=deps
        )
    except deps.exception_types as exc:
        deps.log(
            f"archive_mirror_cleanup_failed target={archived_thread_id} "
            + f"error_type={type(exc).__name__}\n"
            + deps.format_exception()
        )
        return f"Mirror cleanup warning: {type(exc).__name__}: {exc}"
    deps.log(
        f"archive_mirror_cleanup_done target={archived_thread_id} "
        + f"mirror_rows={counts['mirror_threads']} "
        + f"offsets={counts['session_mirror_offsets']} "
        + f"active={counts['active_output_targets']} "
        + f"pending={counts['pending_cursor_targets']} "
        + f"seen_agent={counts['seen_agent_messages']} "
        + f"seen_user={counts['seen_user_messages']}"
    )
    return None
