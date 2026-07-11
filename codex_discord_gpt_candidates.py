from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, override

import codex_app_server_transport as app_server_transport
import codex_desktop_bridge_thread_store as thread_store
import codex_discord_app_server_thread_filter as app_server_thread_filter
import codex_discord_bridge_modules as bridge_modules
import codex_discord_project_paths as project_paths
from codex_discord_logging import log_line
from codex_discord_text import env_flag
from codex_thread_models import ThreadInfo

GPT_CHAT_PROJECT_KEY: Final = "codex:chats"
RESIDENT_APP_SERVER_TRANSPORT: Final = "resident-app-server"

LoadUserRootThreads = Callable[[int], list[ThreadInfo]]
DeriveProjectKey = Callable[[ThreadInfo], str]
FilterAvailableThreads = Callable[[list[ThreadInfo]], list[ThreadInfo]]
TransportName = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class GptCandidateTransportError(RuntimeError):
    actual_transport: str | None

    @override
    def __str__(self) -> str:
        return (
            "GPT chat discovery requires the resident app-server transport; "
            f"received {self.actual_transport or 'none'}."
        )


@dataclass(frozen=True, slots=True)
class GptCandidateDeps:
    load_user_root_threads: LoadUserRootThreads
    derive_project_key: DeriveProjectKey
    filter_app_server_available_threads: FilterAvailableThreads
    transport_name: TransportName


def _derive_project_key(thread: ThreadInfo) -> str:
    return project_paths.get_project_key(
        thread,
        bridge_module=bridge_modules.get_project_bridge_module(),
        projectless_chat_key=GPT_CHAT_PROJECT_KEY,
    )


def _filter_available_threads(threads: list[ThreadInfo]) -> list[ThreadInfo]:
    return app_server_thread_filter.filter_app_server_available_threads_with_deps(
        threads,
        deps=app_server_thread_filter.AppServerThreadFilterDeps(
            app_server_transport_enabled=lambda: True,
            get_client=lambda: app_server_transport.DEFAULT_CLIENT,
            log=log_line,
        ),
    )


def _transport_name() -> str | None:
    if env_flag("CODEX_DISCORD_APP_SERVER_TRANSPORT", True):
        return RESIDENT_APP_SERVER_TRANSPORT
    return None


def load_gpt_candidates_with_deps(
    *,
    deps: GptCandidateDeps,
    limit: int = 0,
) -> tuple[ThreadInfo, ...]:
    transport = deps.transport_name()
    if transport != RESIDENT_APP_SERVER_TRANSPORT:
        raise GptCandidateTransportError(actual_transport=transport)

    user_roots = deps.load_user_root_threads(0)
    app_native = [
        thread
        for thread in user_roots
        if deps.derive_project_key(thread) == GPT_CHAT_PROJECT_KEY
    ]
    available = deps.filter_app_server_available_threads(app_native)
    available.sort(key=lambda thread: thread.updated_at, reverse=True)
    if limit > 0:
        return tuple(available[:limit])
    return tuple(available)


def load_gpt_candidates(limit: int = 0) -> tuple[ThreadInfo, ...]:
    return load_gpt_candidates_with_deps(
        deps=GptCandidateDeps(
            load_user_root_threads=thread_store.load_user_root_threads,
            derive_project_key=_derive_project_key,
            filter_app_server_available_threads=_filter_available_threads,
            transport_name=_transport_name,
        ),
        limit=limit,
    )
