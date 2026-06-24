import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import codex_desktop_bridge as bridge
import codex_discord_context as context


class DiscordContextTests(unittest.TestCase):
    def test_weekly_usage_message_uses_real_bridge_numeric_helpers(self) -> None:
        original_codex_home = bridge.CODEX_HOME
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                codex_home = Path(temp_dir)
                sessions_dir = codex_home / "sessions"
                sessions_dir.mkdir()
                session_path = sessions_dir / "thread.jsonl"
                event = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "rate_limits": {
                            "primary": {
                                "used_percent": 12.5,
                                "window_minutes": "60",
                                "resets_at": "1893456000",
                            },
                        },
                        "info": {
                            "last_token_usage": {
                                "input_tokens": "10000",
                                "total_tokens": "12345",
                            },
                        },
                    },
                }
                _ = session_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
                bridge.CODEX_HOME = codex_home

                output = context.build_weekly_usage_message(
                    7,
                    bridge_module=bridge,
                    format_percent_func=lambda value: f"{value}%",
                )

            self.assertIn("Codex usage (7d local scan)", output)
            self.assertIn("primary: used=12.5% window=1h resets=2030-01-01", output)
            self.assertIn("token_events: 1", output)
            self.assertIn("total_tokens: 12.3k", output)
            self.assertIn("input_tokens: 10.0k", output)
            self.assertIn("output_tokens_est: 2.3k", output)
        finally:
            bridge.CODEX_HOME = original_codex_home


if __name__ == "__main__":
    _ = unittest.main()
