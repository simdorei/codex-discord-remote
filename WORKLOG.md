# Worklog

## 2026-06-05 Discord Harness Stabilization

Goal: keep this repository as a Discord-only Windows-local Codex frontend harness.

### Current State

- Repo path: `C:\repos\simdorei\codex-discord-harness`
- Runtime bot is running from this repo.
- Windows scheduled task `Codex Discord Bot` now points to this repo's watchdog.
- Runtime startup can also be handled by the user Startup shortcut.
- Local runtime files such as `.env`, logs, SQLite state, and lock files are ignored.

### Product Direction

- Discord-only frontend harness.
- Windows-local operator tool for a signed-in Codex app/web session.
- Not a Codex CLI harness.
- Not an official mobile Codex replacement.

### Behavior Decisions

- Plain Discord asks go straight to the mapped Codex thread.
- Discord does not run global idle/busy preflight for ordinary asks.
- Discord does not auto-queue ordinary asks before sending.
- If the Codex app exposes approval/input/follow-up choices, Discord mirrors those choices.
- Other Codex threads being active should not block a mapped Discord thread.
- Existing explicit busy-choice controls are treated as legacy interactive controls and cleaned up when stale.

### Validation Completed

Run from `C:\repos\simdorei\codex-discord-harness`:

```powershell
py -3 -m unittest tests.test_codex_discord_bot
py -3 -m py_compile codex_desktop_bridge.py codex_windows_harness.py codex_discord_bot.py tests\test_codex_discord_bot.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-watchdog.ps1 -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-tray.ps1 -Once
git diff --check
```

Observed latest results:

- `179 tests OK`
- `py_compile OK`
- watchdog dry-run: `running`
- tray once: `running pid=11084`
- scheduled task manual run: `Last Result=0`
- `git diff --check OK`

### Live QA Checklist

- Send an ordinary ask in a mapped Discord thread: should submit without local steering prompt.
- Send into two different mapped Discord threads close together: each should target its own Codex thread.
- Confirm no duplicate bot replies after a newer relay supersedes an older relay.
- Confirm no second bot process owns the Discord websocket.
- Confirm tray icon or `codex-discord-tray.ps1 -Once` reports running.
