from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from codex_discord_history_bootstrap import is_history_bootstrap_user_message


@dataclass(frozen=True, slots=True)
class Author:
    bot: bool


@dataclass(frozen=True, slots=True)
class Owner:
    _history_poll_bootstrap_after: datetime | None


@dataclass(frozen=True, slots=True)
class Message:
    author: Author | None
    created_at: datetime | None


class HistoryBootstrapUserMessageTests(unittest.TestCase):
    def test_accepts_recent_user_message(self) -> None:
        # Given
        cutoff = datetime(2026, 6, 17, 8, 0, tzinfo=timezone.utc)
        owner = Owner(_history_poll_bootstrap_after=cutoff)
        message = Message(
            author=Author(bot=False),
            created_at=cutoff + timedelta(seconds=1),
        )

        # When
        result = is_history_bootstrap_user_message(owner, message)

        # Then
        self.assertIs(result, True)

    def test_rejects_bot_author(self) -> None:
        # Given
        cutoff = datetime(2026, 6, 17, 8, 0, tzinfo=timezone.utc)
        owner = Owner(_history_poll_bootstrap_after=cutoff)
        message = Message(author=Author(bot=True), created_at=cutoff)

        # When
        result = is_history_bootstrap_user_message(owner, message)

        # Then
        self.assertIs(result, False)

    def test_treats_naive_times_as_utc(self) -> None:
        # Given
        cutoff = datetime(2026, 6, 17, 8, 0)
        owner = Owner(_history_poll_bootstrap_after=cutoff)
        message = Message(
            author=Author(bot=False),
            created_at=datetime(2026, 6, 17, 8, 0, 1),
        )

        # When
        result = is_history_bootstrap_user_message(owner, message)

        # Then
        self.assertIs(result, True)

    def test_rejects_missing_timestamps(self) -> None:
        # Given
        cutoff = datetime(2026, 6, 17, 8, 0, tzinfo=timezone.utc)
        owner = Owner(_history_poll_bootstrap_after=cutoff)
        message = Message(author=Author(bot=False), created_at=None)

        # When
        result = is_history_bootstrap_user_message(owner, message)

        # Then
        self.assertIs(result, False)
