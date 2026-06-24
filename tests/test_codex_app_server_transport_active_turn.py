from __future__ import annotations

import unittest
from unittest import mock

import codex_app_server_transport as transport
from codex_app_server_transport_replies import CodexAppServerTransportError, JsonObject


class UnexpectedActiveTurnReadError(Exception):
    pass


class ActiveTurnReadTimeout(TimeoutError):
    pass


class PersistentCodexAppServerActiveTurnTests(unittest.TestCase):
    def test_get_active_turn_id_logs_transport_failure_and_returns_none(self) -> None:
        logs: list[str] = []
        client = transport.PersistentCodexAppServer(log_func=logs.append)

        def read_thread(thread_id: str, *, include_turns: bool = False) -> JsonObject:
            self.assertEqual(thread_id, "thread-1")
            self.assertTrue(include_turns)
            raise CodexAppServerTransportError("transport down")

        with mock.patch.object(client, "read_thread", read_thread):
            self.assertIsNone(client.get_active_turn_id("thread-1"))

        self.assertEqual(len(logs), 1)
        self.assertIn("app_server_active_turn_read_failed thread=thread-1", logs[0])
        self.assertIn("error_type=CodexAppServerTransportError", logs[0])
        self.assertIn("transport down", logs[0])

    def test_get_active_turn_id_logs_timeout_failure_and_returns_none(self) -> None:
        logs: list[str] = []
        client = transport.PersistentCodexAppServer(log_func=logs.append)

        def read_thread(thread_id: str, *, include_turns: bool = False) -> JsonObject:
            _ = (thread_id, include_turns)
            raise ActiveTurnReadTimeout("read timed out")

        with mock.patch.object(client, "read_thread", read_thread):
            self.assertIsNone(client.get_active_turn_id("thread-1"))

        self.assertEqual(len(logs), 1)
        self.assertIn("error_type=ActiveTurnReadTimeout", logs[0])

    def test_get_active_turn_id_surfaces_unexpected_read_failure(self) -> None:
        logs: list[str] = []
        client = transport.PersistentCodexAppServer(log_func=logs.append)

        def read_thread(thread_id: str, *, include_turns: bool = False) -> JsonObject:
            _ = (thread_id, include_turns)
            raise UnexpectedActiveTurnReadError("boom")

        with mock.patch.object(client, "read_thread", read_thread):
            with self.assertRaisesRegex(UnexpectedActiveTurnReadError, "boom"):
                _ = client.get_active_turn_id("thread-1")

        self.assertEqual(logs, [])


if __name__ == "__main__":
    _ = unittest.main()
