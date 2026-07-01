# macos-support - Work Plan

## TL;DR (For humans)
**What you'll get:** A repo that can run the Discord remote on macOS as well as Windows. The first Mac target is practical headless use: install, configure the bot token, launch the bot, connect to a signed-in Codex Desktop app, send one Discord prompt, and mirror the result.

**Why this approach:** The current project is deeply Windows-shaped, so the low-risk path is to keep Windows working and add a small platform layer. Mac support should prove the core remote works before adding native menu-bar/tray polish.

**What it will NOT do:** It will not remove the Windows launchers, replace the local-machine model with a hosted service, or claim Mac support until a real Mac run has passed.

**Effort:** Large
**Risk:** High - Codex Desktop UI automation differs sharply between Windows UI Automation and macOS Accessibility/AppleScript.
**Decisions to sanity-check:** Headless macOS first; native menu-bar later. Use macOS native tools before adding automation dependencies.

Your next move: approve this plan and run it with `$start-work`, or ask for the native menu-bar to be included in the first Mac release. Full execution detail follows below.

---

> TL;DR (machine): Large/high-risk additive macOS port; keep Windows stable, add platform adapters, macOS setup/launcher/watchdog, native macOS desktop automation, docs, and real Mac QA.

## Scope
### Must have
- A clear platform boundary so Discord bot core code does not call Windows APIs directly.
- macOS setup flow that can install dependencies, configure `.env`, and save the Discord bot token without PowerShell.
- macOS Codex Desktop discovery/start/stop support for a signed-in local app.
- macOS prompt delivery and response/approval mirroring through the existing bridge surfaces.
- macOS headless launcher and watchdog equivalent to the current practical Windows headless path.
- Documentation that honestly distinguishes Windows support, macOS support, and any remaining Windows-only features.
- Agent-executed unit/smoke checks plus real manual QA on a Mac before the README says macOS is supported.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Must not break the existing Windows `.ps1`, `.cmd`, or `.vbs` launch paths.
- Must not claim macOS support from Windows-only tests or simulated OS checks alone.
- Must not introduce a hosted relay, cloud dependency, or npm package distribution.
- Must not print, log, or commit Discord bot tokens.
- Must not make native menu-bar/tray parity block the first usable macOS release.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after for refactors, TDD for new platform adapter behavior where a failing unit can be written first.
- Unit evidence: `.omo/evidence/task-specific-macos-support.txt` for each todo command output.
- Smoke evidence: Windows smoke remains `plugins/codex-discord-remote/scripts/qa-smoke.ps1`; macOS gets a new shell/Python smoke that runs on a real Mac.
- Manual QA gate: real macOS host with Codex Desktop installed and signed in; run install/setup, start the bot, send one Discord message into an allowed channel/thread, observe Codex app prompt delivery, and observe mirrored final response.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- Wave 1: inventory and adapter boundaries, shared setup CLI, docs skeleton.
- Wave 2: macOS desktop discovery/process lifecycle, macOS UI automation, macOS launcher/watchdog.
- Wave 3: platform-specific QA scripts, documentation finalization, real Mac manual QA.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2, 3, 4, 5, 6 | none |
| 2 | 1 | 7, 8 | 3, 4 |
| 3 | 1 | 4, 5, 7 | 2, 6 |
| 4 | 1, 3 | 7, 8 | 5, 6 |
| 5 | 1, 3 | 7, 8 | 4, 6 |
| 6 | 1 | 7, 8 | 2, 3, 4, 5 |
| 7 | 2, 3, 4, 5, 6 | 8 | none |
| 8 | 7 | final verification | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 1. Define the platform boundary and inventory every Windows-only call site.
  What to do / Must NOT do: Add a small documented platform contract for desktop discovery, app start/stop, focus/input, runtime locking, host commands, and launcher/watchdog behavior. Do not move behavior yet unless the move is mechanical and covered by existing tests.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 2, 3, 4, 5, 6
  References (executor has NO interview context - be exhaustive): `README.md:9`, `codex_desktop_bridge_desktop_resolver.py:15`, `codex_desktop_bridge_desktop_process.py:67`, `codex_desktop_bridge_window_focus.py:42`, `codex_desktop_bridge_windows_native.py:1`, `codex_desktop_bridge_windows_input.py:1`, `codex_discord_runtime_lock.py:44`, `codex_discord_prefix_host_commands.py:43`, `codex-discord-bot.cmd:1`, `codex-discord-watchdog.ps1:1`, `codex-discord-tray.ps1:1`
  Acceptance criteria (agent-executable): `py -3 -m py_compile` on every touched Python module exits 0, and a grep report in `.omo/evidence/task-1-macos-support.txt` lists remaining Windows-only files with each classified as core, adapter, launcher, or deferred.
  QA scenarios (name the exact tool + invocation): happy: run the existing Windows smoke with `powershell -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-remote\scripts\qa-smoke.ps1 -SkipUnitTests`; failure: run an OS-simulated unit test that verifies non-Windows paths raise clear platform-not-supported errors instead of importing Win32 modules.
  Commit: Y | Refactor platform boundaries for macOS support

