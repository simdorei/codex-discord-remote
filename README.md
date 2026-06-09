# Codex Discord Harness

A Windows-local Discord frontend for operating a signed-in Codex Desktop session from Discord.

This is not an official OpenAI client, hosted relay, or npm package. It is a local operator tool for a Windows machine where Codex Desktop is already installed, signed in, awake, and trusted.

## What It Does

- Maps Discord channels and threads to local Codex threads.
- Sends plain Discord messages directly into the mapped Codex thread.
- Mirrors Codex approval/input/follow-up choices back into Discord when the app exposes them.
- Avoids Discord-side global busy prompts while serializing cross-target Codex app turns so one thread does not abort another.
- Supports slash commands, `!` commands, startup diagnostics, history polling, and a tray/watchdog launcher.
- Can be used alongside other Discord bots or Discord-based CLIs. For example, you can ask a separate Discord CLI/bot to do work in a project thread, then mention the Codex bridge in the same Discord thread to inspect, steer, or continue the local Codex-side work.

## Requirements

- Windows 10/11
- Python 3.11 or newer
- Git
- Codex Desktop installed and signed in
- A Discord bot token
- A Discord server/channel where the bot can read and send messages

The Discord bot needs the message content intent enabled in the Discord Developer Portal if you want plain text messages to be forwarded.

## Install

Clone the repository:

```powershell
git clone https://github.com/simdorei/codex-discord-harness.git
cd codex-discord-harness
```

Run the installer:

```powershell
.\install.ps1
```

The installer:

- installs Python dependencies from `requirements.txt`
- creates `.env` from `.env.example` when `.env` is missing

To preview what the installer would do:

```powershell
.\install.ps1 -DryRun
```

## Install The Codex Plugin

The repository includes a local Codex plugin marketplace at `.agents\plugins\marketplace.json`.
Install it from the repository root:

```powershell
codex plugin marketplace add .
codex plugin add codex-discord-harness@codex-discord-harness
```

Confirm that Codex can see the marketplace and plugin:

```powershell
codex plugin marketplace list
codex plugin list
```

Expected entries include:

- marketplace: `codex-discord-harness`
- plugin: `codex-discord-harness@codex-discord-harness`

Restart Codex after installing the plugin so the bundled skills are loaded into new sessions.

