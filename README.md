# Codex Discord Remote

A local Discord remote for Codex Desktop and the Codex app.

Use this repo when you want to operate Codex Desktop from Discord on your own Windows or macOS machine.

This is not an official OpenAI client, hosted relay, or npm package. It is a local operator tool for a machine where Codex Desktop is already installed, signed in, awake, and trusted.

Windows uses Win32 and PowerShell UI Automation. macOS uses AppleScript/System Events and the standard `open`, `pbcopy`, `pbpaste`, `osascript`, and `mdfind` tools. On macOS, grant Accessibility permission to the terminal app that runs the bot so it can focus Codex Desktop, click controls, and send keyboard shortcuts.

## First: Get The Bot Token

Do this first, but do not paste the token during installation. Installation can finish without it.

1. Open <https://discord.com/developers/applications>.
2. Click **New Application**, give it a name, and create it.
3. Open **Bot** in the left sidebar.
4. Click **Reset Token** or **Copy Token**.
5. In **Privileged Gateway Intents**, turn on **Message Content Intent** and save changes.

Keep the token private. After installation, run `.\setup-discord-bot.ps1` on Windows or `./setup-discord-bot.sh` on macOS; it asks for the token with hidden input, saves it to `.env`, and prints the bot invite link.

## What It Does

- Maps Discord channels and threads to local Codex threads.
- Sends plain Discord messages directly into the mapped Codex thread.
- Mirrors Codex approval/input/follow-up choices back into Discord when the app exposes them.
- Mirrors Codex app image and structured file outputs back to Discord as real attachments.
- Avoids Discord-side global busy prompts while serializing cross-target Codex app turns so one thread does not abort another.
- Supports slash commands, `!` commands, startup diagnostics, history polling, and a tray/watchdog launcher.
- Can be used alongside other Discord bots or Discord-based CLIs. For example, you can ask a separate Discord CLI/bot to do work in a project thread, then mention the Codex bridge in the same Discord thread to inspect, steer, or continue the local Codex-side work.

## Requirements

- Windows 10/11 or macOS
- Python 3.11 or newer
- Git
- Codex Desktop installed and signed in
- A Discord bot token from the first section for post-install setup
- A Discord server/channel where the bot can read and send messages

The Discord bot needs the message content intent enabled in the Discord Developer Portal if you want plain text messages to be forwarded.

## Install

Clone the repository:

```powershell
git clone https://github.com/simdorei/codex-discord-remote.git
cd codex-discord-remote
```

Run the installer on Windows:

```powershell
.\install.ps1
```

On macOS:

```sh
./install.sh
```

The installer:

- installs Python dependencies from `requirements.txt`
- creates `.env` from `.env.example` when `.env` is missing
- discovers the Codex Desktop executable and writes `CODEX_DESKTOP_EXE` to `.env`
- installs the local Codex plugin marketplace and `codex-discord-remote` plugin when the `codex` command is available

To preview what the installer would do on Windows:

```powershell
.\install.ps1 -DryRun
```

On macOS:

```sh
./install.sh --dry-run
```

If the `codex` command is not available yet, the bot setup still completes and the installer prints the plugin step it skipped. Use `.\install.ps1 -SkipCodexPlugin` on Windows or `./install.sh --skip-codex-plugin` on macOS only when you want to skip Codex plugin registration explicitly.

On macOS, if the shell scripts are not executable because the repository was copied from a zip file, run:

```sh
chmod +x ./install.sh ./setup-discord-bot.sh ./codex-discord-bot.sh
```

## After Install: Add The Token And Invite The Bot

Run the Discord setup script:

```powershell
.\setup-discord-bot.ps1
```

On macOS:

```sh
./setup-discord-bot.sh
```

The setup script:

- asks for the Discord bot token after installation, with hidden input
- checks the token with Discord
- saves `DISCORD_BOT_TOKEN` to `.env`
- prints an invite link for the bot

Open the invite link, choose your Discord server, and authorize the bot. The invite link adds the bot to a server; it does not choose a single channel. Channel access still depends on Discord channel permissions and the channel IDs in `.env`.

To fill the remaining `.env` values, turn on Discord **User Settings** -> **Advanced** -> **Developer Mode**.
Then right-click the server, channels, threads, and bot user to copy IDs for `DISCORD_GUILD_ID`, `DISCORD_ALLOWED_CHANNEL_IDS`, `DISCORD_STARTUP_CHANNEL_ID`, and `DISCORD_PLAIN_ASK_MENTION_USER_IDS`.

Keep the bot token private. Do not commit `.env`.

Start the bot on Windows:

```powershell
.\codex-discord-bot.cmd
```

On macOS:

```sh
./codex-discord-bot.sh
```

## Install The Codex Plugin

The repository includes a local Codex plugin marketplace at `.agents\plugins\marketplace.json`.
The installer registers it automatically. To install or reinstall the plugin manually from the repository root:

```powershell
codex plugin marketplace add .
codex plugin add codex-discord-remote@codex-discord-remote
```

Confirm that Codex can see the marketplace and plugin:

```powershell
codex plugin marketplace list
codex plugin list
```

Expected entries include:

- marketplace: `codex-discord-remote`
- plugin: `codex-discord-remote@codex-discord-remote`

Restart Codex after installing the plugin so the bundled skills are loaded into new sessions.


## More Documentation

- [Bundled skills and attribution](docs/plugin-skills.md)
- [Daily workflow and Discord commands](docs/operations.md)
- [Steering, transport, validation, and project position](docs/policies-validation.md)

Bundled workflow skills include `deep-interview`.

Registered Discord slash commands:

- /help, /list, /archived_list, /use, /status, /settings, /doctor, /where, /context, /usage, /runners, /retract, /mirror_check, /bridge_sync, /new, /ask, /interview (legacy alias: /ask_ipc)

Common `!` commands include `!help`, `!list`, `!archived_list`, `!use`, `!open`, `!open_abort`, `!stop`, `!status`, `!settings`, `!setting`, `!doctor`, `!discover_codex`, `!restart_codex`, `!reset_pc`, `!chatid`, `!where`, `!context`, `!usage`, `!runners`, `!resources`, `!system`, `!retract`, `!bridge`, `!mirror`, `!approval`, `!archive`, `!delete_archive`, `!confirm_delete_archive`, `!new`, `!ask`, and `!interview`.

Numeric refs follow the same DB-root numbering as `!list`.

## Mirror Behavior

- Mapped Discord threads follow the matching Codex app thread directly. A plain Discord message in that mapped thread is sent to the mapped Codex thread, not to the currently selected Codex app tab.
- Codex app text output is mirrored as Discord text. Codex app image output and structured file output are mirrored as Discord attachments, so a `view_image` result or app-provided file should appear as an uploaded attachment rather than only a path. For local file paths, the mirror only uploads files under `.codex-remote-attachments\`; this avoids accidentally uploading unrelated files from the Windows machine.
- `STOP REPLY` means the Codex app's current-reply stop button: it stops the active response generation in that Codex thread. It is different from Discord `!stop`, which is a remote-control command.

## Configure

Edit `.env`:

```powershell
notepad .env
```

Minimum useful settings:

```dotenv
# Filled by .\setup-discord-bot.ps1:
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
- `DISCORD_ALLOWED_USER_IDS` is a comma-separated list of Discord user IDs, not channel or server IDs. For normal bot use it is optional; host reboot commands such as `!reset_pc confirm` require both `DISCORD_ENABLE_HOST_COMMANDS=1` and a narrow `DISCORD_ALLOWED_USER_IDS` allowlist of trusted Discord user IDs.
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
