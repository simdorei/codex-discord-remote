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
- App-server approval and request-user-input prompts are cached as server requests. Discord approval/input replies answer the same app-server JSON-RPC request id; missing resident requests are surfaced as explicit errors instead of silently substituting another path.
- Mapped ask output is mirrored from the local Codex session file after cursor priming, so Discord does not also stream a second copy of the answer.
- Background session mirroring still tails archive-recommended threads. Backlog is not dropped; it is caught up in bounded event batches so a stale cursor does not flood Discord in one poll. Active mapped Discord asks can still temporarily allow mirrored output for that target.
- Legacy IPC/UI/subprocess delivery is not used as a silent fallback for ask/steer. Resident app-server delivery failures are surfaced as explicit errors.
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

This repository is a Windows-local Codex Discord remote. It depends on Windows Codex Desktop, PowerShell scripts, and local user-session state. It is useful for personal or team-operated Windows machines, but it is not a hosted multi-user service, Linux service, or mobile Codex replacement.

## Acknowledgements

The resident app-server transport direction was informed by [NathanZane/codex-mobile](https://github.com/NathanZane/codex-mobile), which explores Discord-based Codex Desktop mirroring, approvals, queueing, and steering workflows.
