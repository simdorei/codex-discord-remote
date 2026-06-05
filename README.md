# Codex Discord Frontend Harness

A Windows-local Discord frontend harness for operating a signed-in Codex app/web session remotely.

This is not a Codex CLI harness, not an official OpenAI client, and not a public hosted service. It is a private-operator wrapper for a Windows machine where Codex Desktop or the Codex web surface is already installed, signed in, awake, and trusted.

한국어 소개: Windows 로컬 Codex 앱/웹 세션을 Discord에서 안전하게 원격 운영하기 위한 Discord-only frontend harness입니다. 멘션 기반 요청, Discord thread별 Codex thread 라우팅, target thread queueing, approval/input 메뉴 미러링, structured not-sent handling, QA 로그, headless 실행 상태 표시를 제공하는 운영 래퍼입니다.

## Scope

- Windows-only local operator machine
- Discord-only frontend
- Codex app/web remains the execution surface
- Discord channels/threads map to local Codex threads
- Telegram adapter and Telegram launcher are intentionally excluded

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
     - target thread busy state
     - ask / queue / steer / retry policy
     - structured QA evidence
     - stale process and stale busy detection
  -> Codex app / Codex web surface
```

## Steering Policy

Steering is useful, but it must be explicit.

- Plain Discord asks default to target-thread delivery.
- If the target Codex thread is already busy, the Discord message is queued for that same target thread.
- Other Codex threads being busy should not block a mapped Discord thread.
- `Steer now` should appear only when the user is intentionally handling an existing busy-choice control.
- Codex Desktop `followUpQueueMode = "steer"` may be useful for operators who prefer app-side steering, but it should be a documented local choice, not an invisible side effect of the bridge.

## Quick Start

```powershell
py -3 -m pip install -r requirements.txt
copy .env.example .env
notepad .env
.\codex-discord-bot.cmd
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
- `DISCORD_STALE_BUSY_STEER_BLOCK_SECONDS`: stale busy threshold before additional steering is blocked
- `DISCORD_ASK_BUSY_RETRY_ATTEMPTS`: retry count after Codex app transport reports busy
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
- same target thread busy queues without creating `Steer now`
- different target threads can run independently
- duplicate bot starts keep one Discord websocket owner
- headless launch shows either the tray icon or clear runtime/log evidence

## Release Position

This repo should be released as a Windows-local private operator tool. Do not describe it as an official Codex client, mobile Codex replacement, hosted relay, or general multi-user service.
