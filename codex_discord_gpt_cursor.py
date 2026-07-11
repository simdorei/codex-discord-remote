"""Complete-record cursor boundaries for GPT chat reactivation."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import BinaryIO, Final, NamedTuple, Protocol, override

import codex_desktop_bridge_session_tail as session_tail
import codex_discord_store as discord_store
from codex_bridge_state import JsonObject
from codex_discord_gpt_ownership import CodexThreadId

if sys.platform == "win32":
    import _winapi
    import msvcrt

    def _open_platform_source(rollout_path: Path) -> BinaryIO:
        handle = _winapi.CreateFile(
            str(rollout_path),
            0x80000000,
            0x00000007,
            0,
            3,
            0x00000080,
            0,
        )
        try:
            file_descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        except OSError:
            _winapi.CloseHandle(handle)
            raise
        try:
            return os.fdopen(file_descriptor, "rb", buffering=0, closefd=True)
        except OSError:
            os.close(file_descriptor)
            raise

else:

    def _open_platform_source(rollout_path: Path) -> BinaryIO:
        return rollout_path.open("rb", buffering=0)


_BATCH_SIZE: Final = 100
_COPY_CHUNK_BYTES: Final = 64 * 1024
_SPOOL_MEMORY_BYTES: Final = 1024 * 1024
SnapshotEventReader = Callable[[session_tail.SessionEventStream, int, int], tuple[list[JsonObject], int]]
SessionMirrorCursorUpdater = Callable[[Path, str, str, int], None]
CopyChunkHook = Callable[[Path, int], None]


class SnapshotStream(session_tail.SessionEventStream, Protocol):
    def write(self, data: bytes, /) -> int: ...

    def flush(self) -> None: ...


@unique
class GptCursorSourceFailure(StrEnum):
    MISSING = "missing"
    UNREADABLE = "unreadable"
    INVALID = "invalid"
    CHANGED = "changed during the cursor scan"


class GptCursorError(RuntimeError):
    """Base error for a public-safe reactivation cursor failure."""


@dataclass(frozen=True, slots=True)
class GptCursorSourceError(GptCursorError):
    failure: GptCursorSourceFailure

    @override
    def __str__(self) -> str:
        return f"The GPT session source is {self.failure.value}."


class GptCursorPersistenceError(GptCursorError):
    @override
    def __str__(self) -> str:
        return "The GPT reactivation cursor could not be saved."


class GptCursorBatchSizeError(GptCursorError):
    @override
    def __str__(self) -> str:
        return "The GPT cursor scan batch size is invalid."


class GptCursorChunkSizeError(GptCursorError):
    @override
    def __str__(self) -> str:
        return "The GPT cursor copy chunk size is invalid."


@dataclass(frozen=True, slots=True)
class GptCursorRequest:
    db_path: Path
    codex_thread_id: CodexThreadId
    rollout_path: Path


class GptCursorBoundary(NamedTuple):
    rollout_path: str
    byte_offset: int


def _noop_copy_chunk(_rollout_path: Path, _copied_bytes: int) -> None:
    return None


@dataclass(frozen=True, slots=True)
class GptCursorDeps:
    read_snapshot_events: SnapshotEventReader
    update_session_mirror_cursor: SessionMirrorCursorUpdater
    batch_size: int = _BATCH_SIZE
    copy_chunk_bytes: int = _COPY_CHUNK_BYTES
    after_copy_chunk: CopyChunkHook = _noop_copy_chunk


@dataclass(frozen=True, slots=True)
class _RolloutCapture:
    rollout_path: Path
    source: BinaryIO
    snapshot: SnapshotStream
    initial_stat: os.stat_result


def _read_snapshot_events(
    snapshot: session_tail.SessionEventStream,
    offset: int,
    limit: int,
) -> tuple[list[JsonObject], int]:
    return session_tail.read_session_snapshot_events(snapshot, offset, max_events=limit)


_DEFAULT_DEPS: Final = GptCursorDeps(
    read_snapshot_events=_read_snapshot_events,
    update_session_mirror_cursor=discord_store.update_session_mirror_cursor,
)


def _open_rollout(rollout_path: Path) -> BinaryIO:
    try:
        return _open_platform_source(rollout_path)
    except FileNotFoundError:
        raise GptCursorSourceError(GptCursorSourceFailure.MISSING) from None
    except OSError:
        raise GptCursorSourceError(GptCursorSourceFailure.UNREADABLE) from None


def _initial_stat(source: BinaryIO) -> os.stat_result:
    try:
        return os.fstat(source.fileno())
    except OSError:
        raise GptCursorSourceError(GptCursorSourceFailure.UNREADABLE) from None


def _copy_initial_extent(capture: _RolloutCapture, deps: GptCursorDeps) -> None:
    if deps.copy_chunk_bytes < 1:
        raise GptCursorChunkSizeError()
    remaining = capture.initial_stat.st_size
    copied_bytes = 0
    while remaining:
        try:
            chunk = capture.source.read(min(remaining, deps.copy_chunk_bytes))
        except OSError:
            raise GptCursorSourceError(GptCursorSourceFailure.UNREADABLE) from None
        if not chunk:
            raise GptCursorSourceError(GptCursorSourceFailure.CHANGED)
        written = capture.snapshot.write(chunk)
        if written != len(chunk):
            raise GptCursorSourceError(GptCursorSourceFailure.UNREADABLE)
        copied_bytes += written
        remaining -= written
        deps.after_copy_chunk(capture.rollout_path, copied_bytes)
    capture.snapshot.flush()


def _verify_capture_identity(capture: _RolloutCapture) -> None:
    try:
        current_stat = capture.rollout_path.stat()
    except OSError:
        raise GptCursorSourceError(GptCursorSourceFailure.CHANGED) from None
    if not os.path.samestat(capture.initial_stat, current_stat):
        raise GptCursorSourceError(GptCursorSourceFailure.CHANGED)
    if current_stat.st_size < capture.initial_stat.st_size:
        raise GptCursorSourceError(GptCursorSourceFailure.CHANGED)


def _finish_boundary(snapshot: session_tail.SessionEventStream, offset: int) -> int:
    _ = snapshot.seek(offset)
    tail = snapshot.readline()
    if not tail or not tail.endswith(b"\n"):
        return offset
    raise GptCursorSourceError(GptCursorSourceFailure.INVALID)


def _scan_complete_boundary(snapshot: session_tail.SessionEventStream, deps: GptCursorDeps) -> int:
    if deps.batch_size < 1:
        raise GptCursorBatchSizeError()
    offset = 0
    while True:
        try:
            events, next_offset = deps.read_snapshot_events(snapshot, offset, deps.batch_size)
        except session_tail.SessionEventDecodeError as exc:
            if exc.error.reason == "unexpected end of data" and not exc.complete_line:
                return exc.byte_offset
            raise GptCursorSourceError(GptCursorSourceFailure.INVALID) from None
        if next_offset < offset:
            raise GptCursorSourceError(GptCursorSourceFailure.INVALID)
        if next_offset == offset:
            return _finish_boundary(snapshot, offset)
        offset = next_offset
        if len(events) < deps.batch_size:
            return _finish_boundary(snapshot, offset)


def establish_reactivation_cursor(
    request: GptCursorRequest,
    *,
    deps: GptCursorDeps = _DEFAULT_DEPS,
) -> GptCursorBoundary:
    """Persist the rollout's immutable last-complete-record boundary."""
    with _open_rollout(request.rollout_path) as source, tempfile.SpooledTemporaryFile(
        max_size=_SPOOL_MEMORY_BYTES,
        mode="w+b",
    ) as snapshot:
        capture = _RolloutCapture(request.rollout_path, source, snapshot, _initial_stat(source))
        _copy_initial_extent(capture, deps)
        _verify_capture_identity(capture)
        boundary = GptCursorBoundary(
            rollout_path=str(request.rollout_path),
            byte_offset=_scan_complete_boundary(snapshot, deps),
        )
        _verify_capture_identity(capture)
        cursor_saved = True
        try:
            deps.update_session_mirror_cursor(
                request.db_path,
                request.codex_thread_id,
                boundary.rollout_path,
                boundary.byte_offset,
            )
        except sqlite3.Error:
            cursor_saved = False
        if not cursor_saved:
            raise GptCursorPersistenceError()
    return boundary
