from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import AsyncIterator, Awaitable, Iterable
from pathlib import Path
from typing import Protocol

import codex_discord_gpt_creation_journal as creation_journal
import codex_discord_mirror_sync_result as mirror_sync_result
import codex_discord_store as discord_store


class OrphanMirrorThread(Protocol):
    @property
    def id(self) -> int | str: ...

    @property
    def owner_id(self) -> int | None: ...

    @property
    def name(self) -> str: ...

    def delete(self, *, reason: str) -> Awaitable[None]: ...


class OrphanMirrorProjectChannel(Protocol):
    @property
    def threads(self) -> Iterable[OrphanMirrorThread]: ...

    @property
    def name(self) -> str: ...

    def archived_threads(
        self, *, limit: int | None
    ) -> AsyncIterator[OrphanMirrorThread]: ...


async def _scan_and_delete_orphans(
    project_channels: Iterable[OrphanMirrorProjectChannel],
    protected_ids: set[int],
    marker_tokens: frozenset[creation_journal.GptCreationMarker],
    bot_user_id: int | None,
    *,
    delivery_exceptions: tuple[type[BaseException], ...],
    archived_timeout_seconds: float,
) -> mirror_sync_result.MirrorCleanupResult:
    candidates: list[OrphanMirrorThread] = []
    seen_thread_ids: set[int] = set()
    skipped = 0
    scan_failures = 0
    errors: list[str] = []

    def classify(thread: OrphanMirrorThread) -> None:
        nonlocal skipped
        thread_id = int(thread.id)
        if thread_id in seen_thread_ids:
            return
        seen_thread_ids.add(thread_id)
        marker_nonce = creation_journal.parse_gpt_creation_thread_name(thread.name)
        marker = (
            None
            if marker_nonce is None
            else creation_journal.GptCreationMarker(f"[gpt-sync:{marker_nonce}]")
        )
        if thread_id in protected_ids or marker in marker_tokens:
            skipped += 1
            return
        if bot_user_id is not None and thread.owner_id not in {None, bot_user_id}:
            skipped += 1
            return
        candidates.append(thread)

    for channel in project_channels:
        try:
            active_threads = list(channel.threads)
        except delivery_exceptions as exc:
            scan_failures += 1
            if len(errors) < 3:
                errors.append(f"{channel.name}/active_threads: {exc}")
            continue
        for thread in active_threads:
            classify(thread)
        try:
            async with asyncio.timeout(archived_timeout_seconds):
                async for thread in channel.archived_threads(limit=None):
                    classify(thread)
        except TimeoutError:
            scan_failures += 1
            if len(errors) < 3:
                errors.append(f"{channel.name}/archived_threads: timed out")
        except delivery_exceptions as exc:
            scan_failures += 1
            if len(errors) < 3:
                errors.append(f"{channel.name}/archived_threads: {exc}")

    if scan_failures:
        return {
            "deleted": 0,
            "skipped": skipped,
            "failed": scan_failures,
            "errors": errors,
        }

    deleted = 0
    failed = 0
    for thread in candidates:
        try:
            await thread.delete(reason="Codex mirror cleanup for orphan Discord thread")
            deleted += 1
        except delivery_exceptions as exc:
            failed += 1
            if len(errors) < 3:
                errors.append(f"{thread.name}: {exc}")

    return {
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


async def cleanup_orphan_discord_threads(
    project_channels: Iterable[OrphanMirrorProjectChannel],
    known_thread_ids: set[int],
    bot_user_id: int | None,
    *,
    delivery_exceptions: tuple[type[BaseException], ...],
    archived_timeout_seconds: float = 5.0,
) -> mirror_sync_result.MirrorCleanupResult:
    return await _scan_and_delete_orphans(
        project_channels,
        set(known_thread_ids),
        frozenset(),
        bot_user_id,
        delivery_exceptions=delivery_exceptions,
        archived_timeout_seconds=archived_timeout_seconds,
    )


async def cleanup_configured_channel_orphan_discord_threads(
    project_channels: Iterable[OrphanMirrorProjectChannel],
    known_thread_ids: set[int],
    bot_user_id: int | None,
    *,
    db_path: Path,
    configured_channel_lock: asyncio.Lock,
    delivery_exceptions: tuple[type[BaseException], ...],
    archived_timeout_seconds: float = 5.0,
) -> mirror_sync_result.MirrorCleanupResult:
    async with configured_channel_lock:
        protections = creation_journal.load_gpt_creation_protections(db_path)
        current_known_ids, _ = discord_store.get_remaining_mirror_discord_ids(db_path)
        protected_ids = (
            known_thread_ids
            | current_known_ids
            | {int(thread_id) for thread_id in protections.discord_thread_ids}
        )
        return await _scan_and_delete_orphans(
            project_channels,
            protected_ids,
            protections.marker_tokens,
            bot_user_id,
            delivery_exceptions=delivery_exceptions,
            archived_timeout_seconds=archived_timeout_seconds,
        )
