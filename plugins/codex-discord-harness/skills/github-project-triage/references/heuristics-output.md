## Triage Heuristics

Prioritize:

- PRs with green or nearly-green CI, recent maintainer activity, or low-risk dependency/docs/test changes.
- Repos with high open PR counts but recent activity, because they often hide obvious cleanup.
- Issues that are reproducible, recently reported, or block releases.
- Security, release, auth, install, CI, and data-loss reports before cosmetic items.
- Bugs with clear current-main reproduction and narrow owner path.
- Features only when live validation is possible or the missing access is explicit.

Deprioritize:

- Archived repos unless the user asked for them.
- Fork-only queues unless the fork is actively maintained by Peter.
- Old broad feature requests with no reproduction or owner signal.
- Repos with missing/removable remotes until local state is clarified.
- Feature/provider PRs that need unavailable API keys or accounts for end-to-end proof.
- Broad generated changes without a clear user problem, test plan, or trusted author signal.

## Output Shape

For current-project triage, answer with:

```text
Repo: owner/name
Source: gh list/view/diff/checks, local source/tests where inspected

Immediate:
- #123 PR: title
  What: one-line summary in plain words.
  Type/Fit/Risk: bug|feature|dependency; good|mixed|poor; low|medium|high because ...
  Trust: @login; acct date; repo/global activity; known/unknown/bot.
  Proof: CI/repro/test/e2e state.
  Blocker: none / missing key / first-time CI approval / failing lint / unclear direction.
  Next: exact maintainer action.

Needs judgment:
- #124 issue: ...

Defer/close:
- #125 issue: ...

Skipped:
- <why>
```

For a broad scan, answer with:

```text
Owners scanned: steipete, openclaw
Source: RepoBar <command summary>, plus gh for selected PRs/issues

Top queues:
- owner/repo: X issues, Y PRs; why it matters; next action

Immediate actions:
- <small obvious merge/fix/comment/rerun, with item summary>

Needs judgment:
- <larger/ambiguous queues, with item summary>

Skipped:
- archived/forks/missing access/etc.
```

When the user asks to act, keep going: inspect the selected PRs/issues with `gh`, rerun/fix CI, comment/close/merge only with evidence, and report exact commands/proof.
