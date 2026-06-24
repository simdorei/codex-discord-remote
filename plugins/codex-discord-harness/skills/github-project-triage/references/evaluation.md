## Trust Signals

Include author/opener trust for every non-maintainer item you recommend acting on. For low-risk Dependabot/internal items, a terse bot/internal trust line is enough.

Prefer the bundled helper:

```bash
skills/github-project-triage/scripts/github-activity.sh --repo <owner/repo> --global <login>
```

Fallback if this skill checkout lacks the helper:

```bash
~/Projects/clawdbot/.agents/skills/openclaw-pr-maintainer/scripts/github-activity.sh --repo <owner/repo> --global <login>
```

Also use `github-author-context` when a PR needs deeper trust judgment, especially for OpenClaw, security-sensitive changes, broad PRs, new accounts, or unusual author behavior. Prefer existing contributor notes first:

```bash
~/Projects/maintainers/scripts/clawtributors find github <login>
```

Trust output must stay factual:

```text
Trust: @login; acct 2021-04-03; repo 2 PRs/1 issue/0 commits in 12mo; GitHub 9 PRs/3 issues/12 reviews; signal: known contributor / new drive-by / bot / unknown.
```

Do not treat trust as proof. It changes review depth, not correctness.

## Item Evaluation

Classify each item:

- `bug`: require repro/log/failing test/current-main proof when feasible; identify root cause before recommending fix/merge.
- `feature`: require end-to-end test plan. If live validation needs a provider key, account, device, service, model access, or paid API, say exactly what credential/access is missing before work can be considered complete.
- `dependency`: explain package group, major/minor risk, failing checks, runtime/engine changes, and whether to split.
- `security`: raise priority, require careful code-path proof, tests, and trust/context; do not merge on rationale alone.
- `docs/internal`: lower risk, but still explain user-visible relevance and stale/generated churn risk.

Judge:

- `Fit`: good / mixed / poor, with one reason.
- `Risk`: low / medium / high, with blast radius.
- `Proof`: current CI, local repro, failing test, live E2E, or missing proof.
- `Blocker`: first-time contributor CI approval, failing check, missing key, unclear product direction, stale branch, untrusted/broad diff, no repro, conflicts.
- `Next`: approve CI, run test, request repro, split PR, patch locally, merge after green, close with proof, or defer.
