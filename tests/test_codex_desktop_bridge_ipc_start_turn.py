# pyright: reportUnusedCallResult=false
from __future__ import annotations

import unittest

import codex_desktop_bridge_ipc_start_turn as ipc_start_turn
from codex_thread_models import ThreadInfo


class IpcStartTurnTests(unittest.TestCase):
    def test_request_start_turn_builds_thread_follower_payload(self) -> None:
        written_payloads: list[ipc_start_turn.JsonObject] = []

        def write_message(_handle: int, payload: ipc_start_turn.JsonObject) -> None:
            written_payloads.append(payload)

        def read_response(
            _handle: int,
            _request_id: str,
            _timeout_sec: float,
            _owner_clients: dict[str, str],
        ) -> ipc_start_turn.JsonObject:
            return {
                "resultType": "success",
                "handledByClientId": "handled-client",
                "result": {"result": {"turn": {"id": "turn-1"}}},
            }

        thread = ThreadInfo(
            id="thread-1",
            title="Thread",
            cwd="C:\\repo",
            updated_at=1,
            rollout_path="session.jsonl",
            model="gpt",
            reasoning_effort="high",
            tokens_used=0,
        )
        deps = ipc_start_turn.IpcStartTurnDeps(
            write_ipc_message=write_message,
            read_ipc_response=read_response,
        )

        result = ipc_start_turn.request_start_turn_via_ipc(
            handle=1,
            source_client_id="source-client",
            thread=thread,
            prompt="prompt",
            timeout_sec=1.0,
            owner_clients={"thread-1": "owner-client"},
            deps=deps,
        )

        self.assertEqual(result, {"owner_client_id": "handled-client", "turn_id": "turn-1"})
        request = written_payloads[0]
        self.assertEqual(request["method"], "thread-follower-start-turn")
        self.assertEqual(request["targetClientId"], "owner-client")
        params = _require_json_object(request.get("params"))
        self.assertEqual(params["conversationId"], "thread-1")
        turn_start_params = _require_json_object(params.get("turnStartParams"))
        self.assertTrue(turn_start_params["inheritThreadSettings"])
        self.assertIsNone(turn_start_params["summary"])
        self.assertIsNone(turn_start_params["serviceTier"])
        self.assertIsNone(turn_start_params["effort"])

    def test_request_start_turn_surfaces_transport_failures(self) -> None:
        thread = ThreadInfo(
            id="thread-1",
            title="Thread",
            cwd="C:\\repo",
            updated_at=1,
            rollout_path="session.jsonl",
            model="gpt",
            reasoning_effort="high",
            tokens_used=0,
        )

        with self.assertRaises(ipc_start_turn.IPCNoClientFoundError) as no_client:
            _ = ipc_start_turn.request_start_turn_via_ipc(
                handle=1,
                source_client_id="source-client",
                thread=thread,
                prompt="prompt",
                timeout_sec=1.0,
                owner_clients={},
                deps=_deps_for_response({"resultType": "error", "error": "no-client-found"}),
            )
        self.assertIn("no-client-found", str(no_client.exception))

        with self.assertRaises(ipc_start_turn.IPCStartTurnResponseError) as failed:
            _ = ipc_start_turn.request_start_turn_via_ipc(
                handle=1,
                source_client_id="source-client",
                thread=thread,
                prompt="prompt",
                timeout_sec=1.0,
                owner_clients={},
                deps=_deps_for_response({"resultType": "error", "error": "boom"}),
            )
        self.assertIn("IPC start-turn failed: boom", str(failed.exception))

        with self.assertRaises(ipc_start_turn.IPCStartTurnInvalidPayloadError) as invalid:
            _ = ipc_start_turn.request_start_turn_via_ipc(
                handle=1,
                source_client_id="source-client",
                thread=thread,
                prompt="prompt",
                timeout_sec=1.0,
                owner_clients={},
                deps=_deps_for_response({"resultType": "success", "result": "bad"}),
            )
        self.assertIn("invalid payload", str(invalid.exception))

        fallback = ipc_start_turn.request_start_turn_via_ipc(
            handle=1,
            source_client_id="source-client",
            thread=thread,
            prompt="prompt",
            timeout_sec=1.0,
            owner_clients={"thread-1": "owner-client"},
            deps=_deps_for_response({"resultType": "success", "result": {}}),
        )
        self.assertEqual(fallback, {"owner_client_id": "owner-client", "turn_id": ""})


def _deps_for_response(response: ipc_start_turn.JsonObject) -> ipc_start_turn.IpcStartTurnDeps:
    def write_message(_handle: int, _payload: ipc_start_turn.JsonObject) -> None:
        return None

    def read_response(
        _handle: int,
        _request_id: str,
        _timeout_sec: float,
        _owner_clients: dict[str, str],
    ) -> ipc_start_turn.JsonObject:
        return response

    return ipc_start_turn.IpcStartTurnDeps(
        write_ipc_message=write_message,
        read_ipc_response=read_response,
    )


def _require_json_object(value: ipc_start_turn.JsonValue | None) -> ipc_start_turn.JsonObject:
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object, got {value!r}")
    return value


if __name__ == "__main__":
    unittest.main()
