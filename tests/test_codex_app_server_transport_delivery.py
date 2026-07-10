from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory

import codex_app_server_transport_delivery as delivery
from codex_app_server_transport_replies import JsonObject
from codex_thread_models import ThreadInfo


class AppServerDeliveryResultTests(unittest.TestCase):
    def test_delivery_result_is_frozen_and_slotted(self) -> None:
        result = delivery.AppServerDeliveryResult(0, "ok")

        self.assertFalse(hasattr(result, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            setattr(result, "delivery_pending", True)


class AppServerDeliveryFlowTests(unittest.TestCase):
    def test_start_turn_verified_delivery_preserves_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            _ = session_path.write_text("abc", encoding="utf-8")
            thread = _thread(session_path)
            bridge = FakeBridge(thread, delivered_thread=thread)
            client = FakeDeliveryClient(start_result={"turn": {"id": "turn-1"}})

            result = delivery.start_turn_no_wait(
                client,
                "hello",
                None,
                bridge_module=bridge,
                confirm_timeout_sec=1.0,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.thread_id, "thread-1")
        self.assertEqual(result.turn_id, "turn-1")
        self.assertEqual(result.session_path, str(session_path))
        self.assertEqual(result.start_offset, 3)
        self.assertFalse(result.delivery_pending)
        self.assertEqual(client.started, [("thread-1", "hello")])
        self.assertEqual(bridge.waited_prompts, ["hello"])
        self.assertIn("[delivery_verified] label:thread-1", result.output)
        self.assertIn("transport: resident-app-server turn/start", result.output)

    def test_steer_cross_thread_mismatch_preserves_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            _ = session_path.write_text("x", encoding="utf-8")
            thread = _thread(session_path)
            other_thread = _thread(session_path, thread_id="other-thread")
            bridge = FakeBridge(thread, delivered_thread=other_thread)
            client = FakeDeliveryClient(active_turn_id="active-1", steer_result={})

            result = delivery.steer_or_start_no_wait(
                client,
                "steer",
                "thread-1",
                bridge_module=bridge,
                confirm_timeout_sec=1.0,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.thread_id, "thread-1")
        self.assertEqual(result.turn_id, "active-1")
        self.assertEqual(result.session_path, str(session_path))
        self.assertEqual(result.start_offset, 1)
        self.assertEqual(client.steered, [("thread-1", "steer", "active-1")])
        self.assertIn("Prompt landed in a different thread after app-server delivery.", result.output)
        self.assertIn("Expected label:thread-1, but it was recorded in label:other-thread.", result.output)

    def test_start_turn_id_is_authoritative_when_rollout_confirmation_is_delayed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            _ = session_path.write_text("", encoding="utf-8")
            thread = _thread(session_path)
            bridge = FakeBridge(thread, delivered_thread=None)
            client = FakeDeliveryClient(start_result={"turn": {"id": "turn-1"}})

            result = delivery.start_turn_no_wait(
                client,
                "hello",
                None,
                bridge_module=bridge,
                confirm_timeout_sec=25.0,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.turn_id, "turn-1")
        self.assertFalse(result.delivery_pending)
        self.assertEqual(bridge.waited_prompts, ["hello"])
        self.assertIn("[app_server_delivery] turn_id=turn-1", result.output)
        self.assertNotIn("[delivery_pending]", result.output)

    def test_active_turn_id_is_authoritative_when_rollout_confirmation_is_delayed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            _ = session_path.write_text("", encoding="utf-8")
            thread = _thread(session_path)
            bridge = FakeBridge(thread, delivered_thread=None)
            client = FakeDeliveryClient(active_turn_id="active-1")

            result = delivery.steer_or_start_no_wait(
                client,
                "hello",
                thread.id,
                bridge_module=bridge,
                confirm_timeout_sec=25.0,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.turn_id, "active-1")
        self.assertFalse(result.delivery_pending)
        self.assertEqual(bridge.waited_prompts, ["hello"])
        self.assertEqual(client.steered, [(thread.id, "hello", "active-1")])
        self.assertNotIn("[delivery_pending]", result.output)

    def test_zero_confirm_timeout_without_turn_id_returns_pending(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "session.jsonl"
            _ = session_path.write_text("", encoding="utf-8")
            thread = _thread(session_path)
            bridge = FakeBridge(thread, delivered_thread=thread)
            client = FakeDeliveryClient()

            result = delivery.start_turn_no_wait(
                client,
                "hello",
                None,
                bridge_module=bridge,
                confirm_timeout_sec=0.0,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.turn_id)
        self.assertTrue(result.delivery_pending)
        self.assertEqual(bridge.waited_prompts, [])
        self.assertIn("[delivery_pending]", result.output)


class FakeDeliveryClient:
    def __init__(
        self,
        *,
        active_turn_id: str | None = None,
        start_result: JsonObject | None = None,
        steer_result: JsonObject | None = None,
    ) -> None:
        self.active_turn_id: str | None = active_turn_id
        self.start_result: JsonObject = start_result or {}
        self.steer_result: JsonObject = steer_result or {}
        self.started: list[tuple[str, str]] = []
        self.steered: list[tuple[str, str, str]] = []

    def read_thread(self, thread_id: str, *, include_turns: bool = False) -> JsonObject:
        _ = include_turns
        return {"thread": {"id": thread_id, "status": {"type": "idle"}}}

    def resume_thread(self, thread_id: str) -> JsonObject:
        return {"thread": {"id": thread_id, "status": {"type": "idle"}}}

    def start_turn(self, thread_id: str, prompt: str) -> JsonObject:
        self.started.append((thread_id, prompt))
        return self.start_result

    def steer_turn(self, thread_id: str, prompt: str, *, expected_turn_id: str) -> JsonObject:
        self.steered.append((thread_id, prompt, expected_turn_id))
        return self.steer_result

    def get_active_turn_id(self, thread_id: str) -> str | None:
        _ = thread_id
        return self.active_turn_id


class FakeBridge:
    def __init__(self, thread: ThreadInfo, *, delivered_thread: ThreadInfo | None) -> None:
        self.thread: ThreadInfo = thread
        self.delivered_thread: ThreadInfo | None = delivered_thread
        self.waited_prompts: list[str] = []

    def choose_thread(self, thread_id: str | None, cwd: str | None) -> ThreadInfo:
        _ = (thread_id, cwd)
        return self.thread

    def get_thread_workspace_ref(self, thread: ThreadInfo, threads: list[ThreadInfo] | None = None) -> str:
        _ = threads
        return f"ref:{thread.id}"

    def snapshot_recent_session_offsets(
        self,
        limit: int = 10,
        include_threads: list[ThreadInfo] | None = None,
    ) -> dict[str, tuple[ThreadInfo, Path, int]]:
        _ = (limit, include_threads)
        return {self.thread.id: (self.thread, Path(self.thread.rollout_path), 0)}

    def wait_for_prompt_delivery(
        self,
        session_offsets: dict[str, tuple[ThreadInfo, Path, int]],
        prompt: str,
        timeout_sec: float = 4.0,
    ) -> ThreadInfo | None:
        _ = (session_offsets, timeout_sec)
        self.waited_prompts.append(prompt)
        return self.delivered_thread

    def get_thread_label(self, thread: ThreadInfo) -> str:
        return f"label:{thread.id}"

    def format_title_preview(self, value: str, limit: int = 120) -> str:
        return value[:limit]


def _thread(session_path: Path, *, thread_id: str = "thread-1") -> ThreadInfo:
    return ThreadInfo(
        id=thread_id,
        title=f"Thread {thread_id}",
        cwd=str(session_path.parent),
        updated_at=1,
        rollout_path=str(session_path),
        model="gpt",
        reasoning_effort="high",
        tokens_used=0,
    )


if __name__ == "__main__":
    _ = unittest.main()
