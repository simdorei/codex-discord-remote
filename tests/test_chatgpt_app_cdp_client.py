from __future__ import annotations

import json
from typing import Protocol
import unittest

from aiohttp import WSMsgType, web

from chatgpt_app_cdp_client import read_chatgpt_app_snapshot
from chatgpt_app_cdp import JsonValue


class JsonDecoder(Protocol):
    def __call__(self, s: str, /) -> JsonValue: ...


class TextWebSocketMessage(Protocol):
    @property
    def data(self) -> str: ...


JSON_DECODER: JsonDecoder = json.loads


def _decode_message(message: TextWebSocketMessage) -> dict[str, JsonValue]:
    payload = JSON_DECODER(message.data)
    if not isinstance(payload, dict):
        raise AssertionError("expected CDP command object")
    return payload


async def _start_test_site(runner: web.AppRunner) -> tuple[web.TCPSite, int]:
    for port in range(49152, 49252):
        site = web.TCPSite(runner, "127.0.0.1", port)
        try:
            await site.start()
        except OSError:
            continue
        return site, port
    raise AssertionError("no local test port was available")


class ChatGptAppCdpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_snapshot_through_runtime_evaluate(self) -> None:
        methods: list[str] = []
        expressions: list[str] = []

        async def targets(request: web.Request) -> web.Response:
            port = request.url.port
            return web.json_response(
                [
                    {
                        "type": "page",
                        "title": "Codex",
                        "url": "file:///app/index.html",
                        "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/main",
                    }
                ]
            )

        async def websocket(request: web.Request) -> web.WebSocketResponse:
            socket = web.WebSocketResponse()
            _ = await socket.prepare(request)
            message = await socket.receive()
            self.assertEqual(message.type, WSMsgType.TEXT)
            payload = _decode_message(message)
            methods.append(str(payload["method"]))
            params = payload.get("params")
            if not isinstance(params, dict):
                raise AssertionError("expected CDP command params")
            expressions.append(str(params.get("expression", "")))
            await socket.send_json(
                {
                    "id": payload["id"],
                    "result": {
                        "result": {
                            "type": "object",
                            "value": {
                                "recentConversations": [
                                    {"id": f"c{index}", "title": f"title {index}"}
                                    for index in range(1, 6)
                                ],
                                "activeConversationId": "c1",
                                "turns": [{"id": "u1", "role": "user", "text": "hello"}],
                                "isStreaming": False,
                            },
                        }
                    },
                }
            )
            _ = await socket.close()
            return socket

        app = web.Application()
        _ = app.router.add_get("/json/list", targets)
        _ = app.router.add_get("/devtools/page/main", websocket)
        runner = web.AppRunner(app)
        await runner.setup()
        _site, port = await _start_test_site(runner)
        try:
            snapshot = await read_chatgpt_app_snapshot(f"http://127.0.0.1:{port}")
        finally:
            await runner.cleanup()

        self.assertEqual(methods, ["Runtime.evaluate"])
        self.assertIn("data-chatgpt-conversation-turn", expressions[0])
        self.assertEqual(snapshot.active_conversation_id, "c1")
        self.assertEqual([turn.text for turn in snapshot.turns], ["hello"])


if __name__ == "__main__":
    _ = unittest.main()
