from __future__ import annotations

import unittest

import codex_discord_gpt_candidates as candidates
from codex_thread_models import ThreadInfo


def _thread(thread_id: str, updated_at: int) -> ThreadInfo:
    return ThreadInfo(
        id=thread_id,
        title=f"title-{thread_id}",
        cwd=f"C:\\fixture\\{thread_id}",
        updated_at=updated_at,
        rollout_path=f"{thread_id}.jsonl",
        model="gpt",
        reasoning_effort="high",
        tokens_used=1,
    )


class GptCandidateTests(unittest.TestCase):
    def test_exact_app_native_candidates(self) -> None:
        rows = [
            _thread("app-old", 10),
            _thread("filesystem", 50),
            _thread("app-recent", 40),
            _thread("app-unavailable", 30),
            _thread("projectless", 60),
        ]
        project_keys = {
            "app-old": "codex:chats",
            "filesystem": r"C:\fixture\repo",
            "app-recent": "codex:chats",
            "app-unavailable": "codex:chats",
            "projectless": "projectless:chat",
        }
        loader_limits: list[int] = []
        availability_inputs: list[tuple[str, ...]] = []

        def load_user_roots(limit: int) -> list[ThreadInfo]:
            loader_limits.append(limit)
            return rows

        def filter_available(threads: list[ThreadInfo]) -> list[ThreadInfo]:
            availability_inputs.append(tuple(thread.id for thread in threads))
            return [thread for thread in threads if thread.id != "app-unavailable"]

        result = candidates.load_gpt_candidates_with_deps(
            deps=candidates.GptCandidateDeps(
                load_user_root_threads=load_user_roots,
                derive_project_key=lambda thread: project_keys[thread.id],
                filter_app_server_available_threads=filter_available,
                transport_name=lambda: candidates.RESIDENT_APP_SERVER_TRANSPORT,
            )
        )

        self.assertEqual(loader_limits, [0])
        self.assertEqual(
            availability_inputs,
            [("app-old", "app-recent", "app-unavailable")],
        )
        self.assertEqual(tuple(thread.id for thread in result), ("app-recent", "app-old"))
        self.assertIsInstance(result, tuple)

    def test_invalid_sources_and_transport_do_not_fallback(self) -> None:
        source_rows = [
            _thread("external-web", 50),
            _thread("subagent", 40),
            _thread("filesystem", 30),
            _thread("generic-projectless", 20),
            _thread("app-native", 10),
        ]
        project_keys = {
            "external-web": "chatgpt:web",
            "subagent": "subagent:worker",
            "filesystem": r"C:\fixture\repo",
            "generic-projectless": "projectless:chat",
            "app-native": "codex:chats",
        }
        calls: list[str] = []

        def loader(limit: int) -> list[ThreadInfo]:
            calls.append(f"load:{limit}")
            return source_rows

        def unavailable(_threads: list[ThreadInfo]) -> list[ThreadInfo]:
            calls.append("availability")
            return []

        for transport in (None, "external-web-chatgpt"):
            with self.subTest(transport=transport):
                calls.clear()
                with self.assertRaises(candidates.GptCandidateTransportError):
                    _ = candidates.load_gpt_candidates_with_deps(
                        deps=candidates.GptCandidateDeps(
                            load_user_root_threads=loader,
                            derive_project_key=lambda thread: project_keys[thread.id],
                            filter_app_server_available_threads=unavailable,
                            transport_name=lambda value=transport: value,
                        )
                    )
                self.assertEqual(calls, [])

        result = candidates.load_gpt_candidates_with_deps(
            deps=candidates.GptCandidateDeps(
                load_user_root_threads=loader,
                derive_project_key=lambda thread: project_keys[thread.id],
                filter_app_server_available_threads=unavailable,
                transport_name=lambda: candidates.RESIDENT_APP_SERVER_TRANSPORT,
            )
        )

        self.assertEqual(result, ())
        self.assertEqual(calls, ["load:0", "availability"])


if __name__ == "__main__":
    _ = unittest.main()
