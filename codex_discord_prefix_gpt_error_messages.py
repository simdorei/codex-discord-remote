"""Public-safe messages for app-native GPT command failures."""

from __future__ import annotations

from typing import Final, Protocol, cast

import codex_discord_gpt_candidates as candidates
import codex_discord_gpt_creation_journal_store as journal_store
import codex_discord_gpt_cursor as cursor
import codex_discord_gpt_discord_adapter as discord_adapter
import codex_discord_gpt_lifecycle as lifecycle
import codex_discord_gpt_ownership as ownership
import codex_discord_gpt_read_service as read_service
import codex_discord_gpt_runtime as gpt_runtime
import codex_discord_gpt_snapshots as snapshots
import codex_discord_gpt_sync_workflow as sync_workflow
import codex_discord_gpt_unsync_workflow as unsync_workflow
import codex_discord_store_startup_probe as startup_probe

_GENERIC_ERROR: Final = "GPT command failed: internal GPT state is unavailable."


class _IdentityValue(Protocol):
    pass


_WORKFLOW_ERROR_MESSAGES: Final[dict[str, str]] = {
    "missing_mapping": "a saved GPT mapping is missing.",
    "mapping_identity": "a GPT mapping identity changed.",
    "snapshot_state": "the saved GPT selection is no longer valid.",
    "many_markers": "GPT recovery found multiple retained threads.",
}
_SNAPSHOT_SELECTION_REASONS: Final = frozenset(
    ("missing", "empty", "not-positive-decimal", "out-of-range", "zero")
)
_FIXED_ERROR_MESSAGES: Final[tuple[tuple[type[RuntimeError], str], ...]] = (
    (
        candidates.GptCandidateTransportError,
        "App-native Codex chat discovery is unavailable.",
    ),
    (journal_store.GptCreationAmbiguityError, "GPT recovery state is ambiguous."),
    (
        journal_store.GptCreationMutationError,
        "GPT recovery state changed unexpectedly.",
    ),
    (discord_adapter.GptDiscordError, "GPT Discord operation failed."),
    (discord_adapter.GptDiscordConfigError, "GPT Discord configuration is missing."),
    (
        discord_adapter.GptDiscordAccessError,
        "Configured Discord server or channel is inaccessible.",
    ),
    (
        discord_adapter.GptDiscordChannelTypeError,
        "The configured Discord channel is not a text channel.",
    ),
    (
        discord_adapter.GptDiscordChannelNotAllowedError,
        "The configured Discord channel is not allowed.",
    ),
    (discord_adapter.GptDiscordScanError, "Discord thread scan did not complete."),
    (discord_adapter.GptDiscordRecoveryError, "GPT Discord recovery cannot continue."),
    (
        discord_adapter.GptDiscordRecoveryAmbiguityError,
        "GPT recovery found conflicting identity or markers.",
    ),
    (
        discord_adapter.GptDiscordRetainedThreadError,
        "The retained Discord thread has conflicting identity.",
    ),
    (
        discord_adapter.GptDiscordCreateError,
        "Discord could not create the GPT thread; retry recovery.",
    ),
    (
        discord_adapter.GptDiscordRenameError,
        "Discord could not finalize the GPT thread; retry recovery.",
    ),
    (
        discord_adapter.GptDiscordUnarchiveError,
        "Discord could not restore the GPT thread; retry recovery.",
    ),
    (
        unsync_workflow.GptDiscordArchiveError,
        "Discord could not archive and lock the retained GPT thread.",
    ),
    (
        sync_workflow.GptSyncPreflightError,
        "saved GPT state changed; run !gpt list and try again.",
    ),
    (
        sync_workflow.GptSyncRetryableError,
        "GPT sync stopped after starting; retry the same command.",
    ),
    (
        gpt_runtime.GptRuntimeInstallationError,
        "GPT runtime installation is incomplete.",
    ),
    (
        gpt_runtime.GptRuntimeNotReadyError,
        "GPT commands are not ready because startup reconciliation is incomplete.",
    ),
    (
        gpt_runtime.GptRuntimeLockError,
        "GPT runtime received a different configured-channel lock.",
    ),
    (
        gpt_runtime.GptRuntimeReconciliationError,
        "GPT startup reconciliation could not resolve an exact Codex chat.",
    ),
    (
        startup_probe.ReconciliationRequiredError,
        "GPT startup reconciliation is incomplete.",
    ),
    (
        cursor.GptCursorPersistenceError,
        "The GPT reactivation cursor could not be saved.",
    ),
    (cursor.GptCursorBatchSizeError, "The GPT cursor scan batch size is invalid."),
    (cursor.GptCursorChunkSizeError, "The GPT cursor copy chunk size is invalid."),
    (lifecycle.GptMappingNotFoundError, "A saved GPT mapping is missing."),
    (lifecycle.GptLifecycleOwnerError, "A saved mapping is not owned by GPT sync."),
    (
        lifecycle.GptLifecycleProjectError,
        "A saved GPT mapping belongs to a different project.",
    ),
    (lifecycle.GptLifecycleStateError, "A saved GPT mapping has an invalid state."),
    (
        lifecycle.GptLifecycleTransitionError,
        "The requested GPT lifecycle change is not allowed.",
    ),
    (
        lifecycle.GptCapacityRequestError,
        "GPT capacity increase must be zero or greater.",
    ),
    (ownership.DiscordOwnershipConflictError, "GPT ownership is inconsistent."),
    (ownership.GptOwnershipOverwriteError, "GPT ownership is inconsistent."),
)


