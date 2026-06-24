from __future__ import annotations

from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def build_start_turn_request(
    *,
    request_id: str,
    source_client_id: str,
    thread_id: str,
    prompt: str,
    owner_client_id: str | None,
) -> JsonObject:
    request: JsonObject = {
        "type": "request",
        "requestId": request_id,
        "sourceClientId": source_client_id,
        "version": 1,
        "method": "thread-follower-start-turn",
        "params": {
            "conversationId": thread_id,
            "turnStartParams": {
                "inheritThreadSettings": True,
                "input": [{"type": "text", "text": prompt, "text_elements": []}],
                "cwd": None,
                "approvalPolicy": None,
                "sandboxPolicy": None,
                "approvalsReviewer": "user",
                "model": None,
                "serviceTier": None,
                "effort": None,
                "summary": None,
                "personality": None,
                "outputSchema": None,
                "collaborationMode": None,
                "attachments": [],
            },
        },
    }
    if owner_client_id:
        request["targetClientId"] = owner_client_id
    return request
