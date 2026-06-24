## Fast Queue Map

Use this only when the scope is broad. Start with repo-level queue maps. This finds repos with open issues and/or PRs and gives counts.

PR queue, primary triage order:

```bash
repobar_cmd repos \
  --scope all \
  --only-with work \
  --owner steipete \
  --owner openclaw \
  --sort prs \
  --json
```

Issue pressure, second pass when issues matter:

```bash
repobar_cmd repos \
  --scope all \
  --only-with work \
  --owner steipete \
  --owner openclaw \
  --sort issues \
  --json
```

Use `--forks` and `--archived` only when the user says "all", "everything", or asks for archaeology. Default triage should omit forks and archived repos unless their queues are specifically relevant.

For a compact terminal view:

```bash
repobar_cmd repos --scope all --only-with work --owner steipete --owner openclaw --sort prs --plain
```

Useful `jq` summary:

```bash
repobar_cmd repos --scope all --only-with work --owner steipete --owner openclaw --sort prs --json |
  jq -r '.[] | [.fullName, .openIssues, .openPulls, .activityTitle, .activityActor] | @tsv'
```

When summarizing a PR-sorted queue, preserve RepoBar's PR-count order. Do not include a lower-PR repo while omitting a higher-PR repo from the same owner scope. Zero-issue repos with open PRs, for example `openclaw/crabbox`, are still triage-relevant.

## Detail Pass

After a broad queue map, inspect only the top repos unless the user explicitly wants exhaustive detail.

```bash
repobar_cmd issues <owner/name> --limit 50 --json
repobar_cmd pulls <owner/name> --limit 50 --json
repobar_cmd ci <owner/name> --limit 20 --json
repobar_cmd activity <owner/name> --limit 20 --json
```

For PRs that look mergeable or suspicious, switch to `gh` for maintainer-grade state:

```bash
gh pr view <n> --repo <owner/name> --json number,title,state,author,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup,updatedAt,url
gh pr diff <n> --repo <owner/name> --patch
gh run list --repo <owner/name> --branch <branch> --limit 10
```

For issues that may already be fixed, switch to `gh issue view`, then inspect current source before commenting or closing.

For OpenClaw/ClawdBot queues, use the OpenClaw maintainer pass when useful:

- search duplicates/related threads with `gitcrawl` if available;
- use the activity helper for opener/author identity;
- suppress top-maintainer noise unless the user asks for maintainer-owned work;
- prefer external/user-reported bugs and PRs with clear proof.

## Local Cross-Check

Use this when the task mentions local project state, dirty repos, or "what do I own here".

```bash
repobar_cmd local --root "$HOME/Projects" --depth 1 --limit 200 --plain
repobar_cmd local --root "$HOME/Projects" --depth 1 --sync --limit 200 --json
```

Do not run destructive local actions (`local reset`, branch deletes, checkout moves) unless the user explicitly asks.
