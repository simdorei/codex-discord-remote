from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from codex_thread_models import ThreadInfo

InterruptThreadViaSidecar = Callable[[ThreadInfo], bool]
GetThreadLabel = Callable[[ThreadInfo], str]
PrintLine = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class StopCommandDeps:
    interrupt_thread_via_sidecar: InterruptThreadViaSidecar
    get_thread_label: GetThreadLabel
    print_line: PrintLine


def run_stop_command(thread: ThreadInfo, *, deps: StopCommandDeps) -> None:
    label = deps.get_thread_label(thread)
    deps.print_line(f"target_thread: {label}")
    stopped = deps.interrupt_thread_via_sidecar(thread)
    deps.print_line(f"reply_stop_requested: {str(bool(stopped)).lower()}")
