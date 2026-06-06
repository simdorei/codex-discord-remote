"""Typed delivery result helpers for Discord ask flows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class AskDeliveryStatus(str, Enum):
    FINAL = "final"
    NO_FINAL = "no_final"
    ABORTED = "aborted"
    TIMEOUT = "timeout"
    BUSY_REJECTED = "busy_rejected"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class AskStreamResult:
    exit_code: int
    output: str
    sent_live: bool
    saw_final: bool
    saw_aborted: bool
    saw_timeout: bool
    suppressed_after_steering: bool


def ask_stream_result_from_relay(exit_code: int, output: str, relay: object) -> AskStreamResult:
    return AskStreamResult(
        exit_code=int(exit_code),
        output=str(output or ""),
        sent_live=bool(getattr(relay, "sent_live", False)),
        saw_final=bool(getattr(relay, "saw_final", False)),
        saw_aborted=bool(getattr(relay, "saw_aborted", False)),
        saw_timeout=bool(getattr(relay, "saw_timeout", False)),
        suppressed_after_steering=bool(getattr(relay, "suppressed_after_steering", False)),
    )


def classify_ask_stream_result(
    result: AskStreamResult,
    *,
    is_busy_error_func: Callable[[int, str], bool] | None = None,
) -> AskDeliveryStatus:
    if result.suppressed_after_steering:
        return AskDeliveryStatus.SUPPRESSED
    if result.saw_aborted:
        return AskDeliveryStatus.ABORTED
    if result.saw_timeout:
        return AskDeliveryStatus.TIMEOUT
    if is_busy_error_func is not None and is_busy_error_func(result.exit_code, result.output):
        return AskDeliveryStatus.BUSY_REJECTED
    if result.exit_code != 0:
        return AskDeliveryStatus.FAILED
    if result.saw_final:
        return AskDeliveryStatus.FINAL
    return AskDeliveryStatus.NO_FINAL


def format_target_thread_id(target_thread_id: str | None) -> str:
    return target_thread_id or "-"


def format_ask_stream_done_log(
    result: AskStreamResult,
    status: AskDeliveryStatus,
    *,
    target_thread_id: str | None,
    output_len: int,
) -> str:
    return (
        f"ask_stream_done exit={result.exit_code} target={format_target_thread_id(target_thread_id)} "
        f"status={status.value} "
        f"sent_live={result.sent_live} final={result.saw_final} aborted={result.saw_aborted} "
        f"timeout={result.saw_timeout} output_len={output_len}"
    )


def format_ask_stream_retry_done_log(
    result: AskStreamResult,
    status: AskDeliveryStatus,
    *,
    attempt: int,
    target_thread_id: str | None,
    output_len: int,
) -> str:
    return (
        f"ask_stream_retry_done attempt={attempt} exit={result.exit_code} "
        f"target={format_target_thread_id(target_thread_id)} status={status.value} "
        f"sent_live={result.sent_live} final={result.saw_final} aborted={result.saw_aborted} "
        f"timeout={result.saw_timeout} output_len={output_len}"
    )
