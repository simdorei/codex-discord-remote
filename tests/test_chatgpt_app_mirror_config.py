from __future__ import annotations

import unittest

from chatgpt_app_mirror_config import (
    ChatGptAppMirrorConfigError,
    load_chatgpt_app_mirror_config,
)


class ChatGptAppMirrorConfigTests(unittest.TestCase):
    def test_disabled_by_default_without_mapping(self) -> None:
        config = load_chatgpt_app_mirror_config({})

        self.assertFalse(config.enabled)
        self.assertEqual(config.discord_thread_ids, ())

    def test_enabled_config_requires_five_unique_discord_threads(self) -> None:
        with self.assertRaisesRegex(ChatGptAppMirrorConfigError, "exactly five"):
            _ = load_chatgpt_app_mirror_config(
                {
                    "CHATGPT_APP_MIRROR_ENABLED": "1",
                    "CHATGPT_APP_MIRROR_DISCORD_THREAD_IDS": "11,22,33",
                }
            )

        with self.assertRaisesRegex(ChatGptAppMirrorConfigError, "unique"):
            _ = load_chatgpt_app_mirror_config(
                {
                    "CHATGPT_APP_MIRROR_ENABLED": "1",
                    "CHATGPT_APP_MIRROR_DISCORD_THREAD_IDS": "11,22,22,44,55",
                }
            )

    def test_enabled_config_accepts_only_loopback_cdp(self) -> None:
        with self.assertRaisesRegex(ChatGptAppMirrorConfigError, "loopback"):
            _ = load_chatgpt_app_mirror_config(
                {
                    "CHATGPT_APP_MIRROR_ENABLED": "1",
                    "CHATGPT_APP_CDP_URL": "http://192.0.2.10:9222",
                    "CHATGPT_APP_MIRROR_DISCORD_THREAD_IDS": "11,22,33,44,55",
                }
            )

        config = load_chatgpt_app_mirror_config(
            {
                "CHATGPT_APP_MIRROR_ENABLED": "true",
                "CHATGPT_APP_CDP_URL": "http://127.0.0.1:9222/",
                "CHATGPT_APP_MIRROR_DISCORD_THREAD_IDS": "11,22,33,44,55",
                "CHATGPT_APP_MIRROR_POLL_SECONDS": "2.5",
            }
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.cdp_http_url, "http://127.0.0.1:9222")
        self.assertEqual(config.discord_thread_ids, (11, 22, 33, 44, 55))
        self.assertEqual(config.poll_seconds, 2.5)


if __name__ == "__main__":
    _ = unittest.main()
