from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from pathlib import Path
import tempfile
import threading
from typing import Literal
import unittest

import codex_discord_session_mirror_delivery_flow as delivery_flow
import codex_discord_store_session_mirror as session_mirror_store
from tests.test_codex_discord_bot_session_mirror_factory import FakeChannel
from tests.test_codex_discord_bot_session_mirror_runtime import FakeOwner
from tests.test_codex_discord_session_mirror_delivery_flow import (
    active_delivery_lease_deps,
)


class GptDeliveryCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_cancelled_store_write_is_drained(
        self,
        blocked_write: Literal["claim", "cursor"],
        *,
        repeat_cancel: bool = False,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="app-gpt-discord-sync-todo-09-cancel-"
        ) as temp_dir:
            db_path = Path(temp_dir) / "delivery.sqlite"
            lock, lease_deps, identity = active_delivery_lease_deps()
            worker_started = asyncio.Event()
            worker_completed = asyncio.Event()
            deactivation_attempted = asyncio.Event()
            deactivation_acquired = asyncio.Event()
            release_worker = threading.Event()
            order: list[str] = []
            loop = asyncio.get_running_loop()
            owner = FakeOwner()

            def mark_worker_completed() -> None:
                order.append("write")
                worker_completed.set()

            def claim_worker(digest: str, codex_thread_id: str) -> bool:
                _ = loop.call_soon_threadsafe(worker_started.set)
                _ = release_worker.wait()
                claimed = session_mirror_store.claim_session_mirror_event(
                    db_path, digest, codex_thread_id
                )
                _ = loop.call_soon_threadsafe(mark_worker_completed)
                return claimed

            def cursor_worker(
                codex_thread_id: str, rollout_path: str, cursor: int
            ) -> None:
                _ = loop.call_soon_threadsafe(worker_started.set)
                _ = release_worker.wait()
                session_mirror_store.update_session_mirror_cursor(
                    db_path, codex_thread_id, rollout_path, cursor
                )
                _ = loop.call_soon_threadsafe(mark_worker_completed)

            async def claim_event(digest: str, codex_thread_id: str) -> bool:
                if blocked_write == "claim":
                    return await asyncio.to_thread(
                        claim_worker, digest, codex_thread_id
                    )
                return session_mirror_store.claim_session_mirror_event(
                    db_path, digest, codex_thread_id
                )

            async def update_cursor(
                codex_thread_id: str, rollout_path: str, cursor: int
            ) -> None:
                if blocked_write == "cursor":
                    await asyncio.to_thread(
                        cursor_worker, codex_thread_id, rollout_path, cursor
                    )
                    return
                session_mirror_store.update_session_mirror_cursor(
                    db_path, codex_thread_id, rollout_path, cursor
                )

            async def deactivate() -> None:
                deactivation_attempted.set()
                async with lock:
                    order.append("deactivate")
                    deactivation_acquired.set()

            deps = delivery_flow.SessionMirrorDeliveryFlowDeps(
                configured_channel_lock=lock,
                active_delivery_lease_deps=lease_deps,
                resolve_session_mirror_channel=owner.fetch_channel,
                resolve_target_ref=lambda target: (target, target),
                has_session_mirror_event=lambda digest, target: asyncio.to_thread(
                    session_mirror_store.has_session_mirror_event,
                    db_path,
                    digest,
                    target,
                ),
                send_session_mirror_item=owner.send_session_mirror_item,
                claim_session_mirror_event=claim_event,
                update_session_mirror_cursor=update_cursor,
                deactivate_session_mirror_output_target=lambda _target: None,
                log=lambda _message: None,
            )
            delivery_task = asyncio.create_task(
                delivery_flow.deliver_and_commit_session_mirror_items(
                    "thread-1",
                    "rollout.jsonl",
                    42,
                    discord_thread_id=333,
                    expected_identity=identity,
                    event_count=1,
                    items=[{"kind": "message", "text": "done", "digest": "digest"}],
                    deps=deps,
                )
            )
            _ = await worker_started.wait()
            _ = delivery_task.cancel()
            deactivation_task = asyncio.create_task(deactivate())
            _ = await deactivation_attempted.wait()
            if repeat_cancel:
                _ = delivery_task.cancel()
            cancellation_propagated_early = delivery_task.done()
            deactivation_acquired_early = deactivation_acquired.is_set()
            release_worker.set()
            _ = await worker_completed.wait()
            with self.assertRaises(asyncio.CancelledError):
                _ = await delivery_task
            await deactivation_task

            self.assertTrue(
                session_mirror_store.has_session_mirror_event(
                    db_path, "digest", "thread-1"
                )
            )
            offset = session_mirror_store.get_session_mirror_offset(db_path, "thread-1")
            if blocked_write == "claim":
                self.assertIsNone(offset)
            else:
                if offset is None:
                    self.fail("cursor worker did not persist its offset")
                self.assertEqual(offset[:2], ("rollout.jsonl", 42))
            early = cancellation_propagated_early, deactivation_acquired_early
            self.assertEqual(early, (False, False))
            self.assertEqual(order, ["write", "deactivate"])
            db_path.unlink()

    async def test_send_cancellation_propagates_before_claim_or_cursor(self) -> None:
        lock, lease_deps, identity = active_delivery_lease_deps()
        send_started = asyncio.Event()
        hold_send = asyncio.Event()
        claims: list[tuple[str, str]] = []
        updates: list[tuple[str, str, int]] = []

        async def send_item(
            channel: FakeChannel,
            item: delivery_flow.SessionMirrorItem,
            *,
            target_thread_id: str,
            target_ref: str,
        ) -> None:
            _ = (channel, item, target_thread_id, target_ref)
            send_started.set()
            _ = await hold_send.wait()

        async def claim_event(digest: str, target: str) -> bool:
            claims.append((digest, target))
            return True

        async def update_cursor(target: str, path: str, cursor: int) -> None:
            updates.append((target, path, cursor))

        deps = delivery_flow.SessionMirrorDeliveryFlowDeps(
            configured_channel_lock=lock,
            active_delivery_lease_deps=lease_deps,
            resolve_session_mirror_channel=lambda _channel_id: _fake_channel(),
            resolve_target_ref=lambda target: (target, target),
            has_session_mirror_event=lambda _digest, _target: _false(),
            send_session_mirror_item=send_item,
            claim_session_mirror_event=claim_event,
            update_session_mirror_cursor=update_cursor,
            deactivate_session_mirror_output_target=lambda _target: None,
            log=lambda _message: None,
        )
        task = asyncio.create_task(
            delivery_flow.deliver_and_commit_session_mirror_items(
                "thread-1",
                "rollout.jsonl",
                42,
                discord_thread_id=333,
                expected_identity=identity,
                event_count=1,
                items=[{"kind": "message", "text": "text", "digest": "digest"}],
                deps=deps,
            )
        )
        _ = await send_started.wait()
        _ = task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            _ = await task

        self.assertEqual(claims, [])
        self.assertEqual(updates, [])
        self.assertFalse(lock.locked())

    async def test_cancellation_while_claim_worker_keeps_lock_until_write(self) -> None:
        await self._assert_cancelled_store_write_is_drained("claim")

    async def test_cancellation_while_cursor_worker_keeps_lock_until_write(
        self,
    ) -> None:
        await self._assert_cancelled_store_write_is_drained(
            "cursor", repeat_cancel=True
        )


async def _fake_channel() -> FakeChannel:
    return FakeChannel()


async def _false() -> bool:
    return False


if __name__ == "__main__":
    _ = unittest.main()
