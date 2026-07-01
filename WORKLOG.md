# Worklog

## 2026-06-05 Discord Remote Stabilization

Goal: keep this repository as a Discord-only Windows-local Codex frontend.

### Current State

- Repo path: `C:\repos\simdorei\codex-discord-remote`
- Runtime bot is running from this repo.
- Windows scheduled task `Codex Discord Bot` now points to this repo's watchdog.
- Runtime startup can also be handled by the user Startup shortcut.
- Local runtime files such as `.env`, logs, SQLite state, and lock files are ignored.

### Product Direction

- Discord-only frontend.
- Windows-local operator tool for a signed-in Codex app/web session.
- Not a Codex CLI frontend.
- Not an official mobile Codex replacement.

### Behavior Decisions

- Plain Discord asks go straight to the mapped Codex thread.
- Discord does not run global idle/busy preflight for ordinary asks.
- Discord does not auto-queue ordinary asks before sending.
- If the Codex app exposes approval/input/follow-up choices, Discord mirrors those choices.
- Other Codex threads being active should not block a mapped Discord thread.
- Existing explicit busy-choice controls are treated as legacy interactive controls and cleaned up when stale.

### Validation Completed

Run from `C:\repos\simdorei\codex-discord-remote`:

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

### QA Stamp: parallel-mapped-targets-2026.06.07-1

Status: confirmed for different mapped Codex target threads.

- Unit QA: `py -3 -m unittest tests.test_codex_discord_bot.DiscordBotHelperTests.test_run_prompt_flow_starts_distinct_target_threads_independently tests.test_codex_discord_bot.DiscordBotHelperTests.test_thread_runner_accepts_send_capable_channel tests.test_codex_discord_bot.DiscordBotHelperTests.test_run_prompt_flow_sends_directly_without_runner_queue` passed.
- Live log QA: at `2026-06-07 16:55:15`, target `019e9d4b-301f-77a0-a646-3fe155e4c26d` accepted a mapped Discord ask; at `2026-06-07 16:55:20`, target `019e8e79-5138-7ef3-8c24-7a917e3da18a` accepted a second mapped Discord ask before the first target completed.
- Completion order proved overlap: target `019e8e79-5138-7ef3-8c24-7a917e3da18a` completed at `16:55:40`; target `019e9d4b-301f-77a0-a646-3fe155e4c26d` completed later at `16:55:56`.
- Scope note: same-target consecutive asks are still serialized by `ask_delivery_wait`; this stamp only confirms cross-target parallel routing.
