# Codex Discord Harness

A Windows-local Discord frontend for operating a signed-in Codex Desktop session from Discord.

This is not an official OpenAI client, hosted relay, or npm package. It is a local operator tool for a Windows machine where Codex Desktop is already installed, signed in, awake, and trusted.

This repository is Windows-only in practice. The bot, watchdog, tray launcher, and local Codex Desktop bridge depend on Windows paths, PowerShell scripts, and a signed-in Windows Codex Desktop session.

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
- installs the local Codex plugin marketplace and `codex-discord-harness` plugin so bundled skills are available

To preview what the installer would do:

```powershell
.\install.ps1 -DryRun
```

Use `.\install.ps1 -SkipCodexPlugin` only when you want to skip Codex plugin registration.

## Install The Codex Plugin

The repository includes a local Codex plugin marketplace at `.agents\plugins\marketplace.json`.
The installer registers it automatically. To install or reinstall the plugin manually from the repository root:

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


## More Documentation

- [Bundled skills and attribution](docs/plugin-skills.md)
- [Daily workflow and Discord commands](docs/operations.md)
- [Steering, transport, validation, and project position](docs/policies-validation.md)

Bundled workflow skills include `deep-interview`, `github-project-triage`, and `maintainer-orchestrator`.

Registered Discord slash commands:

- /help, /list, /archived_list, /use, /status, /settings, /doctor, /where, /context, /usage, /runners, /retract, /mirror_check, /bridge_sync, /new, /ask, /interview, /github_triage, /maintainer_orchestrator (legacy alias: /ask_ipc)

Common `!` commands include `!help`, `!list`, `!archived_list`, `!use`, `!open`, `!open_abort`, `!stop`, `!status`, `!settings`, `!setting`, `!doctor`, `!discover_codex`, `!restart_codex`, `!reset_pc`, `!chatid`, `!where`, `!context`, `!usage`, `!runners`, `!resources`, `!system`, `!retract`, `!bridge`, `!mirror`, `!approval`, `!archive`, `!delete_archive`, `!confirm_delete_archive`, `!new`, `!ask`, `!interview`, `!triage`, and `!orchestrate`.

Numeric refs follow the same DB-root numbering as `!list`.

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
- Messages authored by other bots are ignored unless they explicitly mention the Codex bridge user. Restart-check handoff packets are a narrow exception: `ACTION: RESTART-CHECK / HANDOFF` can route when it includes an explicit `codex/session` thread id.
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
