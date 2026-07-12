from __future__ import annotations

# The delivery flow's concrete public contract requires the real standard-library lock.
from asyncio import Lock as AsyncioLock  # noqa: F401  # noqa: ANYIO_OK
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from anyio import Event, create_task_group
from anyio.to_thread import run_sync

import codex_discord_gpt_delivery as gpt_delivery
import codex_discord_session_mirror_delivery_flow as delivery_flow
import codex_discord_store_session_mirror as session_mirror_store
from codex_discord_store_schema import init_store_schema


_TEMP_PREFIX = "app-gpt-discord-sync-todo-09-"


class FakeChannel: ...


new_configured_channel_lock = AsyncioLock


class GptDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def _db_path(self, temp_dir: str) -> Path:
        db_path = Path(temp_dir) / "delivery.sqlite"
        with closing(sqlite3.connect(db_path)) as conn, conn:
            init_store_schema(conn)
            _ = conn.execute(
                "INSERT INTO mirror_threads VALUES "
                + "(?, 'codex:chats', 'GPT', 100, 200, 1.0, 'gpt_chat', 'active')",
                ("gpt-thread",),
            )
        return db_path

    def _set_state(self, db_path: Path, state: str) -> None:
        with closing(sqlite3.connect(db_path)) as conn, conn:
            _ = conn.execute(
                "UPDATE mirror_threads SET lifecycle_state = ? "
                + "WHERE codex_thread_id = 'gpt-thread'",
                (state,),
            )

    def _lease_deps(
        self,
        lock: AsyncioLock,
        db_path: Path,
        order: list[str],
    ) -> tuple[
        gpt_delivery.ActiveDeliveryLeaseDeps,
        gpt_delivery.ActiveDeliveryIdentity,
    ]:
        expected_identity = session_mirror_store.get_session_mirror_delivery_identity(
            db_path,
            "gpt-thread",
        )
        if expected_identity is None:
            raise AssertionError("active delivery fixture has no identity")

        async def reread(
            codex_thread_id: str,
        ) -> gpt_delivery.ActiveDeliveryIdentity | None:
            order.append("reread")
            return await run_sync(
                session_mirror_store.get_session_mirror_delivery_identity,
                db_path,
                codex_thread_id,
                abandon_on_cancel=True,
            )

        lease_deps = gpt_delivery.ActiveDeliveryLeaseDeps(lock, reread)
        return lease_deps, expected_identity

    async def _forbidden_channel(self, discord_thread_id: int) -> FakeChannel | None:
        self.fail(f"channel resolution ran for {discord_thread_id}")

    async def _forbidden_event(self, digest: str, codex_thread_id: str) -> bool:
        self.fail(f"event boundary ran for {digest} {codex_thread_id}")

    async def _forbidden_send(
        self,
        channel: FakeChannel,
        item: delivery_flow.SessionMirrorItem,
        *,
        target_thread_id: str,
        target_ref: str,
    ) -> None:
        _ = (channel, item)
        self.fail(f"send ran for {target_thread_id} {target_ref}")

    async def _forbidden_cursor(
        self,
        codex_thread_id: str,
        rollout_path: str,
        cursor: int,
    ) -> None:
        self.fail(f"cursor commit ran for {codex_thread_id} {rollout_path} {cursor}")

    def _forbidden_deactivate(self, codex_thread_id: str) -> None:
        self.fail(f"output deactivation ran for {codex_thread_id}")

    async def test_delivery_wins_lock_then_deactivation_runs(self) -> None:
        await self.assert_delivery_first(new_configured_channel_lock())

    async def assert_delivery_first(self, lock: AsyncioLock) -> None:
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            order: list[str] = []
            send_started = Event()
            release_send = Event()
            channel = FakeChannel()
            lease_deps, expected_identity = self._lease_deps(lock, db_path, order)

            async def resolve_channel(discord_thread_id: int) -> FakeChannel | None:
                self.assertEqual(discord_thread_id, 200)
                order.append("resolve")
                return channel

            async def has_event(digest: str, codex_thread_id: str) -> bool:
                self.assertEqual((digest, codex_thread_id), ("digest", "gpt-thread"))
                order.append("has")
                return False

            async def send_item(
                channel: FakeChannel,
                item: delivery_flow.SessionMirrorItem,
                *,
                target_thread_id: str,
                target_ref: str,
            ) -> None:
                _ = (channel, item, target_thread_id, target_ref)
                order.append("send:start")
                send_started.set()
                await release_send.wait()
                order.append("send:end")

            async def claim_event(digest: str, codex_thread_id: str) -> bool:
                _ = (digest, codex_thread_id)
                order.append("claim")
                return True

            async def update_cursor(
                codex_thread_id: str, rollout_path: str, cursor: int
            ) -> None:
                _ = (codex_thread_id, rollout_path, cursor)
                order.append("cursor")

            deps = delivery_flow.SessionMirrorDeliveryFlowDeps(
                configured_channel_lock=lock,
                active_delivery_lease_deps=lease_deps,
                resolve_session_mirror_channel=resolve_channel,
                resolve_target_ref=lambda _target: ("gpt-thread", "codex:chats"),
                has_session_mirror_event=has_event,
                send_session_mirror_item=send_item,
                claim_session_mirror_event=claim_event,
                update_session_mirror_cursor=update_cursor,
                deactivate_session_mirror_output_target=lambda _target: order.append(
                    "output-deactivate"
                ),
                log=lambda _message: None,
            )

            async def deactivate() -> None:
                await send_started.wait()
                async with lock:
                    await run_sync(
                        self._set_state,
                        db_path,
                        "inactive",
                        abandon_on_cancel=True,
                    )
                    order.append("gpt-deactivate")

            delivered = False

            async def deliver() -> None:
                nonlocal delivered
                delivered = await delivery_flow.deliver_and_commit_session_mirror_items(
                    "gpt-thread",
                    "rollout.jsonl",
                    42,
                    discord_thread_id=200,
                    expected_identity=expected_identity,
                    event_count=1,
                    items=[{"kind": "final", "text": "done", "digest": "digest"}],
                    deps=deps,
                )

            async with create_task_group() as tasks:
                _ = tasks.start_soon(deliver)
                _ = tasks.start_soon(deactivate)
                await send_started.wait()
                release_send.set()

        self.assertTrue(delivered)
        self.assertEqual(
            order,
            "reread resolve has send:start send:end claim output-deactivate cursor gpt-deactivate".split(),
        )

    async def test_deactivation_wins_lock_and_blocks_send_claim_commit(self) -> None:
        await self.assert_deactivation_first(new_configured_channel_lock())

    async def assert_deactivation_first(self, lock: AsyncioLock) -> None:
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            order: list[str] = []
            deactivation_locked = Event()
            release_deactivation = Event()
            lease_deps, expected_identity = self._lease_deps(lock, db_path, order)

            async def deactivate() -> None:
                async with lock:
                    await run_sync(
                        self._set_state,
                        db_path,
                        "inactive",
                        abandon_on_cancel=True,
                    )
                    order.append("gpt-deactivate")
                    deactivation_locked.set()
                    await release_deactivation.wait()

            deps = delivery_flow.SessionMirrorDeliveryFlowDeps(
                configured_channel_lock=lock,
                active_delivery_lease_deps=lease_deps,
                resolve_session_mirror_channel=self._forbidden_channel,
                resolve_target_ref=lambda target: (target, target),
                has_session_mirror_event=self._forbidden_event,
                send_session_mirror_item=self._forbidden_send,
                claim_session_mirror_event=self._forbidden_event,
                update_session_mirror_cursor=self._forbidden_cursor,
                deactivate_session_mirror_output_target=self._forbidden_deactivate,
                log=lambda _message: None,
            )

            delivered = True

            async def deliver() -> None:
                nonlocal delivered
                delivered = await delivery_flow.deliver_and_commit_session_mirror_items(
                    "gpt-thread",
                    "rollout.jsonl",
                    42,
                    discord_thread_id=200,
                    expected_identity=expected_identity,
                    event_count=1,
                    items=[{"kind": "final", "text": "done", "digest": "digest"}],
                    deps=deps,
                )

            async with create_task_group() as tasks:
                _ = tasks.start_soon(deactivate)
                await deactivation_locked.wait()
                _ = tasks.start_soon(deliver)
                release_deactivation.set()

        self.assertFalse(delivered)
        self.assertEqual(order, ["gpt-deactivate", "reread"])

    def test_second_lock_injection_fails_before_delivery(self) -> None:
        canonical_lock = AsyncioLock()
        order: list[str] = []
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            lease_deps, _expected = self._lease_deps(canonical_lock, db_path, order)

            with self.assertRaises(gpt_delivery.ConfiguredChannelLockMismatchError):
                gpt_delivery.require_configured_channel_lock(
                    AsyncioLock(),
                    lease_deps,
                )

        self.assertEqual(order, [])

    async def test_ownership_change_invalidates_prepared_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temp_dir:
            db_path = self._db_path(temp_dir)
            lock = AsyncioLock()
            lease_deps, expected = self._lease_deps(lock, db_path, [])

            with closing(sqlite3.connect(db_path)) as conn, conn:
                _ = conn.execute(
                    "UPDATE mirror_threads SET managed_by = 'ordinary' "
                    + "WHERE codex_thread_id = 'gpt-thread'"
                )
            async with gpt_delivery.active_delivery_lease(
                expected_identity=expected,
                configured_channel_lock=lock,
                deps=lease_deps,
            ) as active_identity:
                self.assertFalse(active_identity)
