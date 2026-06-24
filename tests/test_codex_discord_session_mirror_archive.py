from __future__ import annotations

import unittest

import codex_discord_session_mirror_archive as archive_policy


class SessionMirrorArchivePolicyTests(unittest.TestCase):
    def test_archive_recommended_without_active_output_logs_once_and_tails_only(self) -> None:
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
        self.assertEqual(logs, ["session_mirror_archive_tail_only target=thread-1 reason=archive_recommended"])

    def test_active_output_with_prior_skip_logs_override_and_clears_marker(self) -> None:
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
        self.assertEqual(logs, ["session_mirror_archive_skip_overridden target=thread-1 reason=active_ask"])

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