Useful plugin-backed scripts can also be run directly from the repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-harness\scripts\status.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-harness\scripts\restart.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-harness\scripts\qa-smoke.ps1 -SkipUnitTests
```

## Configure

Edit `.env`:

```powershell
notepad .env
```

Minimum useful settings:

```dotenv
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_discord_server_id
DISCORD_ALLOWED_CHANNEL_IDS=channel_or_thread_id
DISCORD_STARTUP_CHANNEL_ID=channel_id_for_startup_notice
DISCORD_ENABLE_MESSAGE_CONTENT=1
DISCORD_PLAIN_ASK_MENTION_USER_IDS=your_bridge_bot_user_id
DISCORD_PLAIN_ASK_CONTEXT_FALLBACK=0
DISCORD_STREAM_COMMENTARY=1
DISCORD_CHUNK_MARKERS=1
DISCORD_SESSION_MIRROR=1
DISCORD_SESSION_MIRROR_ARCHIVE_BACKLOG_MAX_EVENTS=200
DISCORD_ENABLE_ATTACHMENTS=1
```

Notes:

- `DISCORD_ALLOWED_CHANNEL_IDS` is a comma-separated allowlist.
- `DISCORD_ALLOWED_USER_IDS` is optional. Leave it empty for all users in allowed channels.
- `DISCORD_PLAIN_ASK_MENTION_USER_IDS` lets plain messages require an explicit mention outside mapped mirror threads.
- In mapped mirror threads, plain messages route to that mapped Codex thread.
- Messages authored by other bots are ignored unless they explicitly mention the Codex bridge user.
- `DISCORD_STREAM_COMMENTARY=1` mirrors Codex in-progress commentary to Discord. Set it to `0` if you only want final answers.
- `DISCORD_CHUNK_MARKERS=1` prefixes multi-part Discord messages with `[1/N]`, `[2/N]`, etc. so long Codex output can be audited for missing chunks.
- `DISCORD_SESSION_MIRROR=1` tails mapped Codex session files and mirrors new app-side user text, commentary, final answers, aborts, and approval/input prompts into the mapped Discord thread.
- `DISCORD_SESSION_MIRROR_ARCHIVE_BACKLOG_MAX_EVENTS=200` limits archive-recommended catch-up reads per poll. Set `0` for unlimited catch-up.
- `DISCORD_ENABLE_ATTACHMENTS=1` saves Discord attachments under `discord_attachments\` and includes the local paths in the Codex prompt. Small text-like attachments are also inlined as previews.

## Run

Visible console launcher:

```powershell
.\codex-discord-bot.cmd
```

Headless launcher with tray/watchdog support:

```powershell
wscript.exe .\codex-discord-bot-headless.vbs
```

The headless launcher writes `discord_launcher.log`. The bot writes `codex_discord_bot.log` unless `CODEX_DISCORD_LOG_PATH` is set.

## Send Attachments Manually

For one-off Codex-to-Discord artifact delivery, use the UTF-8 helper instead of piping Korean text through PowerShell:

```powershell
py -3 .\send_discord_attachment.py --channel-id 123456789012345678 --content "작업 결과입니다." .\result.txt
```

For longer Korean captions, put the content in a UTF-8 text file:

```powershell
py -3 .\send_discord_attachment.py --channel-id 123456789012345678 --content-file .\caption.txt .\image.png
```

## Daily Workflow

1. Start Codex Desktop and sign in.
2. Start the Discord harness.
3. Use `/bridge_sync` to refresh Discord mirror state.
4. Send messages in a mapped Discord thread to operate the matching Codex thread.
5. When Codex asks for approval/input/steering, answer from the Discord controls.

Registered Discord slash commands:

- /help, /list, /archived_list, /use, /status, /doctor, /where, /context, /usage, /runners, /retract, /mirror_check, /bridge_sync, /new, /ask (legacy alias: /ask_ipc)

Common `!` commands:

| Command | Effect |
| --- | --- |
| `!help` | Shows the command list. |
| `!list [limit]` | Lists Codex threads. Without a limit, it uses DB-root user threads; with a limit, it uses the recent thread list. |
| `!archived_list [limit]` | Lists archived Codex threads. Alias: `!archive_list`. |
| `!use <ref>` | Selects a Codex thread by id, workspace ref, or list-style ref. |
| `!open <ref>` | Opens the target Codex thread. |
| `!open_abort <ref>` | Opens the target Codex thread and aborts the active turn. |
| `!status [ref]` | Shows status for the mapped/current thread or a supplied ref. |
| `!doctor` | Runs Discord and local bridge diagnostics. |
| `!discover_codex` | Shows the detected Codex Desktop path. |
| `!restart_codex` | Restarts Codex Desktop through the local bridge. |
| `!chatid` | Prints Discord guild/channel/user ids for configuration. |
| `!where` | Shows the Codex thread mapped to the current Discord channel. |
| `!context [all]` | Shows context usage for the current thread, or mapped threads with `all`. |
| `!usage [days]` | Shows local Codex usage estimates. |
| `!runners` | Shows Discord runner queues. |
| `!retract [ref]` | Removes your latest queued ask for the mapped/current Codex thread or supplied ref. Active asks are not interrupted. |
| `!bridge sync [limit]` | Refreshes local bridge state and Discord mirror state. Without a limit, it uses DB-root user threads. |
| `!mirror sync [limit]` | Syncs Discord mirror project/thread channels. Without a limit, it uses DB-root user threads. |
| `!mirror list [limit]` | Lists mirror mappings. Without a limit, it uses DB-root user threads. |
| `!mirror check [limit]` | Checks mirror mappings and stale rows. Without a limit, it uses DB-root user threads. |
| `!approval` | Re-sends the pending approval controls for the mapped/current Codex thread when one exists. |
| `!archive [ref]` | Archives the mapped/current Codex thread or a supplied ref. Numeric refs follow the same DB-root numbering as `!list`. |
| `!delete_archive <ref>` | Previews deletion of an archived Codex thread. |
| `!confirm_delete_archive <ref>` | Permanently deletes the archived thread after previewing. |
| `!new <prompt>` | Creates a new Codex thread with the first prompt. |
| `!ask <prompt>` | Sends a prompt to the mapped Codex thread, or selected thread outside mirrors. |

## Interop With Other Discord Tools

This harness is intentionally Discord-native, so it can share a Discord thread with other bots or command-line agents that post through Discord.

Useful patterns:

- Use another Discord CLI/bot to run project-specific automation, then mention the Codex bridge to review the result from the local Codex thread.
- Keep a project Discord thread as the shared command surface for humans, Codex, and specialized bots.
- Let other bots post status or artifacts while Codex handles local code edits, approvals, and steering.

Safety behavior:

- Other bot messages are ignored by default.
- Other bot messages are accepted only when they explicitly mention the Codex bridge user.
- The bridge ignores its own messages to avoid loops.

## Steering Policy

Steering is handled by Codex Desktop, not by a Discord-side global busy gate.

- Plain Discord asks go straight to the mapped Codex thread.
- Discord does not preflight idle/busy state and does not auto-queue ordinary asks.
- If Codex Desktop exposes approval/input/follow-up choices, Discord mirrors those choices.
- Same-thread follow-ups can still reach Codex Desktop for steering.
- When a mapped thread is busy, Discord shows `Steer now`, `Queue next`, and `Ignore` controls. `Steer now` injects the prompt into the active turn; `Queue next` waits for the next full turn; `!retract [ref]` removes your latest queued ask before it starts.
- Different target threads wait for the active Codex app turn before starting, because the current desktop transport is single-active-turn in practice.
- The installer does not change Codex Desktop follow-up mode.

## Transport Policy

- Discord ask and steering delivery use a resident `codex app-server` client by default. The bot starts one app-server connection at startup and reuses it for `turn/start`, `turn/steer`, and `turn/interrupt`-style control instead of launching a bridge subprocess for each message.
- App-server approval and request-user-input prompts are cached as server requests. Discord approval/input replies answer the same app-server JSON-RPC request id first, then fall back to the legacy IPC approval path only when no resident request is pending.
- Mapped ask output is mirrored from the local Codex session file after cursor priming, so Discord does not also stream a second copy of the answer.
- Background session mirroring still tails archive-recommended threads. Backlog is not dropped; it is caught up in bounded event batches so a stale cursor does not flood Discord in one poll. Active mapped Discord asks can still temporarily allow mirrored output for that target.
- Legacy IPC/UI/subprocess delivery is disabled for ask/steer by default. Set `CODEX_DISCORD_APP_SERVER_TRANSPORT=0` to force legacy behavior, or `CODEX_DISCORD_APP_SERVER_LEGACY_FALLBACK=1` to allow fallback after resident app-server delivery failure.
- Sidecar transport remains available for non-ask helper operations that still need it, such as archive and local maintenance commands.

## Validation

```powershell
py -3 -m unittest tests.test_codex_discord_bot
py -3 -m py_compile codex_app_server_transport.py codex_desktop_bridge.py codex_windows_harness.py codex_discord_bot.py tests\test_codex_discord_bot.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-watchdog.ps1 -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-tray.ps1 -Once
git diff --check
```

Live Discord QA should verify:

- unmentioned chatter is ignored when mention gating is enabled
- configured bridge mentions are accepted and stripped
- role mentions do not satisfy the user mention gate
- `!` commands and slash commands are unaffected by mention gating
- ordinary asks are submitted without idle/busy preflight or auto-queueing
- ask/steer logs show resident `app_server_transport_started` and `transport: resident-app-server` delivery rather than per-message bridge subprocess delivery
- Codex in-progress commentary appears in Discord before the final answer
- app-side Codex text is mirrored into the mapped Discord thread without replaying old history on startup
- Discord image/text attachments are saved locally and referenced in the Codex prompt
- app-server approval/input requests can be answered from Discord controls
- busy mapped-thread prompts expose explicit steer/queue/ignore controls, and `!retract` removes a still-queued ask without interrupting the active turn
- long Discord sends include `[part/total]` markers and delivery log lines for each chunk
- different mapped target threads route to the correct Codex threads
- cross-target Discord asks wait for the active Codex app turn so they do not abort each other
- other bot messages are ignored unless they mention the Codex bridge
- duplicate bot starts keep one Discord websocket owner
- headless launch shows either the tray icon or clear runtime/log evidence

## Project Position

This repository is a Windows-local operator harness. It is useful for personal or team-operated machines, but it is not a hosted multi-user service and should not be described as a mobile Codex replacement.

## Acknowledgements

The resident app-server transport direction was informed by [NathanZane/codex-mobile](https://github.com/NathanZane/codex-mobile), which explores Discord-based Codex Desktop mirroring, approvals, queueing, and steering workflows.
