# pyright: reportAny=false, reportAttributeAccessIssue=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import unittest
from pathlib import Path

import codex_desktop_bridge as bridge
import codex_desktop_bridge_final_answer as final_answer
import codex_desktop_bridge_final_answer_types as final_answer_types
from codex_session_events import JsonEvent, JsonValue


class FinalAnswerWatchHappyTests(unittest.TestCase):
    def test_watch_returns_final_with_commentary_stream_and_bridge_wrappers(self) -> None:
        events = [
            _agent_message("checking"),
            _agent_message("checking"),
            _assistant_message("commentary", "thinking"),
            _assistant_message("final_answer", "done"),
        ]
        callback_lines: list[str] = []
        fake_deps = _deps([events])

        result = final_answer.watch_for_final_answer(
            Path("session.jsonl"),
            start_offset=0,
            timeout_sec=5.0,
            include_commentary=True,
            stream_live=True,
            stream_label="thread-1",
            stream_callback=callback_lines.append,
            deps=fake_deps.as_deps(),
        )

        self.assertEqual(result["status"], "final")
        self.assertEqual(result["commentary"], ["checking", "thinking"])
        self.assertEqual(result["final_answer"], "done")
        self.assertTrue(result["streamed_live"])
        self.assertTrue(result["final_streamed_live"])
        self.assertIn("thread-1 [commentary]", callback_lines)
        self.assertIn("thread-1 [final_answer]", callback_lines)
        self.assertEqual(fake_deps.cursors, [0])

        original_read = bridge.read_new_session_events
        original_notice = bridge.build_interactive_notice_from_function_call
        original_extract = bridge.extract_message_text
        try:
            bridge.read_new_session_events = lambda session_path, cursor: ([events[-1]], 1)
            bridge.build_interactive_notice_from_function_call = lambda payload: "[approval_required]"
            bridge.extract_message_text = lambda payload: "wrapped"
            wrapped = bridge.watch_for_final_answer(Path("session.jsonl"), 0, 1.0, True)
        finally:
            bridge.read_new_session_events = original_read
            bridge.build_interactive_notice_from_function_call = original_notice
            bridge.extract_message_text = original_extract
        self.assertEqual(wrapped["status"], "final")
        self.assertEqual(wrapped["final_answer"], "wrapped")


class FinalAnswerWatchEdgeTests(unittest.TestCase):
    def test_watch_handles_edge_payloads_abort_and_timeout(self) -> None:
        callback_lines: list[str] = []
        fake_deps = _deps(
            [
                [
                    {"type": "event_msg", "payload": []},
                    _agent_message("checking"),
                    _function_call("call-1"),
                    _function_output("Rejected by user: no"),
                    _assistant_message("commentary", ""),
                    _assistant_message("commentary", "checking"),
                    _event_msg("turn_aborted"),
                ],
            ],
        )

        aborted = final_answer.watch_for_final_answer(
            Path("session.jsonl"),
            start_offset=0,
            timeout_sec=5.0,
            include_commentary=True,
            stream_live=True,
            stream_label="thread-2",
            stream_callback=callback_lines.append,
            deps=fake_deps.as_deps(),
        )

        self.assertEqual(aborted["status"], "aborted")
        self.assertEqual(
            aborted["commentary"],
            ["checking", "[approval_required]\ncall-1", "[approval_rejected]\nCommand approval was rejected by user.", "checking"],
        )
        self.assertTrue(aborted["streamed_live"])
        self.assertFalse(aborted["final_streamed_live"])
        self.assertIn("thread-2 [commentary]", callback_lines)

        timeout = final_answer.watch_for_final_answer(
            Path("session.jsonl"),
            start_offset=5,
            timeout_sec=1.0,
            include_commentary=True,
            deps=_deps([[]], times=[0.0, 2.0]).as_deps(),
        )

        self.assertEqual(timeout["status"], "timeout")
        self.assertEqual(timeout["commentary"], [])
        self.assertEqual(timeout["final_answer"], "")


class FinalAnswerWatchPublicSurfaceTests(unittest.TestCase):
    def test_emit_watch_stream_block_sends_marker_text_and_trailing_blank(self) -> None:
        lines: list[str] = []

        final_answer.emit_watch_stream_block("[commentary]", "first\nsecond", stream_callback=lines.append)

        self.assertEqual(lines, ["[commentary]", "first", "second", ""])

    def test_watch_state_accumulates_mutable_result_fields(self) -> None:
        state = final_answer_types.WatchState()
        state.commentary.append("checking")
        state.seen_agent_messages.add("checking")
        state.seen_interactive_notices.add("call-1")
        state.final_answer = "done"
        state.did_stream_live = True
        state.did_stream_final_live = True

        result = final_answer_types.result_from_state("final", state)

        self.assertEqual(result["status"], "final")
        self.assertEqual(result["commentary"], ["checking"])
        self.assertEqual(result["final_answer"], "done")
        self.assertTrue(result["streamed_live"])
        self.assertTrue(result["final_streamed_live"])
        self.assertEqual(state.seen_agent_messages, {"checking"})
        self.assertEqual(state.seen_interactive_notices, {"call-1"})


class _FakeDeps:
    def __init__(
        self,
        event_batches: list[list[JsonEvent]],
        *,
        times: list[float] | None = None,
    ) -> None:
        self.event_batches: list[list[JsonEvent]] = list(event_batches)
        self.times: list[float] = list(times or [0.0, 0.0, 0.0])
        self.cursors: list[int] = []

    def as_deps(self) -> final_answer.FinalAnswerWatchDeps:
        return final_answer.FinalAnswerWatchDeps(
            time_now=self._time_now,
            sleep=lambda seconds: None,
            read_new_session_events=self._read,
            build_interactive_notice_from_function_call=self._notice,
            extract_message_text=_extract_message_text,
            emit_watch_stream_block=final_answer.emit_watch_stream_block,
        )

    def _time_now(self) -> float:
        if len(self.times) == 1:
            return self.times[0]
        return self.times.pop(0)

    def _read(self, session_path: Path, cursor: int) -> tuple[list[JsonEvent], int]:
        _ = session_path
        self.cursors.append(cursor)
        if not self.event_batches:
            return [], cursor
        return self.event_batches.pop(0), cursor + 1

    def _notice(self, payload: final_answer.JsonObject) -> str:
        return f"[approval_required]\n{payload.get('call_id') or '-'}"


def _deps(event_batches: list[list[JsonEvent]], *, times: list[float] | None = None) -> _FakeDeps:
    return _FakeDeps(event_batches, times=times)


def _extract_message_text(payload: final_answer.JsonObject) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "output_text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _event_msg(event_type: str) -> JsonEvent:
    return {"type": "event_msg", "payload": {"type": event_type}}


def _agent_message(message: str) -> JsonEvent:
    return {"type": "event_msg", "payload": {"type": "agent_message", "message": message}}


def _assistant_message(phase: str, text: str) -> JsonEvent:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _function_call(call_id: str) -> JsonEvent:
    return {"type": "response_item", "payload": {"type": "function_call", "call_id": call_id}}


def _function_output(output: str) -> JsonEvent:
    payload: dict[str, JsonValue] = {"type": "function_call_output", "output": output}
    return {"type": "response_item", "payload": payload}


if __name__ == "__main__":
    _ = unittest.main()