- [ ] 2. Replace PowerShell-only token setup with a shared Python setup command and thin OS wrappers.
  What to do / Must NOT do: Move Discord token validation, `.env` write, and invite-link generation into a Python command usable from Windows and macOS. Keep `setup-discord-bot.ps1` as a Windows wrapper and add a macOS shell wrapper. Do not accept tokens as command-line arguments.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `setup-discord-bot.ps1:1`, `.env.example:1`, `README.md:10`, `codex_discord_runtime_config.py:53`, `send_discord_attachment.py:64`
  Acceptance criteria (agent-executable): dry-run setup command prints an invite URL and does not change `.env`; real-mode code path has unit tests using mocked Discord HTTP; token never appears in captured stdout/stderr.
  QA scenarios (name the exact tool + invocation): happy: `py -3 <new setup module> --dry-run`; failure: mocked invalid token returns the Discord error message and leaves `.env` unchanged.
  Commit: Y | Share Discord bot setup across platforms

- [ ] 3. Add macOS Codex Desktop discovery and process lifecycle adapter.
  What to do / Must NOT do: Teach discovery to resolve Codex Desktop on macOS through `/Applications`, user Applications, running process inspection, and an explicit `CODEX_DESKTOP_EXE`/path override. Add start/stop behavior using `open -a`/process APIs where safe. Do not reuse Windows registry, AppX, `taskkill`, or `.exe` assumptions on macOS.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 4, 5, 7
  References (executor has NO interview context - be exhaustive): `codex_desktop_bridge_desktop_resolver.py:15`, `codex_desktop_bridge_desktop_process.py:67`, `codex_desktop_bridge_desktop_commands.py:1`, `tests/test_codex_desktop_bridge_desktop_process.py:72`, `tests/test_codex_desktop_bridge_desktop_commands.py:23`
  Acceptance criteria (agent-executable): unit tests cover Windows discovery unchanged, macOS discovery candidates, explicit override, not-found error text, and start/stop commands without running real Codex.
  QA scenarios (name the exact tool + invocation): happy: mocked macOS discovery returns `/Applications/Codex.app`; failure: no candidate produces an actionable error telling the user how to set the override.
  Commit: Y | Add macOS Codex Desktop lifecycle adapter

- [ ] 4. Add macOS UI automation for prompt delivery, focus, and approval/input handling.
  What to do / Must NOT do: Implement a macOS adapter using native Accessibility/AppleScript first. It must detect missing Accessibility permission and print a clear fix path. Do not import Windows `ctypes.windll`, Win32 clipboard, or PowerShell UI Automation modules outside Windows adapters.
  Parallelization: Wave 2 | Blocked by: 1, 3 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `codex_desktop_bridge_window_focus.py:42`, `codex_desktop_bridge_windows_native.py:1`, `codex_desktop_bridge_windows_input.py:1`, `codex_desktop_bridge_permission_approval.ps1:1`, `codex_desktop_bridge_prompt_delivery.py:1`, `tests/test_codex_desktop_bridge_window_focus.py:70`, `tests/test_codex_desktop_bridge_windows_input.py:10`
  Acceptance criteria (agent-executable): imports succeed on non-Windows test simulation; macOS adapter unit tests verify command construction and missing-permission error mapping; Windows UI tests still pass on Windows.
  QA scenarios (name the exact tool + invocation): happy: on a real Mac, focus the Codex composer and submit a test prompt from Discord; failure: remove Accessibility permission or run without it and verify the bot reports the exact permission remediation.
  Commit: Y | Add macOS desktop automation adapter

