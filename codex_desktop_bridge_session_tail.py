from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from codex_bridge_state import JsonObject, JsonValue


_decode_json_value: Callable[[str], JsonValue] = json.loads


class SessionEventStream(Protocol):
    def seek(self, offset: int, whence: int = 0, /) -> int: ...

    def tell(self) -> int: ...

    def readline(self, size: int = -1, /) -> bytes: ...


class SessionEventDecodeError(UnicodeError):
    byte_offset: int
    error: UnicodeDecodeError
    complete_line: bool

    def __init__(self, byte_offset: int, error: UnicodeDecodeError, *, complete_line: bool) -> None:
        super().__init__(str(error))
        self.byte_offset = byte_offset
        self.error = error
        self.complete_line = complete_line


def _read_session_events(
    handle: SessionEventStream,
    start_offset: int,
    *,
    max_events: int | None = None,
) -> tuple[list[JsonObject], int]:
    events: list[JsonObject] = []
    event_limit = max(0, int(max_events or 0))
    _ = handle.seek(start_offset)
    while True:
        pos = handle.tell()
        raw = handle.readline()
        if not raw:
            return events, pos

        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SessionEventDecodeError(pos, exc, complete_line=raw.endswith(b"\n")) from None
        if not line:
            continue

        try:
            payload = _decode_json_value(line)
        except json.JSONDecodeError:
            _ = handle.seek(pos)
            return events, pos
        if isinstance(payload, dict):
            events.append(payload)
        if event_limit and len(events) >= event_limit:
            return events, handle.tell()


def read_session_snapshot_events(
    snapshot: SessionEventStream,
    start_offset: int,
    *,
    max_events: int | None = None,
) -> tuple[list[JsonObject], int]:
    """Read one bounded event batch from an immutable rollout snapshot."""
    return _read_session_events(snapshot, start_offset, max_events=max_events)


def read_new_session_events(
    session_path: Path,
    start_offset: int,
    *,
    max_events: int | None = None,
) -> tuple[list[JsonObject], int]:
    if not session_path.exists():
        return [], start_offset
    with session_path.open("rb") as handle:
        try:
            return _read_session_events(handle, start_offset, max_events=max_events)
        except SessionEventDecodeError as exc:
            raise exc.error from None
