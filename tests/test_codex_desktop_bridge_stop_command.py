from __future__ import annotations

import unittest

import codex_desktop_bridge_stop_command as stop_command
from codex_thread_models import ThreadInfo


def _thread() -> ThreadInfo:
    return ThreadInfo(
        id="thread-1",
        title="Thread title",
        cwd="C:/repo",
        updated_at=1,
        rollout_path="C:/repo/session.jsonl",
        model="gpt-5.5",
        reasoning_effort="high",
        tokens_used=42,
    )


class DesktopBridgeStopCommandTests(unittest.TestCase):
    def test_run_stop_command_interrupts_target_thread(self) -> None:
        interrupted_ids: list[str] = []
        lines: list[str] = []

        def interrupt_thread_via_sidecar(thread: ThreadInfo) -> bool:
            interrupted_ids.append(thread.id)
            return True

        stop_command.run_stop_command(
            _thread(),
            deps=stop_command.StopCommandDeps(
                interrupt_thread_via_sidecar=interrupt_thread_via_sidecar,
                get_thread_label=lambda thread: f"{thread.title} ({thread.id})",
                print_line=lines.append,
            ),
        )

        self.assertEqual(interrupted_ids, ["thread-1"])
        self.assertEqual(
            lines,
            [
                "target_thread: Thread title (thread-1)",
                "reply_stop_requested: true",
            ],
        )

    def test_run_stop_command_surfaces_sidecar_failure(self) -> None:
        def interrupt_thread_via_sidecar(thread: ThreadInfo) -> bool:
            _ = thread
            raise RuntimeError("sidecar unavailable")

        with self.assertRaisesRegex(RuntimeError, "sidecar unavailable"):
            stop_command.run_stop_command(
                _thread(),
                deps=stop_command.StopCommandDeps(
                    interrupt_thread_via_sidecar=interrupt_thread_via_sidecar,
                    get_thread_label=lambda thread: thread.id,
                    print_line=lambda line: None,
                ),
            )


if __name__ == "__main__":
    _ = unittest.main()