- [ ] 5. Add macOS launcher and watchdog path without blocking the first release on menu-bar parity.
  What to do / Must NOT do: Add a macOS shell launcher and a headless watchdog or launchd-friendly script that starts the Python bot, writes logs, uses the existing runtime lock where portable, and can restart after a marker. Do not build a menu-bar app in the first pass.
  Parallelization: Wave 2 | Blocked by: 1, 3 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `codex-discord-bot.cmd:1`, `codex-discord-bot-headless.vbs:1`, `codex-discord-watchdog.ps1:1`, `codex-discord-watchdog-runtime.ps1:1`, `codex-discord-tray.ps1:1`, `codex_discord_runtime_lock.py:44`, `tests/test_codex_restart_scripts.py:49`
  Acceptance criteria (agent-executable): macOS launcher dry-run verifies Python selection, log path, lock path, and restart marker behavior; Windows launcher tests remain unchanged.
  QA scenarios (name the exact tool + invocation): happy: on a Mac, start the bot headlessly, confirm a log line and process lock, request restart, and see the process recover; failure: missing Python prints one actionable error and exits non-zero.
  Commit: Y | Add macOS headless launcher and watchdog

- [ ] 6. Make host-level commands platform-aware and conservative.
  What to do / Must NOT do: Keep host commands disabled by default. Route reboot/reset behavior through a platform adapter with Windows as existing behavior and macOS either explicitly implemented with a safe confirmation path or explicitly unsupported with a clear message. Do not silently run `shutdown` on macOS.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `codex_discord_prefix_host_commands.py:13`, `tests/test_codex_discord_prefix_host_commands.py:102`, `.env.example:8`, `README.md:166`
  Acceptance criteria (agent-executable): tests prove disabled-by-default behavior, allowlist requirement, Windows command unchanged, and macOS unsupported/supported branch text.
  QA scenarios (name the exact tool + invocation): happy: unit test verifies `!reset_pc confirm` refuses without allowlist; failure: macOS branch without explicit support returns clear unsupported text and does not spawn a process.
  Commit: Y | Guard host commands by platform

- [ ] 7. Add cross-platform QA entry points and keep Windows smoke intact.
  What to do / Must NOT do: Add a macOS smoke script that runs syntax checks, setup dry-run, platform adapter tests, and launcher dry-run. Keep the existing PowerShell smoke as the Windows gate. Do not make one OS pretend to certify the other.
  Parallelization: Wave 3 | Blocked by: 2, 3, 4, 5, 6 | Blocks: 8
  References (executor has NO interview context - be exhaustive): `plugins/codex-discord-remote/scripts/qa-smoke.ps1:1`, `docs/policies-validation.md:25`, `tests/test_codex_desktop_bridge_desktop_process.py:72`, `tests/test_codex_discord_runtime_lock_integration.py:43`
  Acceptance criteria (agent-executable): Windows smoke passes on Windows; macOS smoke passes on macOS; both scripts fail loudly when run on the wrong OS unless in dry-run mode.
  QA scenarios (name the exact tool + invocation): happy: run Windows smoke on Windows and macOS smoke on Mac; failure: run macOS smoke with a missing Codex app and verify it reports setup remediation instead of passing.
  Commit: Y | Add macOS smoke QA

- [ ] 8. Update docs and run real end-to-end Mac QA before changing support claims.
  What to do / Must NOT do: Update README, operations docs, and policy docs to explain Windows and macOS setup separately. Only remove "Windows-only" language after a real Mac end-to-end pass is recorded. Do not hide unsupported features; list them plainly.
  Parallelization: Wave 3 | Blocked by: 7 | Blocks: final verification
  References (executor has NO interview context - be exhaustive): `README.md:9`, `README.md:35`, `README.md:187`, `docs/operations.md:23`, `docs/policies-validation.md:54`, `WORKLOG.md:5`
  Acceptance criteria (agent-executable): docs include Mac install/setup/run/QA steps, Windows steps still present, support matrix lists any Windows-only features, and `.omo/evidence/task-8-macos-support.txt` contains the real Mac end-to-end QA transcript summary.
  QA scenarios (name the exact tool + invocation): happy: from a clean Mac checkout, follow docs to launch bot and send one Discord prompt to Codex Desktop; failure: docs path for missing Accessibility permission is exercised and matches the bot error text.
  Commit: Y | Document verified macOS support

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
- Keep setup/token onboarding changes in their own commit before macOS implementation work.
- For macOS support, use one commit per todo unless two adjacent todos are inseparable after implementation.
- Do not mix Windows behavior fixes with macOS support unless the fix is required to preserve Windows parity.
- Final docs/support-claim commit must include evidence from real Mac QA.

## Success criteria
- Windows install/setup/watchdog smoke still passes.
- macOS setup can be completed without PowerShell.
- macOS bot can start, connect to Discord, send a prompt into local Codex Desktop, and mirror a final response.
- Missing macOS permissions produce actionable errors instead of silent fallback behavior.
- README no longer overclaims: support matrix matches verified behavior.
