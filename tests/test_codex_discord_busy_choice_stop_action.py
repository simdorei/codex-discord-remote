from __future__ import annotations

import unittest

import codex_discord_busy_choice_stop_action as stop_action


class FakeChannel:
    id = 222


class FakeInteraction:
    pass


class BusyChoiceStopActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_action_targets_supplied_codex_thread(self) -> None:
        followups: list[tuple[str, str]] = []
        bridge_runs: list[tuple[list[str], str]] = []
        logs: list[str] = []

        async def run_bridge_and_send(
            target: stop_action.StopActionChannel,
            argv: list[str],
            title: str,
        ) -> tuple[int, str]:
            _ = target
            bridge_runs.append((argv, title))
            return 0, "ok"

        async def send_direct_followup(
            interaction: stop_action.StopActionInteraction,
            content: str,
            *,
            log_prefix: str,
            context: str,
        ) -> None:
            _ = interaction
            _ = log_prefix
            followups.append((context, content))

        handled = await stop_action.handle_busy_choice_stop_action(
            FakeInteraction(),
            FakeChannel(),
            "thread-1",
            user_id=123,
            deps=stop_action.BusyChoiceStopActionDeps(
                resolve_target_args=lambda channel_id, ref: ["--thread-id", f"{channel_id}:{ref or '-'}"],
                run_bridge_and_send=run_bridge_and_send,
                send_direct_followup=send_direct_followup,
                log=logs.append,
            ),
        )

        self.assertTrue(handled)
        self.assertEqual(bridge_runs, [(["stop", "--thread-id", "thread-1"], "Stop")])
        self.assertEqual(followups, [("busy_choice_stop_requested", "Stop request sent for this Codex reply.")])
        self.assertIn("busy_choice_stop_start user=123 target=thread-1", logs[0])
        self.assertEqual(logs[-1], "busy_choice_stop_done user=123 target=thread-1 exit=0")

    async def test_stop_action_resolves_channel_mapping_without_thread_id(self) -> None:
        bridge_runs: list[list[str]] = []

        async def run_bridge_and_send(
            target: stop_action.StopActionChannel,
            argv: list[str],
            title: str,
        ) -> tuple[int, str]:
            _ = target
            _ = title
            bridge_runs.append(argv)
            return 0, "ok"

        async def send_direct_followup(
            interaction: stop_action.StopActionInteraction,
            content: str,
            *,
            log_prefix: str,
            context: str,
        ) -> None:
            _ = interaction
            _ = content
            _ = log_prefix
            _ = context

        await stop_action.handle_busy_choice_stop_action(
            FakeInteraction(),
            FakeChannel(),
            None,
            user_id=123,
            deps=stop_action.BusyChoiceStopActionDeps(
                resolve_target_args=lambda channel_id, ref: ["--thread-id", f"{channel_id}:{ref or '-'}"],
                run_bridge_and_send=run_bridge_and_send,
                send_direct_followup=send_direct_followup,
                log=lambda message: None,
            ),
        )

        self.assertEqual(bridge_runs, [["stop", "--thread-id", "222:-"]])


if __name__ == "__main__":
    _ = unittest.main()
