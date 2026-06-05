# Worklog

## 2026-06-05 Discord-Only Split

Goal: split the legacy mixed Telegram/Discord bridge into a Discord-only Windows-local Codex frontend harness.

### Current State

- New repo path: `C:\repos\simdorei\codex-discord-harness`
- Initial commit: `3f9b3d7 Initial Discord-only Codex harness`
- Runtime bot is currently running from the new repo.
- Runtime PID observed: `17404`
- Tray check observed: `running pid=17404`
- Legacy repo watchdog is disabled with `C:\repos\simdorei\codex-telegram\.codex_discord_bot.disabled`.
- Legacy bot duplicate process was stopped.

### Product Direction

- Discord-only frontend harness.
- Windows-local operator tool for a signed-in Codex app/web session.
- Not a Codex CLI harness.
- Not an official mobile Codex replacement.
- Telegram adapter and Telegram launcher are intentionally excluded from the new repo.

### Behavior Decisions

- Plain Discord asks route to the mapped target Codex thread.
- If the same target Codex thread is busy, the Discord message is queued for that target thread.
- Other Codex threads being busy should not block a mapped Discord thread.
- `Steer now` should be an explicit Discord control, not an automatic fallback for every busy condition.
- App-side steering mode can be useful, but should be documented as an operator choice rather than hidden config drift.

### Fixes/Changes Already Done

- Created `C:\repos\simdorei\codex-discord-harness`.
- Copied only Discord/harness/desktop bridge files, Discord launchers, watchdog, tray script, requirements, and tests.
- Added Discord-only `README.md`.
- Added Discord-only `.env.example`.
- Added `.gitignore`.
- Removed Telegram wording from new repo user-facing bot docstring.
- Removed Telegram wording from a bridge decline-message error.
- Removed risky default context keyword `chat`, because it matched `chatter`.
- Added Korean operational context fallback coverage for messages such as `디코 봇 응답 없어`.
- Adjusted slash ask expectation: target-busy slash ask queues without creating a `Steer now` view.
- Added/kept tray script for headless bot visibility.
- Added stop/disabled marker support to watchdog scripts.
- Restored Codex Desktop config by removing `[desktop] followUpQueueMode = "steer"` from `C:\Users\banpo\.codex\config.toml`.

### Validation Completed

Run from `C:\repos\simdorei\codex-discord-harness`:

```powershell
py -3 -m unittest tests.test_codex_discord_bot
py -3 -m py_compile codex_desktop_bridge.py codex_windows_harness.py codex_discord_bot.py tests\test_codex_discord_bot.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-watchdog.ps1 -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-tray.ps1 -Once
git diff --check
```

Observed results:

- `176 tests OK`
- `py_compile OK`
- watchdog dry-run: `running`
- tray once: `running pid=17404`
- `git diff --check OK`

### Remaining Blocker

The existing Windows scheduled task named `Codex Discord Bot` still points to the legacy repo:

```text
C:\repos\simdorei\codex-telegram\codex-discord-watchdog.ps1
```

Updating the task action to the new repo failed from the current shell with `Access is denied`.

Current mitigation:

- Old repo watchdog has `.codex_discord_bot.disabled`, so the old scheduled task does not restart the old bot.
- New repo bot is already running headless.

Needed admin action:

Update the scheduled task action to:

```text
powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "C:\repos\simdorei\codex-discord-harness\codex-discord-watchdog.ps1"
```

Working directory:

```text
C:\repos\simdorei\codex-discord-harness
```

### Next Live QA

Use Discord with the new bot:

- Send unmentioned chatter in a gated channel: should be ignored.
- Mention configured bot/user for a plain ask: should strip mention and submit.
- Send Korean operational text with context fallback enabled: should be accepted only when it matches bridge/Codex context.
- Send two different mapped Discord threads close together: should not global-block each other.
- Send while the same mapped target thread is busy: should queue without showing `Steer now`.
- Confirm no duplicate bot replies.
- Confirm no second bot process owns the Discord websocket.
- Confirm tray icon or `codex-discord-tray.ps1 -Once` reports running.
