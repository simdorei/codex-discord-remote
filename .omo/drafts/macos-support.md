---
slug: macos-support
status: drafting
intent: clear
pending-action: write .omo/plans/macos-support.md
approach: Keep Windows behavior intact, introduce explicit platform boundaries, then add a macOS headless path before optional native menu-bar parity.
---

# Draft: macos-support

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
P1 | Platform contract and OS detection for bridge/runtime code | active | README.md:9, codex_desktop_bridge_desktop_process.py:67, codex_discord_runtime_lock.py:44
P2 | macOS install/setup/launcher path beside existing Windows scripts | active | install.ps1:1, setup-discord-bot.ps1:1, codex-discord-bot.cmd:1
P3 | macOS Codex Desktop discovery/start/stop and state-root handling | active | codex_desktop_bridge_desktop_resolver.py:15, codex_desktop_bridge_desktop_process.py:67, codex_desktop_bridge_sidecar_resolver.py:71
P4 | macOS UI automation for focus, prompt delivery, approvals, and permission failure messages | active | codex_desktop_bridge_window_focus.py:42, codex_desktop_bridge_windows_native.py:1, codex_desktop_bridge_windows_input.py:1
P5 | macOS watchdog/headless lifecycle and host-command policy | active | codex-discord-watchdog.ps1:1, codex-discord-tray.ps1:1, codex_discord_prefix_host_commands.py:43
P6 | Documentation, CI-style checks, and real Mac manual QA | active | plugins/codex-discord-remote/scripts/qa-smoke.ps1:1, docs/policies-validation.md:54

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->
Initial macOS target | Headless launch + watchdog first, no native menu-bar/tray in the first usable release | It is the shortest path to a working Mac repo because Windows tray code is pure WinForms/PowerShell | yes
Automation mechanism | Use macOS native AppleScript/Accessibility through `osascript` first, not a new dependency | Native tools are already on macOS and keep install friction low | yes
Setup script direction | Move Discord token setup to a small Python CLI shared by Windows/macOS, with wrappers per OS | PowerShell-only setup blocks Mac; Python already exists as the runtime | yes
Windows support | Preserve existing Windows scripts and behavior during the port | The current users depend on it; Mac support must be additive | no

## Findings (cited - path:lines)
README currently states the repository is Windows-only and depends on PowerShell, Windows paths, and a signed-in Windows Codex Desktop session (`README.md:9`).
The installer and new token setup are PowerShell entry points (`install.ps1:1`, `setup-discord-bot.ps1:1`).
The visible/headless bot launchers are Windows batch/VBS/PowerShell (`codex-discord-bot.cmd:1`, `codex-discord-bot-headless.vbs:1`, `codex-discord-watchdog.ps1:1`, `codex-discord-tray.ps1:1`).
Codex Desktop discovery currently depends on Windows registry, AppX/WindowsApps, PowerShell, and `.exe` paths (`codex_desktop_bridge_desktop_resolver.py:15`, `codex_desktop_bridge_desktop_process.py:67`).
UI focus/input depends on Win32 `user32`, Windows UI Automation, clipboard APIs, and PowerShell helper scripts (`codex_desktop_bridge_window_focus.py:42`, `codex_desktop_bridge_windows_native.py:1`, `codex_desktop_bridge_windows_input.py:1`).
The runtime lock already gracefully bypasses Windows mutexes on non-Windows hosts, so part of the core bot runtime is portable (`codex_discord_runtime_lock.py:44`).
Host reboot command text and implementation are Windows-specific and must stay disabled unless a platform adapter explicitly supports the host (`codex_discord_prefix_host_commands.py:43`).
The QA smoke script is PowerShell and Windows-specific, so macOS needs its own smoke path instead of claiming the existing one proves Mac support (`plugins/codex-discord-remote/scripts/qa-smoke.ps1:1`).

## Decisions (with rationale)
Plan the work as an additive platform port, not a rename from Windows-only to cross-platform. This avoids breaking the known Windows remote while Mac is being introduced.
Make the first Mac milestone headless: install dependencies, configure token, launch bot, discover Codex Desktop, deliver one prompt, and mirror one response. Native tray/menu-bar parity comes after that.
Put platform-specific desktop control behind a narrow adapter boundary. The Discord bot core should not need to know whether the host is Windows or macOS.
Use native macOS tools first (`open`, `pgrep`, `osascript`, Accessibility permissions), because adding a heavy automation framework would raise the setup threshold.

## Scope IN
Create a plan for making the repo usable on macOS while keeping Windows support working.
Cover installer/setup, launcher/watchdog, desktop discovery, UI automation, host commands, docs, and QA.
Require real macOS manual QA before the README can claim Mac support.

## Scope OUT (Must NOT have)
Do not implement macOS support in this planning commit.
Do not remove or weaken Windows launchers, watchdog behavior, or QA.
Do not claim macOS support from Windows-only tests.
Do not introduce a hosted relay or cloud service.
Do not log or print Discord bot tokens.

## Open questions
None blocking for a first plan. Default: headless macOS first, native menu-bar later if the headless path proves useful.

## Approval gate
status: plan-written-from-explicit-user-request
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
