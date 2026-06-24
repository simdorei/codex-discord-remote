from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class HistoryBootstrapAuthor(Protocol):
    @property
    def bot(self) -> bool: ...


class HistoryBootstrapOwner(Protocol):
    @property
    def _history_poll_bootstrap_after(self) -> datetime | None: ...


class HistoryBootstrapMessage(Protocol):
    @property
    def author(self) -> HistoryBootstrapAuthor | None: ...

    @property
    def created_at(self) -> datetime | None: ...


def is_history_bootstrap_user_message(
    owner: HistoryBootstrapOwner,
    message: HistoryBootstrapMessage,
) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    cutoff = getattr(owner, "_history_poll_bootstrap_after", None)
    created_at = getattr(message, "created_at", None)
    if not isinstance(cutoff, datetime) or not isinstance(created_at, datetime):
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return created_at >= cutoff
