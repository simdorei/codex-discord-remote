from __future__ import annotations

import unittest
from typing import Protocol, cast, override

import codex_discord_gpt_cursor as cursor
import codex_discord_gpt_discord_adapter as discord_adapter
import codex_discord_gpt_lifecycle as lifecycle
import codex_discord_gpt_ownership as ownership
import codex_discord_gpt_read_service as read_service
import codex_discord_gpt_snapshots as snapshots
import codex_discord_gpt_sync_workflow as sync_workflow
import codex_discord_gpt_unsync_workflow as unsync_workflow
import codex_discord_prefix_gpt_error_messages as error_messages


class _ComparisonTarget(Protocol):
    pass


class PrefixGptCommandErrorTests(unittest.TestCase):
    @staticmethod
    def _without_fields(error_type: type[RuntimeError]) -> RuntimeError:
        return RuntimeError.__new__(error_type)

    def test_unknown_runtime_error_never_exposes_raw_text(self) -> None:
        secret = "private-owner Discord 987654 C:\\private\\state.sqlite"

        class SecretDiscordError(discord_adapter.GptDiscordError):
            @override
            def __str__(self) -> str:
                return secret

        output = error_messages.format_gpt_command_error(RuntimeError(secret))
        subclass_output = error_messages.format_gpt_command_error(SecretDiscordError())

        self.assertEqual(
            output,
            "GPT command failed: internal GPT state is unavailable.",
        )
        self.assertEqual(subclass_output, output)
        self.assertNotIn(secret, output)
        self.assertNotIn("987654", output)

    def test_typed_error_subclasses_receive_only_the_generic_message(self) -> None:
        class SecretListError(read_service.GptListCountError):
            pass

        class SecretSelectionError(snapshots.GptSnapshotSelectionError):
            pass

        class SecretWorkflowError(unsync_workflow.GptUnsyncPreflightError):
            pass

        class SecretOwnershipError(ownership.DiscordOwnershipConflictError):
            pass

        class SecretSyncPreflightError(sync_workflow.GptSyncPreflightError):
            pass

        class SecretSyncRetryableError(sync_workflow.GptSyncRetryableError):
            pass

        errors: tuple[RuntimeError, ...] = (
            SecretListError("private", read_service.GptListCountErrorReason.MALFORMED),
            SecretSelectionError("private", "missing"),
            SecretWorkflowError(ownership.CodexThreadId("private"), "missing_mapping"),
            SecretOwnershipError(ownership.DiscordThreadId(987654), 2),
            SecretSyncPreflightError("private stale identity"),
            SecretSyncRetryableError("private Discord identifier"),
        )
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(
                    error_messages.format_gpt_command_error(error),
                    "GPT command failed: internal GPT state is unavailable.",
                )

    def test_workflow_and_ownership_errors_hide_internal_ids(self) -> None:
        workflow = unsync_workflow.GptUnsyncPreflightError(
            ownership.CodexThreadId("private-codex-id"),
            "mapping_identity",
        )
        conflict = ownership.DiscordOwnershipConflictError(
            ownership.DiscordThreadId(987654),
            2,
        )

        workflow_output = error_messages.format_gpt_command_error(workflow)
        conflict_output = error_messages.format_gpt_command_error(conflict)

        self.assertEqual(
            workflow_output,
            "GPT command failed: a GPT mapping identity changed.",
        )
        self.assertEqual(
            conflict_output,
            "GPT command failed: GPT ownership is inconsistent.",
        )
        self.assertNotIn("private-codex-id", workflow_output)
        self.assertNotIn("987654", conflict_output)

    def test_only_public_safe_error_details_are_retained(self) -> None:
        access = error_messages.format_gpt_command_error(
            discord_adapter.GptDiscordAccessError()
        )
        selection = error_messages.format_gpt_command_error(
            snapshots.GptSnapshotSelectionError(
                item="private-selection",
                reason="not-positive-decimal",
            )
        )

        self.assertEqual(
            access,
            "GPT command failed: Configured Discord server or channel is inaccessible.",
        )
        self.assertEqual(
            selection,
            "GPT command failed: invalid GPT snapshot selection (not-positive-decimal).",
        )
        self.assertNotIn("private-selection", selection)

        unexpected = error_messages.format_gpt_command_error(
            snapshots.GptSnapshotSelectionError(
                item="private-item",
                reason="private-reason",
            )
        )
        self.assertEqual(
            unexpected,
            "GPT command failed: invalid GPT snapshot selection (invalid).",
        )
        self.assertNotIn("private", unexpected)

    def test_malformed_typed_fields_fall_back_without_exposing_values(self) -> None:
        secret = "private-field-987654"
        errors: tuple[RuntimeError, ...] = (
            read_service.GptListCountError(
                secret,
                cast(
                    read_service.GptListCountErrorReason,
                    cast(object, secret),
                ),
            ),
            snapshots.GptSnapshotMissingError(
                cast(snapshots.GptSnapshotKind, cast(object, secret))
            ),
            cursor.GptCursorSourceError(
                cast(cursor.GptCursorSourceFailure, cast(object, secret))
            ),
            lifecycle.GptCapacityExceededError(
                cast(int, cast(object, secret)),
                1,
                5,
            ),
        )
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                output = error_messages.format_gpt_command_error(error)
                self.assertEqual(
                    output,
                    "GPT command failed: internal GPT state is unavailable.",
                )
                self.assertNotIn(secret, output)

    def test_adversarial_metaclass_hooks_are_never_invoked(self) -> None:
        secret = "private metaclass hook"

        class EqualityBombMeta(type):
            @override
            def __eq__(self, _other: _ComparisonTarget) -> bool:
                raise AssertionError(secret)

            @override
            def __hash__(self) -> int:
                return type.__hash__(self)

        class HashBombMeta(type):
            @override
            def __hash__(self) -> int:
                raise AssertionError(secret)

        class EqualityBomb(RuntimeError, metaclass=EqualityBombMeta):
            pass

        class HashBomb(RuntimeError, metaclass=HashBombMeta):
            pass

        for error in (EqualityBomb(), HashBomb()):
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(
                    error_messages.format_gpt_command_error(error),
                    "GPT command failed: internal GPT state is unavailable.",
                )

    def test_missing_fields_and_huge_capacity_fall_back_safely(self) -> None:
        dynamic_types: tuple[type[RuntimeError], ...] = (
            read_service.GptListCountError,
            snapshots.GptSnapshotSelectionError,
            unsync_workflow.GptUnsyncPreflightError,
            unsync_workflow.GptClearJournalError,
            snapshots.GptSnapshotMissingError,
            snapshots.GptSnapshotExpiredError,
            cursor.GptCursorSourceError,
            lifecycle.GptCapacityExceededError,
        )
        errors = tuple(self._without_fields(error_type) for error_type in dynamic_types)
        errors += (lifecycle.GptCapacityExceededError(10**5000, 1, 5),)
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(
                    error_messages.format_gpt_command_error(error),
                    "GPT command failed: internal GPT state is unavailable.",
                )

    def test_unregistered_exact_enum_members_never_format_their_value(self) -> None:
        class FormatBomb:
            @override
            def __format__(self, _spec: str) -> str:
                raise AssertionError("private enum format hook")

        list_reason = str.__new__(read_service.GptListCountErrorReason, "forged")
        object.__setattr__(list_reason, "_value_", FormatBomb())
        snapshot_kind = str.__new__(snapshots.GptSnapshotKind, "forged")
        object.__setattr__(snapshot_kind, "_value_", FormatBomb())
        cursor_failure = str.__new__(cursor.GptCursorSourceFailure, "forged")
        object.__setattr__(cursor_failure, "_value_", FormatBomb())
        errors: tuple[RuntimeError, ...] = (
            read_service.GptListCountError("private", list_reason),
            snapshots.GptSnapshotMissingError(snapshot_kind),
            snapshots.GptSnapshotExpiredError(snapshot_kind, 1.0),
            cursor.GptCursorSourceError(cursor_failure),
        )
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(
                    error_messages.format_gpt_command_error(error),
                    "GPT command failed: internal GPT state is unavailable.",
                )

    def test_sync_failures_keep_safe_recovery_guidance(self) -> None:
        preflight = error_messages.format_gpt_command_error(
            sync_workflow.GptSyncPreflightError("private stale identity")
        )
        retryable = error_messages.format_gpt_command_error(
            sync_workflow.GptSyncRetryableError("private Discord identifier")
        )

        self.assertEqual(
            preflight,
            "GPT command failed: saved GPT state changed; run !gpt list and try again.",
        )
        self.assertEqual(
            retryable,
            "GPT command failed: GPT sync stopped after starting; retry the same command.",
        )
        self.assertNotIn("private", preflight + retryable)


if __name__ == "__main__":
    _ = unittest.main()
