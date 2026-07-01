from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import TracebackType

import setup_discord_bot as setup


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        return None

    def read(self) -> bytes:
        return self.payload


class SetupDiscordBotTests(unittest.TestCase):
    def test_invite_url_uses_expected_permission_bits(self) -> None:
        permissions = setup.get_default_bot_permissions()

        self.assertEqual(permissions, 328565115968)
        self.assertEqual(
            setup.new_discord_bot_invite_url("123", permissions),
            "https://discord.com/oauth2/authorize?client_id=123&scope=bot%20applications.commands&permissions=328565115968",
        )

    def test_env_file_update_preserves_token_equals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("OTHER=value\nDISCORD_BOT_TOKEN=old\n", encoding="utf-8")

            setup.set_env_file_value(env_path, "DISCORD_BOT_TOKEN", "abc=def")

            self.assertEqual(env_path.read_text(encoding="utf-8"), "OTHER=value\nDISCORD_BOT_TOKEN=abc=def\n")

    def test_fetch_discord_application_uses_bot_authorization_header(self) -> None:
        requests: list[urllib.request.Request] = []

        def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
            self.assertEqual(timeout, 15.0)
            requests.append(request)
            return FakeResponse(b'{"id":"42","name":"Codex Remote"}')

        application = setup.fetch_discord_application("secret-token", urlopen=fake_urlopen)

        self.assertEqual(application, setup.DiscordApplication(application_id="42", name="Codex Remote"))
        self.assertEqual(requests[0].headers["Authorization"], "Bot secret-token")

    def test_dry_run_does_not_write_env_or_print_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = setup.run(["--repo-root", temp_dir, "--dry-run", "--bot-id", "42"])

            self.assertEqual(exit_code, 0)
            self.assertFalse((Path(temp_dir) / ".env").exists())
            self.assertIn("client_id=42", output.getvalue())
            self.assertNotIn("DISCORD_BOT_TOKEN=", output.getvalue())


if __name__ == "__main__":
    unittest.main()
