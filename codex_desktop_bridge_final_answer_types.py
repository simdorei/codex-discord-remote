from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, TypedDict

from codex_session_events import JsonEvent, JsonValue

JsonObject: TypeAlias = dict[str, JsonValue]
StreamCallback: TypeAlias = Callable[[str], None]
WatchStatus: TypeAlias = Literal["aborted", "final", "timeout"]


class EmitWatchStreamBlock(Protocol):
    def __call__(
        self,
        marker: str,
        text: str,
        *,
        stream_label: str = "",
        stream_callback: StreamCallback | None = None,
    ) -> None: ...


class WatchForFinalAnswerResult(TypedDict):
    status: WatchStatus
    commentary: list[str]
    final_answer: str
    streamed_live: bool
    final_streamed_live: bool


@dataclass(frozen=True, slots=True)
class FinalAnswerWatchDeps:
    time_now: Callable[[], float]
    sleep: Callable[[float], None]
    read_new_session_events: Callable[[Path, int], tuple[list[JsonEvent], int]]
    build_interactive_notice_from_function_call: Callable[[JsonObject], str]
    extract_message_text: Callable[[JsonObject], str]
    emit_watch_stream_block: EmitWatchStreamBlock


@dataclass(slots=True)  # noqa: MUTABLE_OK
class WatchState:
    """Mutable final-answer event accumulator; handlers update it incrementally."""

    commentary: list[str] = field(default_factory=list)
    final_answer: str = ""
    seen_agent_messages: set[str] = field(default_factory=set)
    seen_interactive_notices: set[str] = field(default_factory=set)
    did_stream_live: bool = False
    did_stream_final_live: bool = False


def append_commentary(
    text: str,
    *,
    include_commentary: bool,
    commentary: list[str],
    dedupe: set[str] | None,
    stream_live: bool,
    stream_label: str,
    stream_callback: StreamCallback | None,
    deps: FinalAnswerWatchDeps,
    did_stream_live: bool,
) -> bool:
    if not include_commentary or not text:
        return did_stream_live
    if dedupe is not None:
        if text in dedupe:
            return did_stream_live
        dedupe.add(text)
    elif commentary and commentary[-1] == text:
        return did_stream_live

    commentary.append(text)
    if not stream_live:
        return did_stream_live

    deps.emit_watch_stream_block(
        "[commentary]",
        text,
        stream_label=stream_label,
        stream_callback=stream_callback,
    )
    return True


def result(
    status: WatchStatus,
    commentary: list[str],
    final_answer: str,
    streamed_live: bool,
    final_streamed_live: bool,
) -> WatchForFinalAnswerResult:
    return WatchForFinalAnswerResult(
        status=status,
        commentary=commentary,
        final_answer=final_answer,
        streamed_live=streamed_live,
        final_streamed_live=final_streamed_live,
    )


def result_from_state(
    status: WatchStatus,
    state: WatchState,
) -> WatchForFinalAnswerResult:
    return result(
        status,
        state.commentary,
        state.final_answer,
        state.did_stream_live,
        state.did_stream_final_live,
    )
