---
name: "github-project-triage"
description: "Use whenever the user types triage or asks to triage GitHub issues, PRs, queues, CI, blockers, risk, proof, or next actions."
---

# GitHub Project Triage

Always use this skill when the user types `triage`, unless the request explicitly targets a non-GitHub domain. From inside a repo, use the current GitHub project by default. Triage means maintainer-facing item cards: URL, what each issue/PR is about, why it matters, author trust, fit, risk, proof/test state, blockers, and next action. Never return only queue numbers or opaque refs.

Output is URL-first: every surfaced issue/PR/repo item must include its GitHub URL in the first line or first sentence for that item. If giving a shortlist, print one URL per item.

Use RepoBar as the first pass only for broad queue discovery across relevant owners/orgs. RepoBar is faster and more profile-aware than hand-rolling `gh repo list` loops, and it already understands repo activity, issue counts, PR counts, local projects, auth, cache, and filters.

## Setup

Prefer a real `repobar` binary when installed. In this workspace it may only exist as a SwiftPM product in `~/Projects/RepoBar`.

```bash
repobar_cmd() {
  if command -v repobar >/dev/null 2>&1; then
    repobar "$@"
  elif [ -x "$HOME/Projects/RepoBar/.build/debug/repobarcli" ]; then
    "$HOME/Projects/RepoBar/.build/debug/repobarcli" "$@"
  else
    swift run --package-path "$HOME/Projects/RepoBar" repobarcli "$@"
  fi
}

repobar_cmd status --json
```

Default owners for broad triage: `steipete`, `openclaw`. For broad/default queue triage, include a detail pass for `openclaw/openclaw` even if it is not the first repo by count, because it is the main ClawdBot/OpenClaw queue. Do not include `amantus-ai` or other owners unless the user names them, the current repo is already under that owner, or the task explicitly asks for all/everything. For an exact owner-specific task, do not broaden beyond the named owner.

## Local Repo Gate

Before starting work inside any local project, verify the checkout is ready:

```bash
git status --short --branch
git branch --show-current
git pull --ff-only
git status --short --branch
```

Proceed only when the branch is `main`, the pull succeeds, and the worktree is clean. If the branch is not `main`, the pull fails, or `git status --short` shows changes, stop and ask Peter what to do. Do not switch branches, stash, commit, reset, restore, or clean without explicit direction.

## Scope Rule

If the user says `triage` and the current working directory is a Git repo with a GitHub remote, triage only that project. Do not broaden to all Peter/org queues unless the user says `broad`, `all`, `everything`, names multiple owners/orgs, or asks for cross-repo triage.

If the repo has `VISION.md`, read it before judging what can be handled autonomously. Use it as the product-fit source of truth, then apply this skill's risk/testability rules. If no `VISION.md` exists, use the autonomous-fit rules below.

Find the current project:

```bash
repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)
if [ -z "$repo" ]; then
  url=$(git remote get-url origin 2>/dev/null || true)
  repo=$(printf '%s\n' "$url" |
    sed -E 's#^git@github.com:##; s#^https://github.com/##; s#\\.git$##')
fi
printf '%s\n' "$repo"
```

Current-project triage starts with:

```bash
gh issue list --repo "$repo" --state open --limit 50 \
  --json number,title,author,labels,createdAt,updatedAt,url
gh pr list --repo "$repo" --state open --limit 50 \
  --json number,title,author,isDraft,reviewDecision,mergeStateStatus,createdAt,updatedAt,url
```

Before acting on any issue or PR, read all comments and treat Peter/owner comments as authoritative routing instructions. If Peter says it looks good, needs changes, is superseded, is product-approved, or is not wanted, that overrides bot labels and ordinary triage judgment. If there is no Peter/owner comment, use maintainer judgment and say that the call is yours.

Then inspect enough detail to explain every surfaced item. For small queues (about 10 open items or fewer), inspect all items. For larger queues, inspect the top priority slice and say what was not expanded.

