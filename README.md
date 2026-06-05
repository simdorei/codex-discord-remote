# Codex Discord Frontend Harness

A Windows-local Discord frontend harness for operating a signed-in Codex app/web session remotely.

This is not a Codex CLI harness, not an official OpenAI client, and not a public hosted service. It is a private-operator wrapper for a Windows machine where Codex Desktop or the Codex web surface is already installed, signed in, awake, and trusted.

한국어 소개: Windows 로컬 Codex 앱/웹 세션을 Discord에서 안전하게 원격 운영하기 위한 Discord-only frontend harness입니다. 멘션 기반 요청, Discord thread별 Codex thread 라우팅, 직접 전달, approval/input 메뉴 미러링, QA 로그, headless 실행 상태 표시를 제공하는 운영 래퍼입니다.

## Scope

- Windows-only local operator machine
- Discord-only frontend
- Codex app/web remains the execution surface
- Discord channels/threads map to local Codex threads

```text
Discord Bot
  -> Discord Router
     - mention gate and optional context fallback
     - ! commands and slash commands
     - Discord thread to Codex thread mapping
     - buttons and user-facing status
  -> Windows Harness
     - Codex app/web runtime status
     - process/run-state lock
     - mapped target thread resolution
     - direct ask delivery and retry evidence
     - app-exposed approval/input choice mirroring
     - structured QA evidence
     - stale process diagnostics
  -> Codex app / Codex web surface
```

## Steering Policy

Steering is useful, but it must be explicit.

- Plain Discord asks go straight to the mapped Codex thread.
- Discord does not preflight idle/busy state and does not auto-queue ordinary asks.
- If the Codex app later exposes approval/input/follow-up choices, Discord mirrors those choices so the operator can answer them.
- Other Codex threads being active should not block a mapped Discord thread.
- `Steer now` should appear only for an explicit existing busy-choice control, not as a local pre-send policy.
- Codex Desktop should use app-side steering for busy follow-ups by default. Add this to the operator's Codex config:

```toml
[desktop]
followUpQueueMode = "steer"
```

This is a Codex Desktop setting, not a Discord bot environment variable. The bridge still avoids Discord-side global busy gates and only mirrors app-exposed approval/input choices when they appear.

## Quick Start

This is a Windows PowerShell/Python harness, not an npm package.

```powershell
.\install.ps1
notepad .env
.\codex-discord-bot.cmd
```

`install.ps1` installs Python dependencies, creates `.env` from `.env.example` when it is missing, and runs `configure-codex-desktop-steering.ps1` by default.

`configure-codex-desktop-steering.ps1` updates the operator's Codex config at `$CODEX_HOME\config.toml` or `%USERPROFILE%\.codex\config.toml`, creates a timestamped backup when the file already exists, and sets:

```toml
[desktop]
followUpQueueMode = "steer"
```

For daily operation without a console window:

```powershell
wscript.exe .\codex-discord-bot-headless.vbs
```

The headless launcher writes `discord_launcher.log`, starts a tray icon, and the bot writes `codex_discord_bot.log` unless `CODEX_DISCORD_LOG_PATH` is set.

## Configuration

Copy `.env.example` to `.env` and set the Discord token/channel allowlist.

Registered Discord slash commands:

- /help, /list, /archived_list, /use, /status, /doctor, /where, /context, /usage, /runners, /mirror_check, /bridge_sync, /new, /ask, /ask_ipc

Important variables:

- `DISCORD_BOT_TOKEN`: Discord bot token
- `DISCORD_ALLOWED_CHANNEL_IDS`: comma-separated allowed channel/thread IDs
- `DISCORD_ALLOWED_USER_IDS`: optional Discord user allowlist
- `DISCORD_PLAIN_ASK_MENTION_USER_IDS`: optional user IDs that must be mentioned before plain messages are forwarded to Codex
- `DISCORD_PLAIN_ASK_CONTEXT_FALLBACK`: optional fallback for unmentioned messages that clearly discuss Codex/Discord bridge operations
- `DISCORD_ENABLE_QA_COMMANDS`: enable smoke commands such as `!qa buttons`
- `DISCORD_HISTORY_POLL_SECONDS`: fallback polling for missed Discord gateway events
- `DISCORD_STEERING_DELIVERY_CONFIRM_TIMEOUT_SECONDS`: IPC steering delivery confirmation wait
- `DISCORD_STEERING_PENDING_WATCH_TIMEOUT_SECONDS`: max watch time after accepted steering
- `DISCORD_STALE_BUSY_STEER_BLOCK_SECONDS`: legacy guard for explicit busy-choice steering controls
- `DISCORD_ASK_BUSY_RETRY_ATTEMPTS`: retry count after the Codex app transport rejects delivery
- `CODEX_HOME`: optional Codex state directory override
- `CODEX_DESKTOP_EXE`: optional Codex Desktop executable override
- `PYTHON_EXE`: optional Python executable for the Windows launcher

## Validation

```powershell
py -3 -m unittest tests.test_codex_discord_bot
py -3 -m py_compile codex_desktop_bridge.py codex_windows_harness.py codex_discord_bot.py tests\test_codex_discord_bot.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-watchdog.ps1 -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-tray.ps1 -Once
git diff --check
```

Live Discord QA should verify:

- unmentioned chatter is ignored when mention gate is enabled
- configured user mention is accepted and stripped
- role mentions do not satisfy the user mention gate
- `!` commands and slash commands are unaffected by mention gating
- Korean operational messages such as `디코 봇 응답 없어` are accepted only when context fallback is enabled
- ordinary asks are submitted without idle/busy preflight or auto-queueing
- app-exposed approval/input menus are mirrored when they appear after delivery
- different target threads can run independently
- duplicate bot starts keep one Discord websocket owner
- headless launch shows either the tray icon or clear runtime/log evidence

## Release Position

This repo should be released as a Windows-local private operator tool. Do not describe it as an official Codex client, mobile Codex replacement, hosted relay, or general multi-user service.
