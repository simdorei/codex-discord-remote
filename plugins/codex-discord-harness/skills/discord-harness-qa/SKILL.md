---
name: discord-harness-qa
description: Run focused QA for the Codex Discord Harness, especially mirror mapping, context refresh, session mirror cursor priming, steering suppression, archive lock retry, and deployment readiness checks.
---

# Codex Discord Harness QA

Use this skill when validating local changes or deciding whether the harness is ready to push or deploy.

## Standard QA

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File plugins/codex-discord-harness/scripts/qa-smoke.ps1
```

The smoke script covers:

- `git diff --check`
- installer dry-run without dependency or `.env` changes
- Python compile checks for core harness modules
- the main Discord bot and mirror cleanup test suites

## Runtime QA Notes

- A passing unit suite is not enough for live Discord routing claims.
- For live QA, verify bot logs show actual gateway receipt and final send lines.
- If the target thread is archive-recommended, session mirror delegation should be disabled by design.
- Cursor priming should advance to the current session file EOF before delegated mirror output.
- Old ask output after a steering handoff should be suppressed when a newer steering relay already sent the final answer.

