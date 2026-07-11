from __future__ import annotations

import unittest
from typing import final

import codex_discord_session_mirror_archive as archive_policy


@final
class _State:
    active_output_targets: set[str]
    pending_cursor_targets: set[str]

    def __init__(self, target: str) -> None:
        self.active_output_targets = {target}
        self.pending_cursor_targets = {target}


@final
class _Owner:
    _session_mirror_archive_skip_logged: set[str]
    _session_mirror_seen_agent_messages: dict[str, dict[str, float]]
    _session_mirror_seen_user_messages: dict[str, dict[str, float]]

    def __init__(self, target: str) -> None:
        self._session_mirror_archive_skip_logged = {target}
        self._session_mirror_seen_agent_messages = {target: {"a": 1.0}}
        self._session_mirror_seen_user_messages = {target: {"u": 1.0}}

    def archive_skip_logged(self) -> set[str]:
        return self._session_mirror_archive_skip_logged

    def seen_agent_messages(self) -> dict[str, dict[str, float]]:
        return self._session_mirror_seen_agent_messages

    def seen_user_messages(self) -> dict[str, dict[str, float]]:
        return self._session_mirror_seen_user_messages


class SessionMirrorArchivePolicyTests(unittest.TestCase):
    def test_gpt_archive_cleanup_preserves_every_runtime_cursor_branch(self) -> None:
        target = "gpt-thread"
        owner = _Owner(target)

        def forbidden_call(*_values: str | None) -> None:
            raise AssertionError("destructive GPT archive cleanup was called")

        def forbidden_state() -> _State:
            raise AssertionError("GPT archive runtime state was read")

        counts = archive_policy.cleanup_archived_session_mirror_state(
            owner,
            target,
            deps=archive_policy.ArchiveMirrorCleanupDeps(
                delete_archived_mirror_state=lambda _target: {
                    "mirror_threads": 0,
                    "session_mirror_offsets": 0,
                    "destructive_cleanup_allowed": 0,
                },
                get_session_mirror_state=forbidden_state,
                normalize_runner_key=lambda value: str(value),
                deactivate_session_mirror_output_target=forbidden_call,
                parse_bridge_output_value=lambda _output, _key: "",
                format_log_argv=lambda _argv: "",
                exception_types=(RuntimeError,),
                format_exception=lambda: "",
                log=lambda _message: None,
            ),
        )

        self.assertEqual(set(counts.values()), {0})
        self.assertEqual(owner.archive_skip_logged(), {target})
        self.assertIn(target, owner.seen_agent_messages())
        self.assertIn(target, owner.seen_user_messages())

    def test_ordinary_archive_cleanup_retains_existing_destructive_behavior(
        self,
    ) -> None:
        target = "ordinary-thread"
        owner = _Owner(target)
        state = _State(target)

        def deactivate(value: str | None) -> None:
            key = str(value)
            state.active_output_targets.discard(key)
            state.pending_cursor_targets.discard(key)

        counts = archive_policy.cleanup_archived_session_mirror_state(
            owner,
            target,
            deps=archive_policy.ArchiveMirrorCleanupDeps(
                delete_archived_mirror_state=lambda _target: {
                    "mirror_threads": 1,
                    "session_mirror_offsets": 1,
                    "destructive_cleanup_allowed": 1,
                },
                get_session_mirror_state=lambda: state,
                normalize_runner_key=lambda value: str(value),
                deactivate_session_mirror_output_target=deactivate,
                parse_bridge_output_value=lambda _output, _key: "",
                format_log_argv=lambda _argv: "",
                exception_types=(RuntimeError,),
                format_exception=lambda: "",
                log=lambda _message: None,
            ),
        )

        self.assertEqual(counts["mirror_threads"], 1)
        self.assertEqual(counts["session_mirror_offsets"], 1)
        self.assertEqual(state.active_output_targets, set())
        self.assertEqual(state.pending_cursor_targets, set())
        self.assertEqual(owner.archive_skip_logged(), set())

    def test_archive_recommended_without_active_output_logs_once_and_tails_only(
        self,
    ) -> None:
        skip_logged: set[str] = set()
        logs: list[str] = []

        first_tail_only = archive_policy.resolve_session_mirror_archive_policy(
            "thread-1",
            archive_recommended=True,
            active_output_target=False,
            archive_skip_logged=skip_logged,
            log=logs.append,
        )
        second_tail_only = archive_policy.resolve_session_mirror_archive_policy(
            "thread-1",
            archive_recommended=True,
            active_output_target=False,
            archive_skip_logged=skip_logged,
            log=logs.append,
        )

        self.assertTrue(first_tail_only)
        self.assertTrue(second_tail_only)
        self.assertEqual(skip_logged, {"thread-1"})
        self.assertEqual(
            logs,
            [
                "session_mirror_archive_tail_only target=thread-1 reason=archive_recommended"
            ],
        )

    def test_active_output_with_prior_skip_logs_override_and_clears_marker(
        self,
    ) -> None:
        skip_logged = {"thread-1"}
        logs: list[str] = []

        archive_tail_only = archive_policy.resolve_session_mirror_archive_policy(
            "thread-1",
            archive_recommended=True,
            active_output_target=True,
            archive_skip_logged=skip_logged,
            log=logs.append,
        )

        self.assertFalse(archive_tail_only)
        self.assertEqual(skip_logged, set())
        self.assertEqual(
            logs,
            [
                "session_mirror_archive_skip_overridden target=thread-1 reason=active_ask"
            ],
        )

    def test_non_archive_recommended_clears_stale_marker_without_log(self) -> None:
        skip_logged = {"thread-1"}
        logs: list[str] = []

        archive_tail_only = archive_policy.resolve_session_mirror_archive_policy(
            "thread-1",
            archive_recommended=False,
            active_output_target=False,
            archive_skip_logged=skip_logged,
            log=logs.append,
        )

        self.assertFalse(archive_tail_only)
        self.assertEqual(skip_logged, set())
        self.assertEqual(logs, [])

    def test_active_output_without_prior_skip_does_not_log_override(self) -> None:
        skip_logged: set[str] = set()
        logs: list[str] = []

        archive_tail_only = archive_policy.resolve_session_mirror_archive_policy(
            "thread-1",
            archive_recommended=True,
            active_output_target=True,
            archive_skip_logged=skip_logged,
            log=logs.append,
        )

        self.assertFalse(archive_tail_only)
        self.assertEqual(skip_logged, set())
        self.assertEqual(logs, [])
