from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence, Sized
import unittest

import codex_discord_bot_lifecycle_runtime as lifecycle
import codex_discord_interaction_log as interaction_log


class FakeReadyBot:
    def __init__(self) -> None:
        self.user: interaction_log.DiscordUserLogValue = "bot"
        self.guilds: Sized = []
        self.startup_channel_id: int | None = None
        self.calls: list[str] = []

    async def start_chatgpt_app_mirroring(self) -> None:
        self.calls.append("chatgpt")

    async def start_session_mirroring(self) -> None:
        self.calls.append("codex")

    async def log_startup_diagnostics(self) -> None:
        self.calls.append("diagnostics")


async def _noop_ready(bot: lifecycle.ReadyBot) -> None:
    _ = bot


class ChatGptMirrorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_starts_chatgpt_mirror_after_codex_mirror(self) -> None:
        bot = FakeReadyBot()
        runtime = lifecycle.BotLifecycleRuntime(
            lifecycle.BotLifecycleRuntimeDeps(
                app_server_transport_enabled=lambda: False,
                start_app_server_transport=lambda: None,
                run_in_thread=_unexpected_thread,
                register_commands=lambda value: None,
                make_guild_object=lambda value: value,
                wait_for_slash_sync=_unexpected_sync,
                run_ready_maintenance=_noop_ready,
                send_startup_notice=_noop_ready,
                format_user_id=lambda value: str(value),
                delivery_exceptions=(RuntimeError,),
                log=lambda message: None,
            )
        )

        await runtime.on_ready(bot)

        self.assertEqual(bot.calls, ["codex", "chatgpt", "diagnostics"])


async def _unexpected_thread(action: Callable[[], None]) -> None:
    raise AssertionError(f"unexpected thread action: {action}")


async def _unexpected_sync(
    action: Awaitable[Sequence[lifecycle.SlashCommand]],
    timeout: float,
) -> Sequence[lifecycle.SlashCommand]:
    raise AssertionError(f"unexpected sync: {action}, {timeout}")


if __name__ == "__main__":
    _ = unittest.main()
