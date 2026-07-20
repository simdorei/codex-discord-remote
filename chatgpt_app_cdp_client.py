from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

import aiohttp

from chatgpt_app_cdp import (
    RENDERER_SNAPSHOT_FUNCTION,
    CdpContractError,
    JsonValue,
    parse_renderer_snapshot,
    select_chatgpt_cdp_target,
)
from chatgpt_app_mirror_models import ChatGptSnapshot


_COMMAND_ID = 1
_MAX_WS_MESSAGE_BYTES = 4 * 1024 * 1024


class _JsonDecoder(Protocol):
    def __call__(self, s: str, /) -> JsonValue: ...


class _TextWebSocketMessage(Protocol):
    @property
    def data(self) -> str: ...


_JSON_DECODER: _JsonDecoder = json.loads


async def read_chatgpt_app_snapshot(cdp_http_url: str) -> ChatGptSnapshot:
    timeout = aiohttp.ClientTimeout(
        total=8.0,
        connect=3.0,
        sock_connect=3.0,
        sock_read=5.0,
    )
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        targets = await _read_targets(session, cdp_http_url)
        target = select_chatgpt_cdp_target(targets)
        renderer_value = await _call_renderer_snapshot(session, target.websocket_url)
    return parse_renderer_snapshot(renderer_value)


async def _read_targets(
    session: aiohttp.ClientSession,
    cdp_http_url: str,
) -> JsonValue:
    async with session.get(f"{cdp_http_url}/json/list") as response:
        response.raise_for_status()
        return _decode_json(await response.text())


async def _call_renderer_snapshot(
    session: aiohttp.ClientSession,
    websocket_url: str,
) -> JsonValue:
    async with session.ws_connect(
        websocket_url,
        max_msg_size=_MAX_WS_MESSAGE_BYTES,
        autoclose=True,
        autoping=True,
    ) as socket:
        await socket.send_json(
            {
                "id": _COMMAND_ID,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": f"({RENDERER_SNAPSHOT_FUNCTION})()",
                    "returnByValue": True,
                    "awaitPromise": False,
                    "userGesture": False,
                },
            }
        )
        while True:
            message = await socket.receive()
            if message.type is aiohttp.WSMsgType.TEXT:
                payload = _decode_text_message(message)
                value = _extract_command_value(payload)
                if isinstance(value, _NotOurCommand):
                    continue
                return value
            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                raise CdpContractError("CDP websocket closed before the renderer replied")


@dataclass(frozen=True, slots=True)
class _NotOurCommand:
    pass


_NOT_OUR_COMMAND = _NotOurCommand()
type CommandValue = JsonValue | _NotOurCommand


def _extract_command_value(payload: JsonValue) -> CommandValue:
    if not isinstance(payload, dict) or payload.get("id") != _COMMAND_ID:
        return _NOT_OUR_COMMAND
    if "error" in payload:
        raise CdpContractError("Runtime.evaluate returned a CDP error")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CdpContractError("Runtime.callFunctionOn result is missing")
    remote_object = result.get("result")
    if not isinstance(remote_object, dict) or "value" not in remote_object:
        raise CdpContractError("Runtime.evaluate did not return a value")
    return remote_object["value"]


def _decode_json(text: str) -> JsonValue:
    try:
        value = _JSON_DECODER(text)
    except json.JSONDecodeError as exc:
        raise CdpContractError("CDP returned invalid JSON") from exc
    return value


def _decode_text_message(message: _TextWebSocketMessage) -> JsonValue:
    return _decode_json(message.data)
