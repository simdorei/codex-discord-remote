from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock

import codex_discord_message_dispatch as message_dispatch
import codex_discord_message_target as message_target
import codex_discord_project_runtime as project_runtime


@dataclass(frozen=True, slots=True)
class FakeAuthor:
    id: int


@dataclass(frozen=True, slots=True)
class FakeChannel:
    id: int
    parent_id: int | None = None
    name: str = "channel"


@dataclass(frozen=True, slots=True)
class FakeMessage:
    author: FakeAuthor
    channel: FakeChannel
    content: str
    attachments: tuple[str, ...] = ()
    raw_mentions: tuple[int, ...] = ()
    mentions: tuple[int, ...] = ()


def allow_message_channel(channel: message_dispatch.DispatchChannel) -> bool:
    _ = channel
    return True


def no_bridge_mention(message: message_dispatch.InboundMessage) -> bool:
    _ = message
    return False


class InboundDiscordMessageDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_plain_message_keeps_current_selection_behavior(
        self,
    ) -> None:
        handled: list[tuple[str, str | None]] = []

        async def handle_plain_ask(
            _message: message_dispatch.DispatchMessage,
            content: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            handled.append((content, target_thread_id))

        async def send_chunks(
            _target: message_dispatch.DispatchChannel, _text: str
        ) -> int:
            return 1

        message = FakeMessage(FakeAuthor(7), FakeChannel(11), "ordinary prompt")
        deps = message_dispatch.PreparedMessageDispatchDeps(
            format_log_text_len=len,
            persist_inbound_mirror_thread_channel=lambda _thread_id, _channel_id: None,
            handle_prefix_command=lambda _message, _command: self._unused_awaitable(),
            describe_mirrored_project_channel=lambda _channel_id: None,
            send_chunks=send_chunks,
            handle_plain_ask=handle_plain_ask,
            log=lambda _line: None,
        )

        await message_dispatch.dispatch_prepared_message(
            message,
            message.content,
            message_target.DiscordMessageTarget(None, "selected"),
            deps=deps,
        )

        self.assertEqual(handled, [("ordinary prompt", None)])

    async def test_direct_blocked_target_guard_skips_all_dispatch_callbacks(
        self,
    ) -> None:
        target_fallback = Mock(return_value=None)
        blocked = project_runtime.ExactChannelBlocked("blocked")
        target = message_target.resolve_discord_message_target(
            target_fallback, 11, 10, exact_channel_decision=blocked
        )
        formatter = Mock(side_effect=len)
        persist = Mock()
        prefix = AsyncMock()
        project_fallback = Mock(return_value="fallback")
        chunks = AsyncMock(return_value=1)
        plain_ask = AsyncMock()
        logs: list[str] = []
        deps = message_dispatch.PreparedMessageDispatchDeps(
            formatter, persist, prefix, project_fallback, chunks, plain_ask, logs.append
        )
        message = FakeMessage(FakeAuthor(7), FakeChannel(11), "ignored")

        for content in ("!prompt", "plain prompt"):
            await message_dispatch.dispatch_prepared_message(
                message, content, target, deps=deps
            )

        for callback in (
            target_fallback,
            formatter,
            persist,
            prefix,
            project_fallback,
            chunks,
            plain_ask,
        ):
            callback.assert_not_called()
        self.assertEqual(logs.count("message_blocked chat=11 user=7 reason=blocked"), 2)

    async def _unused_awaitable(self) -> None:
        return None

    async def test_prefix_command_bypasses_mirror_target_lookup_failure(self) -> None:
        logs: list[str] = []
        handled_commands: list[str] = []
        message = FakeMessage(
            author=FakeAuthor(242286902982606848),
            channel=FakeChannel(1522796405209567325),
            content="!list",
        )

        def failing_target_lookup(_channel_id: int | None) -> str | None:
            msg = "target lookup should not run for prefix commands"
            raise AssertionError(msg)

        async def send_restarting_notice(
            target: message_dispatch.DispatchChannel,
        ) -> None:
            _ = target
            return None

        async def maybe_send_empty_content_notice(
            _message: message_dispatch.InboundMessage,
        ) -> None:
            return None

        async def prepare_plain_ask_message_content(
            _message: message_dispatch.InboundMessage,
            _content: str,
            _target_thread_id: str | None,
            *,
            has_attachments: bool,
        ) -> str | None:
            self.assertFalse(has_attachments)
            msg = "plain ask preparation should not run for prefix commands"
            raise AssertionError(msg)

        async def handle_prefix_command(
            _message: message_dispatch.DispatchMessage,
            command: str,
        ) -> None:
            handled_commands.append(command)

        async def send_chunks(
            _target: message_dispatch.DispatchChannel, _text: str
        ) -> int:
            return 1

        async def handle_plain_ask(
            _message: message_dispatch.DispatchMessage,
            _content: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            self.assertIsNone(target_thread_id)
            msg = "plain ask handler should not run for prefix commands"
            raise AssertionError(msg)

        deps = message_dispatch.InboundDiscordMessageProcessDeps(
            require_messageable_channel=lambda channel: channel,
            is_allowed_message_channel=allow_message_channel,
            is_bot_authored_bridge_mention=no_bridge_mention,
            is_allowed_user=lambda user_id: user_id == 242286902982606848,
            is_stopping=lambda: False,
            send_restarting_notice=send_restarting_notice,
            get_mirrored_codex_thread_id=failing_target_lookup,
            get_bridge_mention_user_ids=set,
            maybe_send_empty_content_notice=maybe_send_empty_content_notice,
            prepare_plain_ask_message_content=prepare_plain_ask_message_content,
            persist_inbound_mirror_thread_channel=lambda _target_thread_id, _channel_id: (
                None
            ),
            handle_prefix_command=handle_prefix_command,
            describe_mirrored_project_channel=lambda _channel_id: None,
            send_chunks=send_chunks,
            handle_plain_ask=handle_plain_ask,
            format_log_text_len=len,
            log=logs.append,
        )

        await message_dispatch.process_inbound_discord_message(
            message,
            source="test",
            enable_prefix_commands=True,
            deps=deps,
        )

        self.assertEqual(handled_commands, ["list"])
        self.assertTrue(any("prefix=True" in line for line in logs))

    async def test_exact_active_routes_and_blocked_owner_never_reaches_plain_ask(
        self,
    ) -> None:
        handled: list[str | None] = []
        logs: list[str] = []

        async def no_op_notice(target: message_dispatch.DispatchChannel) -> None:
            _ = target
            return None

        async def no_op_empty(_message: message_dispatch.InboundMessage) -> None:
            return None

        async def prepare(
            _message: message_dispatch.InboundMessage,
            content: str,
            _target_thread_id: str | None,
            *,
            has_attachments: bool,
        ) -> str | None:
            _ = has_attachments
            return content

        async def prefix(
            _message: message_dispatch.DispatchMessage,
            _command: str,
        ) -> None:
            return None

        async def chunks(_target: message_dispatch.DispatchChannel, _text: str) -> int:
            return 1

        async def ask(
            _message: message_dispatch.DispatchMessage,
            _content: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            handled.append(target_thread_id)

        safety = project_runtime.ExactChannelActive("gpt-thread")
        deps = message_dispatch.InboundDiscordMessageProcessDeps(
            require_messageable_channel=lambda channel: channel,
            is_allowed_message_channel=allow_message_channel,
            is_bot_authored_bridge_mention=no_bridge_mention,
            is_allowed_user=lambda _user_id: True,
            is_stopping=lambda: False,
            send_restarting_notice=no_op_notice,
            get_mirrored_codex_thread_id=lambda _channel_id: None,
            get_bridge_mention_user_ids=set,
            maybe_send_empty_content_notice=no_op_empty,
            prepare_plain_ask_message_content=prepare,
            persist_inbound_mirror_thread_channel=lambda _target, _channel: None,
            handle_prefix_command=prefix,
            describe_mirrored_project_channel=lambda _channel: "fallback must not run",
            send_chunks=chunks,
            handle_plain_ask=ask,
            format_log_text_len=len,
            log=logs.append,
            resolve_exact_channel_decision=lambda _channel_id, _channel_name: safety,
        )
        message = FakeMessage(FakeAuthor(7), FakeChannel(901), "hello")

        await message_dispatch.process_inbound_discord_message(
            message,
            source="test",
            enable_prefix_commands=True,
            deps=deps,
        )
        safety = project_runtime.ExactChannelBlocked(
            project_runtime.ExactChannelBlockReason.INACTIVE.value
        )
        await message_dispatch.process_inbound_discord_message(
            message,
            source="test",
            enable_prefix_commands=True,
            deps=deps,
        )

        self.assertEqual(handled, ["gpt-thread"])
        self.assertTrue(any("reason=gpt_inactive" in line for line in logs))


if __name__ == "__main__":
    _ = unittest.main()
