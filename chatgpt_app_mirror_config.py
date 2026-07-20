from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ChatGptAppMirrorConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChatGptAppMirrorConfig:
    enabled: bool
    cdp_http_url: str
    discord_thread_ids: tuple[int, ...]
    poll_seconds: float


def load_chatgpt_app_mirror_config(environ: Mapping[str, str]) -> ChatGptAppMirrorConfig:
    enabled = _parse_flag(environ.get("CHATGPT_APP_MIRROR_ENABLED", ""))
    if not enabled:
        return ChatGptAppMirrorConfig(
            enabled=False,
            cdp_http_url="http://127.0.0.1:9222",
            discord_thread_ids=(),
            poll_seconds=2.0,
        )

    cdp_http_url = _parse_loopback_http_url(
        environ.get("CHATGPT_APP_CDP_URL", "http://127.0.0.1:9222")
    )
    discord_thread_ids = _parse_discord_thread_ids(
        environ.get("CHATGPT_APP_MIRROR_DISCORD_THREAD_IDS", "")
    )
    poll_seconds = _parse_poll_seconds(
        environ.get("CHATGPT_APP_MIRROR_POLL_SECONDS", "")
    )
    return ChatGptAppMirrorConfig(
        enabled=True,
        cdp_http_url=cdp_http_url,
        discord_thread_ids=discord_thread_ids,
        poll_seconds=poll_seconds,
    )


def _parse_flag(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    return value not in {"0", "false", "no", "off"}


def _parse_loopback_http_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ChatGptAppMirrorConfigError(
            "CHATGPT_APP_CDP_URL must use HTTP on a loopback host"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ChatGptAppMirrorConfigError(
            "CHATGPT_APP_CDP_URL must not include credentials, query, or fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ChatGptAppMirrorConfigError("CHATGPT_APP_CDP_URL has an invalid port") from exc
    if port is None or port <= 0:
        raise ChatGptAppMirrorConfigError("CHATGPT_APP_CDP_URL must include a port")
    return value


def _parse_discord_thread_ids(raw: str) -> tuple[int, ...]:
    parts = tuple(part.strip() for part in raw.split(",") if part.strip())
    if len(parts) != 5:
        raise ChatGptAppMirrorConfigError(
            "CHATGPT_APP_MIRROR_DISCORD_THREAD_IDS must contain exactly five IDs"
        )
    try:
        values = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise ChatGptAppMirrorConfigError("Discord thread IDs must be integers") from exc
    if any(value <= 0 for value in values):
        raise ChatGptAppMirrorConfigError("Discord thread IDs must be positive")
    if len(set(values)) != len(values):
        raise ChatGptAppMirrorConfigError("Discord thread IDs must be unique")
    return values


def _parse_poll_seconds(raw: str) -> float:
    if not raw.strip():
        return 2.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise ChatGptAppMirrorConfigError(
            "CHATGPT_APP_MIRROR_POLL_SECONDS must be a number"
        ) from exc
    if not 0.5 <= value <= 60.0:
        raise ChatGptAppMirrorConfigError(
            "CHATGPT_APP_MIRROR_POLL_SECONDS must be between 0.5 and 60"
        )
    return value
