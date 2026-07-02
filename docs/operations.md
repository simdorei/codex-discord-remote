## Send Attachments Manually

For one-off Codex-to-Discord artifact delivery, use the UTF-8 helper instead of piping Korean text through PowerShell:

```powershell
py -3 .\send_discord_attachment.py --channel-id 123456789012345678 --content-file .\caption.txt .\image.png
```

For longer Korean captions, put the content in a UTF-8 text file:

```powershell
py -3 .\send_discord_attachment.py --channel-id 123456789012345678 --content "작업 결과입니다." .\result.txt
```

## Daily Workflow

1. Start Codex Desktop and sign in.
2. Start the Discord remote.
3. Use `/bridge_sync` to refresh Discord mirror state.
4. Send messages in a mapped Discord thread to operate the matching Codex thread.
5. When Codex asks for approval/input/steering, answer from the Discord controls.

Use this repo when you want to operate Codex Desktop or the Codex app from Discord on your own Windows machine.

Registered Discord slash commands:

- /help, /list, /archived_list, /use, /status, /settings, /doctor, /where, /context, /usage, /runners, /retract, /mirror_check, /bridge_sync, /new, /ask, /interview (legacy alias: /ask_ipc)

Common `!` commands:

| Command | Effect |
| --- | --- |
| `!help` | Shows the command list. |
| `!list [limit]` | Lists Codex threads. Without a limit, it uses DB-root user threads; with a limit, it uses the recent thread list. |
| `!archived_list [limit]` | Lists archived Codex threads. Alias: `!archive_list`. |
| `!use <ref>` | Selects a Codex thread by id, workspace ref, or list-style ref. |
| `!open <ref>` | Opens the target Codex thread. |
| `!open_abort <ref>` | Opens the target Codex thread and aborts the active turn. |
| `!status [ref]` | Shows status for the mapped/current thread or a supplied ref. |
| `!settings [ref] --model <model> --effort <effort> --speed <speed>` | Updates the mapped/current thread or supplied ref. Alias: `!setting`. Omit a value, for example `!setting --model`, to list app-provided options. Use `--speed standard` when not using fast. |
| `!doctor` | Runs Discord and local bridge diagnostics. |
| `!discover_codex` | Shows the detected Codex Desktop path. |
| `!restart_codex` | Restarts Codex Desktop through the local bridge. |
| `!reset_pc confirm` | Requests a Windows host reboot from the Discord bot process. Disabled unless `DISCORD_ENABLE_HOST_COMMANDS=1` and `DISCORD_ALLOWED_USER_IDS` is a narrow allowlist of trusted Discord user IDs. |
| `!chatid` | Prints Discord guild/channel/user ids for configuration. |
| `!where` | Shows the Codex thread mapped to the current Discord channel. |
| `!context [all]` | Shows context usage for the current thread, or mapped threads with `all`. |
| `!usage [days]` | Shows local Codex usage estimates. |
| `!runners` | Shows Discord runner queues. |
| `!resources` | Shows local CPU, RAM, and disk free space. Alias: `!system`. |
| `!retract [ref]` | Removes your latest queued ask for the mapped/current Codex thread or supplied ref. Active asks are not interrupted. |
| `!bridge sync [limit]` | Refreshes local bridge state and Discord mirror state. Without a limit, it uses DB-root user threads. |
| `!mirror sync` | Syncs Discord mirror project/thread channels using DB-root user threads. |
| `!mirror list [limit]` | Lists mirror mappings. Without a limit, it uses DB-root user threads. |
| `!mirror check [limit]` | Checks mirror mappings and stale rows. Without a limit, it uses DB-root user threads. |
| `!approval` | Re-sends the pending approval controls for the mapped/current Codex thread when one exists. |
| `!archive [ref]` | Archives the mapped/current Codex thread or a supplied ref. Numeric refs follow the same DB-root numbering as `!list`. |
| `!archive-used <threshold>` | Runs the packaged `archive-used` skill for high-`used` cleanup using the threshold you provide. |
| `!delete_archive <ref>` | Previews deletion of an archived Codex thread. |
| `!confirm_delete_archive <ref>` | Permanently deletes the archived thread after previewing. |
| `!new <prompt>` | Creates a new Codex thread with the first prompt. |
| `!interview <request>` | Sends the request as a Gajae-style deep interview so Codex confirms work structure, scores ambiguity, and waits for approval before implementation. |
## Interop With Other Discord Tools

This remote is intentionally Discord-native, so it can share a Discord thread with other bots or command-line agents that post through Discord.

Useful patterns:

- Use another Discord CLI/bot to run project-specific automation, then mention the Codex bridge to review the result from the local Codex thread.
- Keep a project Discord thread as the shared command surface for humans, Codex, and specialized bots.
- Let other bots post status or artifacts while Codex handles local code edits, approvals, and steering.

Safety behavior:

- Other bot messages are ignored by default.
- Other bot messages are accepted only when they explicitly mention the Codex bridge user.
- Restart-check handoff packets are accepted without the bridge mention only when they are `ACTION: RESTART-CHECK / HANDOFF` packets and include an explicit `codex/session` id.
- The bridge ignores its own messages to avoid loops.
- `DISCORD_ALLOWED_USER_IDS` contains Discord user IDs, not channel or server IDs. Host reboot commands require both `DISCORD_ENABLE_HOST_COMMANDS=1` and a narrow `DISCORD_ALLOWED_USER_IDS` allowlist.

Explicit routing:

- A `codex/session: <thread-id>` line overrides the mapped or selected thread for handoff packets.
- Exact Codex thread ids are resolved against the full local active thread database, not only the recent list, so older but still unarchived threads can be targeted safely.
- Mirror sync and mirror list commands still use the narrower mirror scope; do not use them as a cleanup mechanism for all local Codex history.
