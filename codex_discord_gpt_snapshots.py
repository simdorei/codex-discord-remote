from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
import re
import time
from typing import Final, final, override

GPT_SNAPSHOT_TTL_SECONDS: Final = 600.0
_POSITIVE_DECIMAL_RE: Final = re.compile(r"[0-9]+")


class GptSnapshotKind(StrEnum):
    LIST = "list"
    SYNCED = "synced"


@dataclass(frozen=True, slots=True)
class GptSnapshotKey:
    guild_id: int
    configured_general_channel_id: int
    user_id: int


@dataclass(frozen=True, slots=True)
class GptSnapshot:
    key: GptSnapshotKey
    kind: GptSnapshotKind
    codex_thread_ids: tuple[str, ...]
    saved_at: float


@dataclass(frozen=True, slots=True)
class GptSnapshotMissingError(RuntimeError):
    kind: GptSnapshotKind

    @override
    def __str__(self) -> str:
        return f"No saved {self.kind.value} GPT snapshot is available."


@dataclass(frozen=True, slots=True)
class GptSnapshotExpiredError(RuntimeError):
    kind: GptSnapshotKind
    age_seconds: float

    @override
    def __str__(self) -> str:
        return f"The saved {self.kind.value} GPT snapshot expired."


@dataclass(frozen=True, slots=True)
class GptSnapshotSelectionError(RuntimeError):
    item: str | None
    reason: str

    @override
    def __str__(self) -> str:
        return f"Invalid GPT snapshot selection ({self.reason}): {self.item or '<missing>'}."


def parse_csv_indices(raw: str | None) -> tuple[int, ...]:
    if raw is None:
        raise GptSnapshotSelectionError(item=None, reason="missing")

    selected: list[int] = []
    seen: set[int] = set()
    for raw_item in raw.split(","):
        item = raw_item.strip()
        if not item:
            raise GptSnapshotSelectionError(item=item, reason="empty")
        if _POSITIVE_DECIMAL_RE.fullmatch(item) is None:
            raise GptSnapshotSelectionError(item=item, reason="not-positive-decimal")
        try:
            index = int(item)
        except ValueError:
            raise GptSnapshotSelectionError(
                item=item[:32],
                reason="out-of-range",
            ) from None
        if index == 0:
            raise GptSnapshotSelectionError(item=item, reason="zero")
        if index not in seen:
            seen.add(index)
            selected.append(index)
    return tuple(selected)


@final
class GptSnapshotStore:
    """Mutable owner of immutable, per-user GPT command snapshots."""

    _monotonic: Callable[[], float]
    _snapshots: dict[tuple[GptSnapshotKey, GptSnapshotKind], GptSnapshot]

    __slots__: tuple[str, str] = ("_monotonic", "_snapshots")

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._snapshots = {}

    def replace(
        self,
        key: GptSnapshotKey,
        kind: GptSnapshotKind,
        codex_thread_ids: Sequence[str],
    ) -> GptSnapshot:
        snapshot = GptSnapshot(
            key=key,
            kind=kind,
            codex_thread_ids=tuple(codex_thread_ids),
            saved_at=self._monotonic(),
        )
        self._snapshots[(key, kind)] = snapshot
        return snapshot

    def get(self, key: GptSnapshotKey, kind: GptSnapshotKind) -> GptSnapshot:
        storage_key = (key, kind)
        snapshot = self._snapshots.get(storage_key)
        if snapshot is None:
            raise GptSnapshotMissingError(kind=kind)
        age = self._monotonic() - snapshot.saved_at
        if age >= GPT_SNAPSHOT_TTL_SECONDS:
            del self._snapshots[storage_key]
            raise GptSnapshotExpiredError(kind=kind, age_seconds=age)
        return snapshot

    def select(
        self,
        key: GptSnapshotKey,
        kind: GptSnapshotKind,
        raw_indices: str | None,
    ) -> tuple[str, ...]:
        indices = parse_csv_indices(raw_indices)
        snapshot = self.get(key, kind)
        size = len(snapshot.codex_thread_ids)
        for index in indices:
            if index > size:
                raise GptSnapshotSelectionError(item=str(index), reason="out-of-range")
        return tuple(snapshot.codex_thread_ids[index - 1] for index in indices)
