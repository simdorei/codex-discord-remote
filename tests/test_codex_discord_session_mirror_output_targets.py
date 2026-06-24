from __future__ import annotations

import unittest

import codex_discord_session_mirror as session_mirror
import codex_discord_session_mirror_output_targets as output_targets


class SessionMirrorOutputTargetTests(unittest.TestCase):
    def test_none_target_is_noop_and_false(self) -> None:
        state = session_mirror.SessionMirrorState()

        output_targets.activate_session_mirror_output_target(state, None, active_ttl_seconds=10.0, now_func=lambda: 1.0)
        output_targets.activate_pending_session_mirror_output_target(
            state,
            None,
            active_ttl_seconds=10.0,
            now_func=lambda: 1.0,
        )
        output_targets.deactivate_session_mirror_output_target(state, None)
        output_targets.clear_pending_session_mirror_cursor_target(state, None)

        self.assertFalse(
            output_targets.is_active_session_mirror_output_target(
                state,
                None,
                active_ttl_seconds=10.0,
                now_func=lambda: 1.0,
            )
        )
        self.assertFalse(
            output_targets.is_pending_session_mirror_cursor_target(
                state,
                None,
                active_ttl_seconds=10.0,
                now_func=lambda: 1.0,
            )
        )
        self.assertEqual(state.active_output_targets, {})
        self.assertEqual(state.pending_cursor_targets, set())

    def test_cleanup_removes_expired_active_and_pending_targets(self) -> None:
        state = session_mirror.SessionMirrorState(
            active_output_targets={"thread-1": 1.0, "thread-2": 9.0},
            pending_cursor_targets={"thread-1", "thread-2"},
        )

        output_targets.cleanup_active_session_mirror_output_targets(
            state,
            active_ttl_seconds=10.0,
            now=12.0,
        )

        self.assertEqual(state.active_output_targets, {"thread-2": 9.0})
        self.assertEqual(state.pending_cursor_targets, {"thread-2"})

    def test_pending_activation_clear_and_deactivate_preserve_coupling(self) -> None:
        state = session_mirror.SessionMirrorState()

        output_targets.activate_pending_session_mirror_output_target(
            state,
            "thread-1",
            active_ttl_seconds=10.0,
            now_func=lambda: 5.0,
        )

        self.assertEqual(state.active_output_targets, {"thread-1": 5.0})
        self.assertEqual(state.pending_cursor_targets, {"thread-1"})
        output_targets.clear_pending_session_mirror_cursor_target(state, "thread-1")
        self.assertEqual(state.active_output_targets, {"thread-1": 5.0})
        self.assertEqual(state.pending_cursor_targets, set())

        output_targets.activate_pending_session_mirror_output_target(
            state,
            "thread-1",
            active_ttl_seconds=10.0,
            now_func=lambda: 6.0,
        )
        output_targets.deactivate_session_mirror_output_target(state, "thread-1")

        self.assertEqual(state.active_output_targets, {})
        self.assertEqual(state.pending_cursor_targets, set())