def _message(detail: str) -> str:
    return f"GPT command failed: {detail}"


def _fixed_message(error_type: type[RuntimeError]) -> str | None:
    for expected_type, detail in _FIXED_ERROR_MESSAGES:
        if error_type is expected_type:
            return detail
    return None


def _list_reason_text(reason: _IdentityValue) -> str | None:
    if reason is read_service.GptListCountErrorReason.MALFORMED:
        return "malformed"
    if reason is read_service.GptListCountErrorReason.OUT_OF_RANGE:
        return "out-of-range"
    return None


def _snapshot_kind_text(kind: _IdentityValue) -> str | None:
    if kind is snapshots.GptSnapshotKind.LIST:
        return "list"
    if kind is snapshots.GptSnapshotKind.SYNCED:
        return "synced"
    return None


def _cursor_failure_text(failure: _IdentityValue) -> str | None:
    if failure is cursor.GptCursorSourceFailure.MISSING:
        return "missing"
    if failure is cursor.GptCursorSourceFailure.UNREADABLE:
        return "unreadable"
    if failure is cursor.GptCursorSourceFailure.INVALID:
        return "invalid"
    if failure is cursor.GptCursorSourceFailure.CHANGED:
        return "changed during the cursor scan"
    return None


def format_gpt_command_error(error: RuntimeError) -> str:
    """Return useful guidance only for exact, validated expected errors."""
    if type(error) is read_service.GptListCountError:
        try:
            reason = error.reason
        except AttributeError:
            return _GENERIC_ERROR
        reason_text = _list_reason_text(reason)
        if reason_text is None:
            return _GENERIC_ERROR
        return _message(f"invalid GPT list count ({reason_text}).")
    if type(error) is snapshots.GptSnapshotSelectionError:
        try:
            reason = error.reason
        except AttributeError:
            return _GENERIC_ERROR
        if type(reason) is not str:
            return _GENERIC_ERROR
        if reason not in _SNAPSHOT_SELECTION_REASONS:
            reason = "invalid"
        return _message(f"invalid GPT snapshot selection ({reason}).")
    if (
        type(error) is unsync_workflow.GptUnsyncPreflightError
        or type(error) is unsync_workflow.GptClearJournalError
    ):
        workflow_error = cast(unsync_workflow.GptWorkflowError, error)
        try:
            reason = workflow_error.reason
        except AttributeError:
            return _GENERIC_ERROR
        if type(reason) is not str:
            return _GENERIC_ERROR
        return _message(
            _WORKFLOW_ERROR_MESSAGES.get(reason, "internal GPT state is unavailable.")
        )
    fixed = _fixed_message(type(error))
    if fixed is not None:
        return _message(fixed)
    if type(error) is snapshots.GptSnapshotMissingError:
        try:
            kind = error.kind
        except AttributeError:
            return _GENERIC_ERROR
        kind_text = _snapshot_kind_text(kind)
        if kind_text is None:
            return _GENERIC_ERROR
        return _message(f"no saved {kind_text} GPT snapshot is available.")
    if type(error) is snapshots.GptSnapshotExpiredError:
        try:
            kind = error.kind
        except AttributeError:
            return _GENERIC_ERROR
        kind_text = _snapshot_kind_text(kind)
        if kind_text is None:
            return _GENERIC_ERROR
        return _message(f"the saved {kind_text} GPT snapshot expired.")
    if type(error) is cursor.GptCursorSourceError:
        try:
            failure = error.failure
        except AttributeError:
            return _GENERIC_ERROR
        failure_text = _cursor_failure_text(failure)
        if failure_text is None:
            return _GENERIC_ERROR
        return _message(f"the GPT session source is {failure_text}.")
    if type(error) is lifecycle.GptCapacityExceededError:
        try:
            values = error.used_slots, error.requested_increase, error.limit
        except AttributeError:
            return _GENERIC_ERROR
        if any(type(value) is not int for value in values):
            return _GENERIC_ERROR
        used_slots, requested_increase, limit = values
        if not (0 <= used_slots <= 1_000_000):
            return _GENERIC_ERROR
        if not (0 <= requested_increase <= 1_000_000):
            return _GENERIC_ERROR
        if not (1 <= limit <= 1_000_000):
            return _GENERIC_ERROR
        detail = f"GPT sync capacity is {used_slots}/{limit}; "
        detail += f"an increase of {requested_increase} is not allowed."
        return _message(detail)
    return _GENERIC_ERROR
