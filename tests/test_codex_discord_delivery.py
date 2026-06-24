from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import codex_discord_delivery as delivery
import codex_discord_delivery_runtime as delivery_runtime
import codex_discord_delivery_interactions as delivery_interactions
from codex_discord_text import DISCORD_MAX_LEN


class SentMessage:
    def __init__(self, message_id: int) -> None:
        self.id: int = message_id


class FakeTarget:
    def __init__(self, *, channel_id: int = 123, failures: int = 0) -> None:
        self.id: int = channel_id
        self.failures: int = failures
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def send(self, content: str, **kwargs: object) -> SentMessage:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("transient send failure")
        self.messages.append((content, kwargs))
        return SentMessage(len(self.messages))


class FakeInteractionResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append((content, ephemeral))


class FakeInteractionCommand:
    def __init__(self, name: str) -> None:
        self.name: str = name


class FakeInteraction:
    def __init__(self, *, command_name: str = "ask", channel_id: int = 123) -> None:
        self.command: FakeInteractionCommand = FakeInteractionCommand(command_name)
        self.channel_id: int = channel_id
        self.response: FakeInteractionResponse = FakeInteractionResponse()


class DiscordDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_read_attachment_source_bytes_supports_data_url(self) -> None:
        payload = delivery_runtime.read_attachment_source_bytes("data:text/plain;base64,aGVsbG8=")

        self.assertEqual(payload, b"hello")

    def test_read_attachment_source_bytes_rejects_local_file_path(self) -> None:
        with self.assertRaises(delivery_runtime.AttachmentDataUrlError):
            _ = delivery_runtime.read_attachment_source_bytes("C:/tmp/report.txt")

    def test_read_attachment_source_bytes_supports_local_output_file_path(self) -> None:
        output_root = delivery_runtime.CODEX_SESSION_MIRROR_ATTACHMENT_DIR
        output_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=output_root) as temp_dir:
            attachment_path = Path(temp_dir) / "report.txt"
            _ = attachment_path.write_bytes(b"hello file")

            payload = delivery_runtime.read_attachment_source_bytes(str(attachment_path))

        self.assertEqual(payload, b"hello file")

    def test_interaction_helpers_are_reexported_from_delivery_module(self) -> None:
        self.assertIs(
            delivery.send_interaction_response_tracked,
            delivery_interactions.send_interaction_response_tracked,
        )
        self.assertIs(
            delivery.send_interaction_not_allowed,
            delivery_interactions.send_interaction_not_allowed,
        )

    async def test_send_chunks_marks_and_retries_transient_failure(self) -> None:
        logs: list[str] = []
        state = delivery.DiscordDeliveryState(retry_delays_seconds=(0.0,))
        target = FakeTarget(failures=1)

        sent = await delivery.send_chunks(
            state,
            target,
            "retry me",
            log_func=logs.append,
            context="unit_retry",
        )

        self.assertEqual(sent, 1)
        self.assertEqual(target.messages, [("retry me", {})])
        self.assertFalse(state.active_deliveries)
        self.assertTrue(any("discord_delivery_retry" in line for line in logs))
        self.assertTrue(any("discord_delivery_sent" in line for line in logs))

    async def test_stopping_rejects_new_delivery_but_allows_restart_notice(self) -> None:
        logs: list[str] = []
        state = delivery.DiscordDeliveryState()
        target = FakeTarget()
        delivery.set_discord_delivery_stopping(state, "unit", log_func=logs.append)

        with self.assertRaises(delivery.DiscordDeliveryRejected):
            _ = await delivery.send_chunks(state, target, "blocked", log_func=logs.append)

        await delivery.send_discord_restarting_notice(state, target, log_func=logs.append)

        self.assertEqual(len(target.messages), 1)
        self.assertIn("Discord bot is restarting", target.messages[0][0])
        self.assertTrue(any("discord_delivery_rejected" in line for line in logs))
        self.assertTrue(any("context=restart_notice" in line for line in logs))

    async def test_interaction_response_rejects_new_delivery_while_stopping(self) -> None:
        logs: list[str] = []
        state = delivery.DiscordDeliveryState(stopping=True)
        interaction = FakeInteraction()

        with self.assertRaises(delivery.DiscordDeliveryRejected):
            await delivery.send_interaction_response_tracked(
                state,
                interaction,
                "blocked",
                log_func=logs.append,
                ephemeral=True,
            )

        self.assertEqual(interaction.response.messages, [])
        self.assertTrue(any("context=response:" in line for line in logs))

    async def test_wait_for_drain_waits_for_active_delivery(self) -> None:
        logs: list[str] = []
        state = delivery.DiscordDeliveryState()
        token = delivery.begin_discord_delivery(state, "unit", log_func=logs.append)

        async def release() -> None:
            await asyncio.sleep(0.02)
            delivery.end_discord_delivery(state, token)

        release_task = asyncio.create_task(release())
        drained = await delivery.wait_for_discord_delivery_drain(
            state,
            timeout_seconds=1.0,
            reason="unit",
            log_func=logs.append,
        )
        await release_task

        self.assertTrue(drained)
        self.assertTrue(any("discord_delivery_drain_done reason=unit" in line for line in logs))

    def test_split_delivery_chunks_adds_markers_within_discord_limit(self) -> None:
        state = delivery.DiscordDeliveryState(chunk_markers_enabled=True)

        chunks = delivery.split_delivery_chunks("x" * (DISCORD_MAX_LEN + 200), state=state)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(chunks[0].startswith("[1/"))
        self.assertTrue(all(len(chunk) <= DISCORD_MAX_LEN for chunk in chunks))
