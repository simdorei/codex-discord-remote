"""Discord-facing helpers for the resident Codex app-server transport."""

from __future__ import annotations

from typing import Any

import codex_app_server_transport as app_server_transport
import codex_desktop_bridge as bridge


def run_prompt_no_wait(
    prompt: str,
    target_thread_id: str | None,
    *,
    transport_module: Any = app_server_transport,
    bridge_module: Any = bridge,
    client: object | None = None,
    confirm_timeout_sec: float = 6.0,
) -> tuple[int, str]:
    result = transport_module.start_turn_no_wait(
        client or transport_module.DEFAULT_CLIENT,
        prompt,
        target_thread_id,
        bridge_module=bridge_module,
        confirm_timeout_sec=confirm_timeout_sec,
    )
    return result.exit_code, result.output


def run_steering_no_wait(
    prompt: str,
    target_thread_id: str | None,
    *,
    transport_module: Any = app_server_transport,
    bridge_module: Any = bridge,
    client: object | None = None,
    confirm_timeout_sec: float,
) -> app_server_transport.AppServerDeliveryResult:
    return transport_module.steer_or_start_no_wait(
        client or transport_module.DEFAULT_CLIENT,
        prompt,
        target_thread_id,
        bridge_module=bridge_module,
        confirm_timeout_sec=confirm_timeout_sec,
    )


def submit_approval_reply(
    target_thread_id: str,
    answer: str,
    *,
    client: object | None = None,
) -> tuple[int, str] | None:
    active_client = client or app_server_transport.DEFAULT_CLIENT
    pending_request = active_client.get_latest_pending_approval_request(target_thread_id)
    if pending_request is None:
        return None
    try:
        result = active_client.reply_to_pending_approval(target_thread_id, answer)
    except Exception as exc:
        return 1, f"ERROR: resident app-server approval writeback failed: {exc}"
    lines = [
        f"thread_id: {target_thread_id}",
        f"decision_action: {result.get('decision_action') or '-'}",
        f"request_kind: {result.get('request_kind') or '-'}",
        f"request_id: {result.get('request_id') or '-'}",
        "transport: resident-app-server approval",
    ]
    return 0, "\n".join(lines)


def submit_input_reply(
    target_thread_id: str,
    answer: str,
    *,
    client: object | None = None,
) -> tuple[int, str] | None:
    active_client = client or app_server_transport.DEFAULT_CLIENT
    pending_input = active_client.get_latest_pending_input_request(target_thread_id)
    if pending_input is None:
        return None
    try:
        result = active_client.reply_to_pending_input(target_thread_id, answer)
    except Exception as exc:
        return 1, f"ERROR: resident app-server input writeback failed: {exc}"
    lines = [
        f"thread_id: {target_thread_id}",
        f"request_id: {result.get('request_id') or '-'}",
        "transport: resident-app-server input",
    ]
    answers_by_question = result.get("answers_by_question") or {}
    if isinstance(answers_by_question, dict):
        for question_id, values in answers_by_question.items():
            if isinstance(values, list):
                lines.append(f"{question_id}: {' | '.join(str(value) for value in values)}")
    return 0, "\n".join(lines)


def get_pending_interactive_state(
    target_thread_id: str,
    *,
    client: object | None = None,
) -> str | None:
    active_client = client or app_server_transport.DEFAULT_CLIENT
    try:
        pending_request = active_client.get_latest_pending_approval_request(target_thread_id)
    except Exception:
        pending_request = None
    if pending_request is not None:
        return "approval"
    try:
        pending_input = active_client.get_latest_pending_input_request(target_thread_id)
    except Exception:
        pending_input = None
    if pending_input is not None:
        return "input"
    return None