```bash
gh issue view <n> --repo "$repo" \
  --json number,title,author,body,comments,labels,createdAt,updatedAt,url
gh pr view <n> --repo "$repo" \
  --json number,title,author,body,comments,files,commits,isDraft,reviewDecision,mergeStateStatus,statusCheckRollup,createdAt,updatedAt,url
gh pr diff <n> --repo "$repo" --patch
```

Only comment, close, merge, rerun, or patch with strong evidence.

## Triage Output

When the user says `triage`, always scan open issues and open PRs for the current repo. Return:

- `Autonomous candidates`: items that appear fixable/landable without more product input, with URL, why it qualifies, required verification, and confidence. This is a selection for review, not permission to start work unless the user also asks for autonomous execution.
- `Needs Peter`: items blocked on Peter/owner decision, product direction, missing credentials/access, live-provider proof that cannot be obtained, security/privacy judgment, or an authoritative Peter comment requesting changes.
- `Defer/close/supersede`: stale, duplicate, lower-quality, or overlapping items where the likely action is not new code.

For every plausible autonomous candidate, use available high-reasoning subagents, oracle, or independent agent review to check feasibility before presenting it when tool support exists. Give the subagent only task-local evidence and ask whether the item can be completed autonomously, what verification is required, and what could make it unsafe. If subagents are unavailable, do the same depth yourself and say so.

## Autonomous Work Mode

When the user says `do work autonomously`, `work you can do autonomously`, `keep going`, or similar, do not stop after a queue summary or one local patch. Treat it as permission to process the eligible issue/PR queue sequentially until no safe autonomous item remains, each item is landed/closed/deferred with proof, or a blocker requires Peter.

Never work multiple tickets at once. For each item:

1. Read the issue/PR, related code, docs, CI, and `VISION.md` if present; Google/use official docs when facts may be stale or unclear.
2. Decide if it is autonomous:
   - Go: performance improvements unless complexity rises too much; bugfixes with repro/root cause and verification path; small UI/UX tweaks; docs fixes; narrow test/internal fixes; low-risk dependency/CI cleanup with green proof.
   - Ask first: new features, product/vision choices, broad behavior changes, risky dependencies, security-sensitive changes without strong proof, live-provider work without usable credentials, anything that cannot be end-to-end tested.
   - Refactor preference: choose a clean bounded refactor when it is the better fix for an autonomous item; do not use "small patch" as the default if it leaves worse design.
3. Implement or fix the PR in the best maintainable way. Prefer updating the contributor PR when writable; otherwise recreate locally with credit.
4. Verify locally and live end-to-end when possible. For UI behavior, use the repo's expected live UI proof path, such as Peekaboo, browser-use, screenshots, or VM proof. For API/provider behavior, use a real usable key/account through the expected secret workflow when available. If access is missing, stop before pretending the item is done and ask Peter for help.
5. Run Codex Auto Review before commit/land unless trivial/docs-only or explicitly skipped; address accepted/actionable findings.
6. Ensure CI is green, PR description/changelog are good, land/close/comment with evidence, then return to `main`, pull `--ff-only`, and verify a clean worktree before selecting the next autonomous item.
7. After every landed PR, post a PR comment with exactly how it was tested: local commands, live/UI/API proof, CI run/check state, landed commit, and any caveats. If verification images apply, upload/post them with `gh image` when available; if the uploader is unavailable, say so and include the screenshot path or alternate GitHub attachment proof instead of silently omitting images.

Do not end autonomous mode with dirty files or an unpushed local fix unless blocked. If blocked, state the exact blocker, current branch/status, proof already gathered, and the next decision needed.

Autonomous work is still bounded by scope: current repo by default; broad/all queues only when the user asked for broad/all/everything or named owners/orgs.


## Required References

Before using this skill, read these files completely. They are normative parts of this skill, split only to keep each file focused:

- `references/evaluation.md` - trust signals and per-item evaluation criteria.
- `references/queue-discovery.md` - broad queue discovery, detail pass, and local cross-check commands.
- `references/heuristics-output.md` - prioritization heuristics, output templates, and acting rules.
