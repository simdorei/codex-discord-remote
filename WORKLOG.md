# Worklog

## 2026-06-06 Refactor Slices And Live QA

Goal: refactor the Discord harness in small slices, with local tests and Discord web live QA after each slice.

### Completed Slices

- Slice 1: moved ask stream delivery classification and log formatting into `codex_discord_delivery.py`.
- Slice 2: introduced `ThreadAskJob` in `codex_discord_runner.py` so per-target runner queues use an explicit job contract.
- Slice 3: moved prompt-flow dispatch into the runner contract while keeping bot-level compatibility wrappers.
- Slice 4: moved session mirror item collection, echo suppression, and mirror text formatting into `codex_discord_session_mirror.py`.
- Slice 5: introduced `RuntimeState` in `codex_discord_runtime.py` for per-target steering handoff and relay generation state.
- Slice 6: added dependency injection seams to `handle_plain_ask` and converted representative tests away from global monkey patching.

### Validation Completed

Run from `C:\repos\simdorei\codex-discord-harness`:

```powershell
py -3 -m unittest tests.test_codex_discord_bot
py -3 -m py_compile codex_desktop_bridge.py codex_windows_harness.py codex_discord_bot.py codex_discord_delivery.py codex_discord_prompt_guard.py codex_discord_runner.py codex_discord_runtime.py codex_discord_session_mirror.py tests\test_codex_discord_bot.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-watchdog.ps1 -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipDependencies -SkipEnvFile -DryRun
git diff --check
```

Observed latest results:

- `218 tests OK`
- `py_compile OK`
- watchdog dry-run: `running`
- install dry-run: would configure Codex Desktop `followUpQueueMode = "steer"`
- Discord web QA replies received: `SLICE1_LOG_OK`, `SLICE2_JOB_OK`, `SLICE3_FLOW_OK`, `SLICE5_RUNTIME_OK`, `SLICE6_DI_OK`
- Session mirror web QA confirmed current Codex app progress messages appearing in the mapped Discord mirror channel after Slice 4.

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

- `193 tests OK`
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
