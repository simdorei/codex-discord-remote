## Bundled Skills And Attribution

The Codex Discord Remote plugin packages these skills. In Codex, the fully qualified
skill names use the `$codex-discord-harness:<skill-name>` form. In Discord,
some skills also have slash or `!` command wrappers.

| Skill | Purpose | Discord entrypoint |
| --- | --- | --- |
| `discord-harness` | Operational runbook for this local Discord bridge: bot status, watchdog restarts, archive-lock recovery, mirror routing checks, log triage, and local deployment checks. | No direct skill wrapper; use the normal remote commands such as `!status`, `!doctor`, `!mirror check`, and `!bridge sync`. |
| `discord-harness-qa` | Focused QA checklist for mirror mapping, context refresh, session mirror cursor priming, steering suppression, archive lock retry, and deployment readiness. | No direct skill wrapper; use it from Codex when validating remote changes. |
| `deep-interview` | Clarification-first requirements workflow. It confirms the work structure, asks one question at a time, scores ambiguity, preserves the user's language, tracks scope/entities/constraints, and stops at a pending-approval ticket before implementation. | `/interview <request>` or `!interview <request>`. |
| `github-project-triage` | GitHub queue triage workflow for issues, PRs, CI, blockers, risk, evidence, and next actions. It is for deciding what needs attention; it does not authorize implementation, merge, close, release, or destructive actions by itself. | `/github_triage [prompt]` or `!triage [request]`. |
| `maintainer-orchestrator` | Maintainer workflow for organizing decision-ready PR work, worker/queue monitoring, release readiness, and cross-repository follow-up. In this remote it is a skill prompt wrapper, not an automatic multi-agent runtime; any mutation, delegation, push, merge, close, or release still requires explicit user authorization. | `/maintainer_orchestrator <prompt>` or `!orchestrate <request>`. |

Source attribution is included for transparency and license compliance. These
upstream authors are not listed as this repository's contributors unless they
contributed directly here; the notes below identify inspiration, adaptation, or
vendored source material only.

- `deep-interview` is adapted from the Gajae Code deep-interview skill:
  https://github.com/Yeachan-Heo/gajae-code/tree/main/packages/coding-agent/src/defaults/gjc/skills/deep-interview
- Gajae Code is MIT licensed. The packaged notice is at
  `plugins/codex-discord-harness/skills/deep-interview/NOTICE.md`.
- `github-project-triage` is vendored from `steipete/agent-scripts`:
  https://github.com/steipete/agent-scripts/blob/main/skills/github-project-triage/SKILL.md
- `maintainer-orchestrator` is vendored from `steipete/agent-scripts`:
  https://github.com/steipete/agent-scripts/blob/main/skills/maintainer-orchestrator/SKILL.md
- `steipete/agent-scripts` is MIT licensed. The packaged notices are at
  `plugins/codex-discord-harness/skills/github-project-triage/NOTICE.md` and
  `plugins/codex-discord-harness/skills/maintainer-orchestrator/NOTICE.md`.
- Repository-level third-party notices are collected in `NOTICE.md`.

Useful plugin-backed scripts can also be run directly from the repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-harness\scripts\status.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-harness\scripts\restart.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-harness\scripts\qa-smoke.ps1 -SkipUnitTests
```

`restart.ps1` queues a deferred restart by default. The watchdog waits until every DB-root
Codex thread is `idle` and no listed thread has recent activity before stopping the bot, so
the current Discord-mirrored turn can finish. Use `-DryRun` to check readiness or `-Immediate`
when you intentionally want the script to wait in the foreground.
