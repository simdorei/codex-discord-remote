from __future__ import annotations

from pathlib import Path
import threading
import unittest
from unittest import mock

from anyio import CancelScope, Event, create_task_group, get_cancelled_exc_class, sleep
from anyio.to_thread import run_sync

import codex_discord_gpt_delivery as gpt_delivery
import codex_discord_session_mirror_delivery_flow as delivery_flow
import codex_discord_store_session_mirror as session_mirror_store
from tests.test_codex_discord_bot_session_mirror_factory import make_test_runtime
from tests.test_codex_discord_session_mirror_delivery_flow import (
    active_delivery_lease_deps,
)


class GptIdentityRereadCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_keeps_lock_until_started_identity_read_finishes(
        self,
    ) -> None:
        lock, _lease_deps, identity = active_delivery_lease_deps()
        runtime = make_test_runtime(lock, Path("blocked-identity.sqlite"))
        started = threading.Event()
        completed = threading.Event()
        release = threading.Event()
        scope_ready = Event()
        lease_finished = Event()
        contender_acquired = Event()
        scopes: list[CancelScope] = []
        early = True, True, False
        drain_shields: list[bool] = []

        def tracked_cancel_scope(*, shield: bool = False) -> CancelScope:
            drain_shields.append(shield)
            return CancelScope(shield=shield)

        def blocking_reader(
            _db_path: Path, _owner: str
        ) -> gpt_delivery.ActiveDeliveryIdentity:
            started.set()
            _ = release.wait()
            completed.set()
            return identity

        async def hold_lease() -> None:
            with CancelScope() as scope:
                scopes.append(scope)
                scope_ready.set()
                async with gpt_delivery.active_delivery_lease(
                    identity,
                    configured_channel_lock=lock,
                    deps=runtime.deps.active_delivery_lease_deps,
                ):
                    self.fail("cancelled lease entered its delivery body")
            lease_finished.set()

        async def contend() -> None:
            async with lock:
                contender_acquired.set()

        with (
            mock.patch.object(
                session_mirror_store,
                "get_session_mirror_delivery_identity",
                side_effect=blocking_reader,
            ),
            mock.patch.object(
                delivery_flow,
                "CancelScope",
                side_effect=tracked_cancel_scope,
            ),
        ):
            async with create_task_group() as tasks:
                _ = tasks.start_soon(hold_lease)
                await scope_ready.wait()
                _ = await run_sync(started.wait)
                scope = scopes[0]
                try:
                    scope.cancel()
                    await sleep(0)
                    _ = tasks.start_soon(contend)
                    await sleep(0)
                    scope.cancel()
                    await sleep(0)
                    early = (
                        lease_finished.is_set(),
                        contender_acquired.is_set(),
                        lock.locked(),
                    )
                finally:
                    release.set()
                _ = await run_sync(completed.wait)
                await lease_finished.wait()
                await contender_acquired.wait()

        self.assertEqual(early, (False, False, True))
        self.assertTrue(completed.is_set())
        self.assertEqual(drain_shields, [True])

    async def test_worker_failure_is_retrieved_as_cancellation_cause(self) -> None:
        class WorkerFailure(RuntimeError):
            pass

        started = Event()
        release = Event()
        scope_ready = Event()
        finished = Event()
        scopes: list[CancelScope] = []
        causes: list[BaseException | None] = []

        async def operation() -> None:
            started.set()
            await release.wait()
            raise WorkerFailure("worker failed")

        async def cancel_drain() -> None:
            with CancelScope() as scope:
                scopes.append(scope)
                scope_ready.set()
                try:
                    await delivery_flow.complete_before_cancellation(operation())
                except get_cancelled_exc_class() as cancellation:
                    causes.append(cancellation.__cause__)
            finished.set()

        async with create_task_group() as tasks:
            _ = tasks.start_soon(cancel_drain)
            await scope_ready.wait()
            await started.wait()
            scopes[0].cancel()
            release.set()
            await finished.wait()

        self.assertEqual(len(causes), 1)
        self.assertIsInstance(causes[0], WorkerFailure)


if __name__ == "__main__":
    _ = unittest.main()
