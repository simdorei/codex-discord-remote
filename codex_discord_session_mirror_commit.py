from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

CursorUpdater = Callable[[str, str, int], Awaitable[None]]
OutputTargetDeactivator = Callable[[str], None]
LogFunc = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class SessionMirrorCommitDeps:
    update_session_mirror_cursor: CursorUpdater
    deactivate_session_mirror_output_target: OutputTargetDeactivator
    log: LogFunc


async def commit_session_mirror_delivery(
    codex_thread_id: str,
    rollout_path: str,
    next_cursor: int,
    *,
    discord_thread_id: int,
    event_count: int,
    sent_count: int,
    terminal_sent: bool,
    deps: SessionMirrorCommitDeps,
) -> None:
    if terminal_sent:
        deps.deactivate_session_mirror_output_target(codex_thread_id)
    await deps.update_session_mirror_cursor(codex_thread_id, rollout_path, next_cursor)
    if sent_count:
        deps.log(
            f"session_mirror_sent target={codex_thread_id} channel={discord_thread_id} "
            + f"events={event_count} items={sent_count} cursor={next_cursor}"
        )
