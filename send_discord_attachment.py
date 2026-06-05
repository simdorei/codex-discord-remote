from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
from pathlib import Path

import aiohttp


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def get_message_content(args: argparse.Namespace) -> str:
    if args.content_file:
        return Path(args.content_file).read_text(encoding="utf-8").strip()
    return str(args.content or "").strip()


async def send_discord_attachment(args: argparse.Namespace) -> dict[str, object]:
    env = load_env()
    token = os.environ.get("DISCORD_BOT_TOKEN") or env.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing from environment or .env")

    files = [Path(path).resolve() for path in args.files]
    missing = [str(path) for path in files if not path.exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))

    form = aiohttp.FormData()
    payload = {
        "content": get_message_content(args),
        "attachments": [{"id": index, "filename": path.name} for index, path in enumerate(files)],
    }
    form.add_field(
        "payload_json",
        json.dumps(payload, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )
    for index, path in enumerate(files):
        form.add_field(
            f"files[{index}]",
            path.read_bytes(),
            filename=path.name,
            content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )

    headers = {"Authorization": f"Bot {token}"}
    url = f"https://discord.com/api/v10/channels/{args.channel_id}/messages"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=form) as response:
            text = await response.text()
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Discord send failed: HTTP {response.status}: {text[:1000]}")
            return json.loads(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send UTF-8 Discord message content with file attachments.")
    parser.add_argument("--channel-id", required=True, help="Discord channel or thread ID to send into.")
    parser.add_argument("--content", default="", help="UTF-8 message content.")
    parser.add_argument("--content-file", help="UTF-8 text file to use as message content.")
    parser.add_argument("files", nargs="+", help="One or more files to attach.")
    return parser.parse_args()


def main() -> None:
    result = asyncio.run(send_discord_attachment(parse_args()))
    print("DISCORD_ATTACHMENT_SENT")
    print("message_id=" + str(result.get("id") or ""))
    print("channel_id=" + str(result.get("channel_id") or ""))
    print(
        "attachments="
        + ",".join(str(attachment.get("filename") or "") for attachment in result.get("attachments", []))
    )


if __name__ == "__main__":
    main()
