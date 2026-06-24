from __future__ import annotations

import unittest
from typing import override

from codex_app_server_transport_pending import PendingRequestState
from codex_app_server_transport_replies import JsonObject


class PendingRequestStateTests(unittest.TestCase):
    @override
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.logs: list[str] = []

    @override
    def setUp(self) -> None:
        self.logs.clear()

    def test_pending_requests_keep_order_and_filter_by_thread(self) -> None:
        state = PendingRequestState()
        first: JsonObject = {
            "id": "approval-1",
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-1"},
        }
        second: JsonObject = {
            "id": "input-1",
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "thread-2"},
        }

        state.record_server_request("approval-1", first, self.logs.append)
        state.record_server_request("input-1", second, self.logs.append)

        self.assertEqual([request["id"] for request in state.pending_requests()], ["approval-1", "input-1"])
        self.assertEqual([request["id"] for request in state.pending_requests("thread-2")], ["input-1"])

    def test_latest_requests_ignore_other_threads_and_methods(self) -> None:
        state = PendingRequestState()
        approval: JsonObject = {
            "id": "approval-1",
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "thread-1"},
        }
        input_request: JsonObject = {
            "id": "input-1",
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "thread-1"},
        }

        state.record_server_request("approval-1", approval, self.logs.append)
        state.record_server_request("input-1", input_request, self.logs.append)

        self.assertIs(state.latest_approval_request("thread-1"), approval)
        self.assertIs(state.latest_input_request("thread-1"), input_request)
        self.assertIsNone(state.latest_approval_request("thread-2"))

    def test_notifications_track_active_turn_until_matching_completion(self) -> None:
        state = PendingRequestState()

        state.record_notification(
            {"method": "turn/started", "params": {"threadId": "thread-1", "turnId": "turn-1"}},
            self.logs.append,
        )
        state.record_notification(
            {"method": "turn/completed", "params": {"threadId": "thread-1", "turnId": "turn-other"}},
            self.logs.append,
        )

        self.assertEqual(state.active_turn_id("thread-1"), "turn-1")

        state.record_notification(
            {"method": "turn/completed", "params": {"threadId": "thread-1", "turnId": "turn-1"}},
            self.logs.append,
        )

        self.assertIsNone(state.active_turn_id("thread-1"))

    def test_resolve_request_removes_it_from_pending_requests(self) -> None:
        state = PendingRequestState()
        request: JsonObject = {
            "id": "input-1",
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "thread-1"},
        }

        state.record_server_request("input-1", request, self.logs.append)
        state.resolve_request("input-1")

        self.assertEqual(state.pending_requests("thread-1"), [])


if __name__ == "__main__":
    _ = unittest.main()
