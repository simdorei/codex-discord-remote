"""Read-only list and synced views for GPT chat commands."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Final, NewType, TypeAlias, override

import codex_discord_gpt_candidates as candidates
from codex_discord_gpt_ownership import (
    CodexThreadId,
    DiscordChannelId,
    DiscordThreadId,
    MirrorThreadLifecycleState,
    MirrorThreadManagedBy,
    MirrorThreadOwnership,
)
from codex_discord_gpt_snapshots import (
    GptSnapshot,
    GptSnapshotKey,
    GptSnapshotKind,
    GptSnapshotStore,
)
from codex_thread_models import ThreadInfo

DEFAULT_GPT_LIST_COUNT: Final = 10
MIN_GPT_LIST_COUNT: Final = 1
MAX_GPT_LIST_COUNT: Final = 100
_DECIMAL_RE: Final = re.compile(r"[0-9]+")
_GPT_OWNERSHIP_QUERY: Final = (
    "SELECT codex_thread_id, project_key, thread_title, "
    "discord_channel_id, discord_thread_id, updated_at, managed_by, "
    "lifecycle_state FROM mirror_threads WHERE managed_by = ? "
    "ORDER BY updated_at DESC, codex_thread_id"
)

GptListCount = NewType("GptListCount", int)
LoadCandidates: TypeAlias = Callable[[int], tuple[ThreadInfo, ...]]
LoadMappings: TypeAlias = Callable[[Path], tuple[MirrorThreadOwnership, ...]]
LookupSource: TypeAlias = Callable[
    [CodexThreadId, tuple[ThreadInfo, ...]],
    ThreadInfo | None,
]
_OwnershipRow: TypeAlias = tuple[str, str, str, int, int, float, str, str]


@unique
class GptListCountErrorReason(StrEnum):
    MALFORMED = "malformed"
    OUT_OF_RANGE = "out-of-range"


@dataclass(frozen=True, slots=True)
class GptListCountError(RuntimeError):
    raw_count: str
    reason: GptListCountErrorReason

    @override
    def __str__(self) -> str:
        shown = self.raw_count[:32] or "<empty>"
        return f"Invalid GPT list count ({self.reason.value}): {shown}."


@unique
class GptSourceStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@unique
class GptParentStatus(StrEnum):
    CONFIGURED = "configured"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True)
class GptListItem:
    index: int
    thread: ThreadInfo


@dataclass(frozen=True, slots=True)
class GptListResult:
    count: GptListCount
    items: tuple[GptListItem, ...]
    snapshot: GptSnapshot


@dataclass(frozen=True, slots=True)
class GptMappingReport:
    mapping: MirrorThreadOwnership
    source_status: GptSourceStatus
    parent_status: GptParentStatus


@dataclass(frozen=True, slots=True)
class GptSyncedItem(GptMappingReport):
    index: int


@dataclass(frozen=True, slots=True)
class GptSyncedResult:
    active: tuple[GptSyncedItem, ...]
    audit: tuple[GptMappingReport, ...]
    snapshot: GptSnapshot


def parse_gpt_list_count(raw_count: str | None) -> GptListCount:
    """Parse the optional command count into the accepted 1..100 range."""
    if raw_count is None:
        return GptListCount(DEFAULT_GPT_LIST_COUNT)
    normalized = raw_count.strip()
    if _DECIMAL_RE.fullmatch(normalized) is None:
        raise GptListCountError(
            raw_count=normalized,
            reason=GptListCountErrorReason.MALFORMED,
        )
    try:
        count = int(normalized)
    except ValueError:
        raise GptListCountError(
            raw_count=normalized,
            reason=GptListCountErrorReason.OUT_OF_RANGE,
        ) from None
    if count < MIN_GPT_LIST_COUNT or count > MAX_GPT_LIST_COUNT:
        raise GptListCountError(
            raw_count=normalized,
            reason=GptListCountErrorReason.OUT_OF_RANGE,
        )
    return GptListCount(count)


def _to_ownership(row: _OwnershipRow) -> MirrorThreadOwnership:
    return MirrorThreadOwnership(
        codex_thread_id=CodexThreadId(row[0]),
        project_key=row[1],
        thread_title=row[2],
        discord_channel_id=DiscordChannelId(row[3]),
        discord_thread_id=DiscordThreadId(row[4]),
        updated_at=row[5],
        managed_by=MirrorThreadManagedBy(row[6]),
        lifecycle_state=MirrorThreadLifecycleState(row[7]),
    )


def load_gpt_mappings_read_only(
    db_path: Path,
) -> tuple[MirrorThreadOwnership, ...]:
    """Load GPT ownership without creating or changing persistence."""
    database_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as conn:
        rows: list[_OwnershipRow] = conn.execute(
            _GPT_OWNERSHIP_QUERY,
            (MirrorThreadManagedBy.GPT_CHAT.value,),
        ).fetchall()
    return tuple(_to_ownership(row) for row in rows)


def lookup_gpt_source(
    codex_thread_id: CodexThreadId,
    available_sources: tuple[ThreadInfo, ...],
) -> ThreadInfo | None:
    for source in available_sources:
        if source.id == codex_thread_id:
            return source
    return None


@dataclass(frozen=True, slots=True)
class GptReadDeps:
    load_candidates: LoadCandidates
    load_mappings: LoadMappings
    snapshot_store: GptSnapshotStore
    lookup_source: LookupSource


def create_gpt_read_deps(snapshot_store: GptSnapshotStore) -> GptReadDeps:
    return GptReadDeps(
        load_candidates=candidates.load_gpt_candidates,
        load_mappings=load_gpt_mappings_read_only,
        snapshot_store=snapshot_store,
        lookup_source=lookup_gpt_source,
    )


def _is_list_selectable(
    thread: ThreadInfo,
    mappings_by_id: Mapping[str, MirrorThreadOwnership],
) -> bool:
    mapping = mappings_by_id.get(thread.id)
    if mapping is None or mapping.managed_by is not MirrorThreadManagedBy.GPT_CHAT:
        return True
    return mapping.lifecycle_state not in (
        MirrorThreadLifecycleState.DEACTIVATING,
        MirrorThreadLifecycleState.REACTIVATING,
    )


@dataclass(frozen=True, slots=True)
class GptReadService:
    db_path: Path
    deps: GptReadDeps

    def list_candidates(
        self,
        key: GptSnapshotKey,
        raw_count: str | None = None,
    ) -> GptListResult:
        count = parse_gpt_list_count(raw_count)
        mappings_by_id = {
            str(mapping.codex_thread_id): mapping
            for mapping in self.deps.load_mappings(self.db_path)
        }
        displayed = tuple(
            thread
            for thread in self.deps.load_candidates(0)
            if _is_list_selectable(thread, mappings_by_id)
        )[:count]
        items = tuple(
            GptListItem(index=index, thread=thread)
            for index, thread in enumerate(displayed, start=1)
        )
        snapshot = self.deps.snapshot_store.replace(
            key,
            GptSnapshotKind.LIST,
            tuple(thread.id for thread in displayed),
        )
        return GptListResult(count=count, items=items, snapshot=snapshot)

    def list_synced(self, key: GptSnapshotKey) -> GptSyncedResult:
        available_sources = self.deps.load_candidates(0)
        active: list[GptSyncedItem] = []
        audit: list[GptMappingReport] = []
        for mapping in self.deps.load_mappings(self.db_path):
            report = GptMappingReport(
                mapping=mapping,
                source_status=(
                    GptSourceStatus.AVAILABLE
                    if self.deps.lookup_source(
                        mapping.codex_thread_id,
                        available_sources,
                    )
                    is not None
                    else GptSourceStatus.UNAVAILABLE
                ),
                parent_status=(
                    GptParentStatus.CONFIGURED
                    if mapping.discord_channel_id == key.configured_general_channel_id
                    else GptParentStatus.LEGACY
                ),
            )
            if mapping.is_active_gpt:
                active.append(
                    GptSyncedItem(
                        mapping=report.mapping,
                        source_status=report.source_status,
                        parent_status=report.parent_status,
                        index=len(active) + 1,
                    )
                )
            else:
                audit.append(report)
        snapshot = self.deps.snapshot_store.replace(
            key,
            GptSnapshotKind.SYNCED,
            tuple(item.mapping.codex_thread_id for item in active),
        )
        return GptSyncedResult(
            active=tuple(active),
            audit=tuple(audit),
            snapshot=snapshot,
        )
