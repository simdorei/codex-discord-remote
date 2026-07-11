from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import override

from codex_discord_store_schema import init_store_schema


def _init_mirror_db(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        init_store_schema(conn)


@dataclass(frozen=True, slots=True)
class ReconciliationComplete:
    configured_channel_lock: asyncio.Lock


class ReconciliationRequiredError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Startup and history polling require completed GPT reconciliation."


@dataclass(frozen=True, slots=True)
class ReconciledStartupProbeTargets:
    prerequisite: ReconciliationComplete
    targets: tuple[tuple[str, int], ...]


def get_startup_probe_targets(
    db_path: Path,
    allowed_channel_ids: set[int],
    startup_channel_id: int | None,
    *,
    limit: int = 30,
) -> list[tuple[str, int]]:
    seen: set[int] = set()
    targets: list[tuple[str, int]] = []

    def add(label: str, channel_id: int | None) -> None:
        if not channel_id:
            return
        normalized = int(channel_id)
        if normalized in seen or len(targets) >= limit:
            return
        seen.add(normalized)
        targets.append((label, normalized))

    add("startup", startup_channel_id)
    for channel_id in sorted(allowed_channel_ids):
        add("allowed", channel_id)

    _init_mirror_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        project_rows: list[tuple[int]] = conn.execute(
            "SELECT project.discord_channel_id FROM mirror_projects AS project "
            + "WHERE project.project_key <> 'codex:chats' AND EXISTS ("
            + "SELECT 1 FROM mirror_threads AS owned "
            + "WHERE owned.project_key = project.project_key "
            + "AND owned.managed_by = 'ordinary' "
            + "AND owned.lifecycle_state = 'active' "
            + "AND (SELECT COUNT(*) FROM mirror_threads AS duplicate "
            + "WHERE duplicate.discord_thread_id = owned.discord_thread_id) = 1) "
            + "ORDER BY project.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        for row in project_rows:
            add("mirror_project", int(row[0]))
        thread_rows: list[tuple[int]] = conn.execute(
            "SELECT owned.discord_thread_id FROM mirror_threads AS owned "
            + "WHERE owned.lifecycle_state = 'active' AND ("
            + "(owned.managed_by = 'ordinary' AND owned.project_key <> 'codex:chats') OR "
            + "(owned.managed_by = 'gpt_chat' AND owned.project_key = 'codex:chats')) "
            + "AND (SELECT COUNT(*) FROM mirror_threads AS duplicate "
            + "WHERE duplicate.discord_thread_id = owned.discord_thread_id) = 1 "
            + "ORDER BY owned.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        for row in thread_rows:
            add("mirror_thread", int(row[0]))
    return targets


def get_reconciled_startup_probe_targets(
    reconciliation: ReconciliationComplete | None,
    db_path: Path,
    allowed_channel_ids: set[int],
    startup_channel_id: int | None,
    *,
    limit: int = 30,
) -> ReconciledStartupProbeTargets:
    if reconciliation is None:
        raise ReconciliationRequiredError()
    return ReconciledStartupProbeTargets(
        prerequisite=reconciliation,
        targets=tuple(
            get_startup_probe_targets(
                db_path,
                allowed_channel_ids,
                startup_channel_id,
                limit=limit,
            )
        ),
    )
