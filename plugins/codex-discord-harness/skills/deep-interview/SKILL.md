---
name: deep-interview
description: Use for vague, broad, risky, or under-specified work requests before implementation; runs a Gajae Code style Socratic interview with ambiguity scoring, work-structure locking, ontology tracking, and explicit approval gating before coding.
---

# Deep Interview

This is a Codex-port of the Gajae Code deep-interview workflow. Keep the original intent and phase structure intact:

- expose hidden assumptions through Socratic questioning
- mathematically score ambiguity after each answer
- lock the top-level work structure before depth-first questioning
- gather brownfield codebase facts before asking the user to rediscover them
- stop at a pending-approval ticket before any implementation

Source pattern:
https://github.com/Yeachan-Heo/gajae-code/tree/main/packages/coding-agent/src/defaults/gjc/skills/deep-interview

License notice for the upstream material is kept in `NOTICE.md`.

## Codex Port Contract

The upstream Gajae skill uses GJC-specific state, fragments, and workflow entrypoints. Preserve those behaviors with these Codex equivalents:

- Where upstream says `gjc state write` or `gjc state read`, maintain the interview state in the conversation. Do not edit `.gjc/` or create repo artifacts unless the user explicitly asks for persisted planning files.
- Where upstream says `ask tool`, ask the user directly in the normal Codex conversation. Ask exactly one user-facing question at a time.
- Where upstream says `explore agent`, use read-only repository inspection yourself first. If a multi-agent tool is available and explicitly appropriate, it must remain read-only during the interview.
- Where upstream says `/skill:ralplan`, `/skill:ultragoal`, or `/skill:team`, present the equivalent pending next step and wait for explicit user approval. Do not auto-invoke execution, commits, pushes, PRs, mutation commands, or implementation delegates.
- Where upstream refers to `.gjc/specs/deep-interview-{slug}.md`, produce the same spec shape in the conversation unless the user explicitly requests a file.
- Where upstream says fallback after fragment failure, do not invent requirements. Continue the manual interview path and surface any material failure that affects the user-facing decision.
- If the user is writing Korean, every user-facing question, option, and summary must be Korean. Use "작업 구조" instead of "토폴로지" in Korean user-facing text.

## When To Use

Use this skill when any of these are true:

- The user has a vague idea and wants thorough requirements gathering before execution.
- The user says "deep interview", "interview me", "ask me everything", "don't assume", "make sure you understand", or the Korean equivalent.
- The user wants to avoid "that's not what I meant" outcomes from autonomous execution.
- The task is complex enough that jumping to code would spend time discovering scope instead of implementing.
- The user wants mathematically visible clarity before committing to execution.
- The request is brownfield work but lacks explicit integration points, constraints, or acceptance criteria.

Do not use this skill when:

- The user gives a precise command, exact-reply QA, or simple factual question.
- The user has already provided concrete file paths, function names, acceptance criteria, and explicitly asks to execute.
- The user explicitly says not to ask questions or to skip the interview.
- The task is a small, low-risk change where a normal code edit is clearly the requested path.

If the user says "just do it" while the request is still vague, respect the intent by summarizing a pending-approval ticket with the remaining ambiguity. Do not mutate files during the interview.

## Global Rules

- Ask ONE question at a time. Never batch multiple user-facing questions.
- Preserve the user's language for announcements, work-structure confirmation, options, questions, progress reports, and final ticket.
- Target the weakest active component and weakest clarity dimension each round.
- Make weakest-dimension targeting explicit every round: name the dimension, state its score or gap, and explain why the next question is aimed there.
- Before Round 1 ambiguity scoring, run a one-time Round 0 work-structure enumeration gate.
- For brownfield work, gather repository facts before asking the user about codebase context. Cite file paths, symbols, or observed patterns.
- Score ambiguity after every answer and show the score transparently.
- When several active components exist, score and target each component so one detailed component cannot hide unclear siblings.
- Keep prompt payloads budgeted. If the initial context or interview transcript is too large, summarize it before scoring, question generation, or final ticket generation.
- Do not proceed to execution until ambiguity is at or below the resolved threshold and the user explicitly approves a scoped execution path.
- Allow early exit only with a clear warning that names the unresolved gaps.
- Track ontology changes across rounds so shifting nouns or entity names are visible.
- Do not silently fill missing requirements. Turn unknowns into questions.
- If a required read, command, or tool fails, surface the actual error and stop or ask how to proceed.

## Internal Fragments

The upstream workflow has two internal fragments. This port keeps them as files in this skill directory:

- `auto-research-greenfield.md`
- `auto-answer-uncertain.md`

Load them only at the documented hooks:

- Auto-research: between Step 2a and Step 2b when a greenfield question is explicitly tagged `research: true`.
- Auto-answer: after the user opts out, answers with uncertainty, or explicitly asks the agent to decide.

Fragments are internal prompts, not public skills. They must never be advertised as slash commands, registered as separate skills, used to mutate files, or used to skip explicit approval.

## Phase 0: Resolve Ambiguity Threshold

Complete this phase before initialization, before Round 0, and before any ambiguity score.

1. Resolve the threshold.
   - If the user explicitly gave a threshold, use it.
   - Otherwise use the Gajae default `0.05`.
2. Track the threshold source as one of:
   - `user`
   - `default`
   - `project-config` only if the user explicitly supplied a readable project config path
3. Emit the threshold marker before any other interview announcement:

```text
Deep Interview threshold: {threshold} (source: {threshold_source})
```

4. Carry `threshold` and `threshold_source` into every later score report and the final ticket.
5. Infer the user's language from the request when obvious. Do not surprise a Korean session with English questions.

## Phase 1: Initialize

1. Parse the user's idea from the request.
2. Detect greenfield vs brownfield.
   - Brownfield: the current working directory contains existing source code, package files, or git history, and the request references modifying or extending it.
   - Greenfield: the request is primarily about creating a new thing without existing integration context.
3. For brownfield work, build initial codebase context before Round 1:
   - Inspect relevant files with read-only commands.
   - Search for file names, functions, configuration, command handlers, tests, or domain terms related to the request.
   - Summarize durable facts only: ownership, boundaries, existing patterns, constraints, and unresolved gaps.
   - Do not ask the user to tell you facts the repo already reveals.
4. Normalize oversized initial context:
   - If the request includes large logs, transcripts, screenshots, or file excerpts, summarize them first.
   - Treat the summary as the canonical initial idea.
   - Preserve explicit decisions, constraints, non-goals, file references, and unresolved gaps.
5. Initialize interview state in the conversation:

```json
{
  "active": true,
  "current_phase": "interviewing",
  "interview_id": "{stable id if useful}",
  "type": "greenfield|brownfield",
  "initial_idea": "{prompt-safe idea}",
  "initial_context_summary": "{summary or empty}",
  "rounds": [],
  "current_ambiguity": 1.0,
  "threshold": 0.05,
  "threshold_source": "default",
  "language": "{user/session language}",
  "codebase_context": "{brownfield facts or null}",
  "work_structure": {
    "status": "pending",
    "components": [],
    "deferrals": [],
    "last_targeted_component_id": null
  },
  "challenge_modes_used": [],
  "ontology_snapshots": [],
  "auto_researched_rounds": [],
  "auto_answered_rounds": [],
  "architect_failures": 0
}
```

6. Announce the interview after the threshold marker:

```text
Starting deep interview. I will ask targeted questions before any implementation. After each answer, I will show the clarity score. We proceed only after the ambiguity threshold is met and you explicitly approve the next step.

Your idea: "{initial_idea}"
Project type: {greenfield|brownfield}
Current ambiguity: 100% (not scored yet)
```

Translate this announcement to the user's language.

## Round 0: Work-Structure Enumeration Gate

Run this gate exactly once after Phase 1 initialization and before Phase 2 ambiguity scoring.

The goal is to lock the shape of the user's scope before depth-first questioning overfits to the most-described component.

1. Enumerate candidate top-level components from the initial idea and brownfield context.
   - Extract top-level verbs, nouns, workstreams, surfaces, integrations, or deliverables that can succeed or fail independently.
   - Prefer 1-6 components.
   - If more than 6 appear, group siblings at the highest useful level and explain the grouping.
   - Do not treat implementation tasks, fields, or sub-features as top-level components unless the user framed them as independent outcomes.
2. Ask exactly one confirmation question before scoring:

```text
Round 0 | Work structure confirmation | Ambiguity: not scored yet

I'm reading this as {N} top-level component(s):
1. {component_name}: {one_sentence_description}
2. ...

Current understanding: {one-sentence summary}
Blocked decision: whether these are the right top-level components.
Recommended answer: {sensible default, usually "이 작업 구조대로 진행"}
Question: Is this work structure right? Should any component be added, removed, merged, split, or explicitly deferred?
```

For Korean:

```text
Round 0 | 작업 구조 확인 | Ambiguity: not scored yet
...
Question: 이 작업 구조가 맞나요? 추가, 제거, 병합, 분리, 또는 보류할 항목이 있나요?
```

3. After the answer, lock the work structure:

```json
{
  "work_structure": {
    "status": "confirmed",
    "confirmed_at": "{timestamp if useful}",
    "components": [
      {
        "id": "component-slug",
        "name": "Component Name",
        "description": "Confirmed top-level outcome",
        "status": "active|deferred",
        "evidence": ["initial prompt phrase or brownfield citation"],
        "clarity_scores": {
          "goal": null,
          "constraints": null,
          "criteria": null,
          "context": null
        },
        "weakest_dimension": null
      }
    ],
    "deferrals": [
      {
        "component_id": "component-slug",
        "reason": "User-confirmed deferral reason",
        "confirmed_at": "{timestamp if useful}"
      }
    ],
    "last_targeted_component_id": null
  }
}
```

4. Single-component pass-through:
   - If the user confirms one active component, proceed normally while carrying that component into scoring and the final ticket.
5. Multi-component behavior:
   - Every active component must receive sufficient goal, constraint, and success-criteria clarity.
   - Rotate targeting across similarly weak components.
   - The final ticket must cover every active component and explicitly list deferrals.

## Phase 2: Interview Loop

Repeat until ambiguity is at or below threshold, the hard cap is reached, or the user exits early.

### Step 2a: Generate Next Question

Build the next question from:

- prompt-safe initial idea
- prior Q&A, trimmed or summarized if needed
- current clarity scores per dimension
- locked work structure
- brownfield codebase context, summarized with file/path/symbol citations
- ontology snapshots from previous rounds
- challenge mode if active
- preserved language instruction

If any input is too large, summarize it before generating the question.

Targeting strategy:

1. Identify the active component with the lowest clarity.
2. Identify that component's weakest dimension.
3. If multiple components are tied, rotate away from the last targeted component.
4. Generate a question that specifically improves that component and dimension.
5. Explain why that component/dimension is the bottleneck.
6. Ask a question that exposes assumptions, not broad feature wishlists.
7. If the core nouns keep changing, ask an ontology-style question before continuing feature detail.

Question styles by dimension:

| Dimension | Question Style | Example |
| --- | --- | --- |
| Goal clarity | What exactly happens when...? | "When you say manage tasks, what specific action does a user take first?" |
| Constraint clarity | What are the boundaries? | "Should this work offline, or is internet connectivity assumed?" |
| Success criteria | How do we know it works? | "If I showed you the finished result, what would make you say yes, that's it?" |
| Context clarity | How does this fit? | "I found JWT auth middleware in `src/auth/`. Should this extend that path or intentionally diverge?" |
| Scope-fuzzy ontology | What is the core thing? | "You have named Tasks, Projects, and Workspaces. Which one is the core entity?" |

### Step 2a-prime: Auto-Research Greenfield Questions

When the next question is greenfield and explicitly tagged `research: true`, load `auto-research-greenfield.md`.

Pass only:

- the tagged question
- locked work structure summary
- prompt-safe initial idea
- prior decisions/gaps
- relevant constraints

The fragment must return 2-3 ranked candidates with rationale, confidence, and uncertainty. Validate the shape before using it. If it is useful, incorporate the candidates into the single user-facing question. If it fails or is not useful, ask the normal manual question and increment `architect_failures`; do not invent a requirement.

### Step 2b: Ask The Question

Present each question with this structure:

```text
Round {n} | Component: {target_component_name} | Targeting: {weakest_dimension} | Ambiguity: {score}%
Why now: {one_sentence_targeting_rationale}

Current understanding: {one sentence}
Blocked decision: {the decision that cannot be made safely yet}
Recommended answer: {sensible default if one exists; otherwise say no default}
Question: {exactly one question}
```

Translate the whole structure to the user's language except stable labels or code identifiers where preserving them helps.

### Step 2b-prime: Auto-Answer Uncertain Opt-Out

If the user opts out, answers with uncertainty, or explicitly asks the agent to decide, load `auto-answer-uncertain.md`.

Pass only:

- the opted-out question
- transcript summary
- locked work structure
- current scores/gaps
- any auto-research candidates used for the round

The fragment must return exactly one tentative answer with rationale, confidence, and uncertainty. Validate before using.

Auto-answer clarity cap:

- If confidence is not high and uncertainty is not negligible, no dimension improved only by auto-answer may exceed `0.85`.
- If the auto-answer would cross the threshold, ask the user for threshold-crossing confirmation before the final ticket.
- If validation fails, continue treating the gap as unresolved.

### Step 2c: Score Ambiguity

After each user answer, score all clarity dimensions from 0.0 to 1.0.

For greenfield:

```text
ambiguity = 1 - (goal * 0.40 + constraints * 0.30 + criteria * 0.30)
```

For brownfield:

```text
ambiguity = 1 - (goal * 0.35 + constraints * 0.25 + criteria * 0.25 + context * 0.15)
```

Scoring prompt to apply internally:

```text
Given the interview transcript for a {greenfield|brownfield} project, score clarity on each dimension from 0.0 to 1.0.

Honor the locked work structure. Score every active component independently and never drop sibling components just because one component is already clear.

Score each dimension:
1. Goal clarity: Is the primary objective unambiguous? Are core entities and relationships stable?
2. Constraint clarity: Are boundaries, limitations, and non-goals explicit?
3. Success criteria clarity: Could a test or acceptance check verify success?
4. Context clarity for brownfield: Do we understand the existing system well enough to modify it safely?

For each dimension provide:
- score
- one-sentence justification
- gap if score < 0.9

Also identify:
- weakest_component_id
- weakest_dimension
- weakest_dimension_rationale
- component_scores keyed by component id
```

### Ontology Extraction

After each answer, identify key entities:

- name
- type
- fields
- relationships

For rounds after the first, compare with the previous ontology snapshot:

- `stable_entities`: same name and concept
- `changed_entities`: renamed but same type and more than 50% field overlap
- `new_entities`: not matched to a prior entity
- `removed_entities`: no longer present
- `stability_ratio`: `(stable + changed) / total_entities`

Show the user enough matching reasoning to sanity-check the result. Renamed entities are convergence, not instability, when the concept persists.

### Step 2d: Report Progress

After scoring, show:

```text
Round {n} complete.

| Dimension | Score | Weight | Weighted | Gap |
| --- | ---: | ---: | ---: | --- |
| Goal | {s} | {w} | {s*w} | {gap or clear} |
| Constraints | {s} | {w} | {s*w} | {gap or clear} |
| Success Criteria | {s} | {w} | {s*w} | {gap or clear} |
| Context (brownfield) | {s} | {w} | {s*w} | {gap or clear} |
| Ambiguity | | | {score}% | |

Work structure: Targeted {target_component_name} | Active: {active_count} | Deferred: {deferred_count} | Next rotation after: {last_targeted_component_id}
Ontology: {entity_count} entities | Stability: {stability_ratio} | New: {new} | Changed: {changed} | Stable: {stable}
Next target: {target_component_name} / {weakest_dimension} - {weakest_dimension_rationale}
```

If ambiguity is at or below threshold, say the clarity threshold is met and move to Phase 4. Otherwise ask the next Phase 2 question.

### Step 2e: Update State

Track in conversation:

- new round
- global scores
- per-component clarity scores
- per-component weakest dimensions
- ontology snapshot
- last targeted component id
- auto-researched rounds
- auto-answered rounds
- architect failures

### Step 2f: Soft Limits And Exit

- Round 3+: if the user says enough, let's go, build it, or equivalent, allow early exit with a risk warning.
- Round 10: show a soft warning and ask whether to continue or proceed with current clarity.
- Round 20: hard cap. Proceed to a pending-approval ticket with current clarity and explicit risk.
- If ambiguity stalls within plus or minus 0.05 for 3 rounds, activate Ontologist mode.
- If all dimensions are 0.9 or higher, proceed to Phase 4 even if the numeric threshold is not exactly met.
- If codebase exploration fails for brownfield, surface the failure and ask whether to continue with limited context. Do not silently treat it as greenfield.

## Phase 3: Challenge Modes

At specific round thresholds, shift the perspective. Each mode is used once.

### Round 4+: Contrarian Mode

Inject this into question generation:

```text
You are now in CONTRARIAN mode. Your next question should challenge the user's core assumption. Ask "What if the opposite were true?" or "What if this constraint does not actually exist?" The goal is to test whether the framing is correct or habitual.
```

### Round 6+: Simplifier Mode

Inject:

```text
You are now in SIMPLIFIER mode. Your next question should probe whether complexity can be removed. Ask "What is the simplest version that would still be valuable?" or "Which constraints are actually necessary vs assumed?"
```

### Round 8+: Ontologist Mode

If ambiguity is still above `0.30`, inject:

```text
You are now in ONTOLOGIST mode. The ambiguity is still high, suggesting we may be addressing symptoms instead of the core problem. The tracked entities so far are: {current_entities_summary}. Ask "What IS this, really?" or "Which entity is the CORE concept and which are supporting?"
```

## Phase 4: Crystallize Spec

When ambiguity is at or below threshold, hard cap is reached, or early exit is confirmed:

1. Generate a final spec from the prompt-safe transcript.
2. Preserve the user's language in prose.
3. Keep code identifiers, file paths, commands, JSON keys, settings keys, and source citations unchanged.
4. Do not write the spec to disk unless the user explicitly asked for a file.
5. Mark the spec as pending approval.

Spec structure:

```markdown
# Deep Interview Spec: {title}

## Metadata
- Interview ID: {id if used}
- Rounds: {count}
- Final Ambiguity Score: {score}%
- Type: greenfield | brownfield
- Generated: {timestamp if useful}
- Threshold: {threshold}
- Threshold Source: {threshold_source}
- Initial Context Summarized: yes|no
- Status: PASSED | BELOW_THRESHOLD_EARLY_EXIT
- Auto-Researched Rounds: {auto_researched_rounds}
- Auto-Answered Rounds: {auto_answered_rounds}
- Architect Failures: {architect_failures}

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
| --- | ---: | ---: | ---: |
| Goal Clarity | {s} | {w} | {s*w} |
| Constraint Clarity | {s} | {w} | {s*w} |
| Success Criteria | {s} | {w} | {s*w} |
| Context Clarity | {s} | {w} | {s*w} |
| Total Clarity | | | {total} |
| Ambiguity | | | {1-total} |

## Work Structure
| Component | Status | Description | Coverage / Deferral Note |
| --- | --- | --- | --- |
| {component.name} | active|deferred | {description} | {coverage or deferral reason} |

## Goal
{crystal-clear goal statement covering every active component}

## Included Scope
- {included scope}

## Excluded Scope / Non-Goals
- {non-goal}

## Constraints
- {constraint}

## Acceptance Criteria
- [ ] {testable criterion}

## Assumptions Exposed And Resolved
| Assumption | Challenge | Resolution |
| --- | --- | --- |
| {assumption} | {how it was questioned} | {decision} |

## Technical Context
{brownfield repo findings with paths/symbols, or greenfield technology constraints}

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
| --- | --- | --- | --- |
| {entity.name} | {entity.type} | {fields} | {relationships} |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | {n} | {n} | - | - | - |
| {final} | {n} | {new} | {changed} | {stable} | {ratio}% |

## Interview Transcript
### Round 1
Q: {question}
A: {answer}
Ambiguity: {score}%

## Risks And Open Questions
- {remaining gap}

## Proposed Next Step
{pending approval next step}
```

## Phase 5: Execution Bridge

After the spec is ready, present exactly one next-step question and wait:

```text
Your spec is ready (ambiguity: {score}%). How would you like to proceed?
```

Offer context-appropriate options:

1. Refine plan before implementation (recommended for broad, risky, or multi-component work).
2. Approve implementation from this spec.
3. Continue interviewing to improve clarity.
4. Stop here with the pending ticket.

Do not implement directly from deep-interview. Implementation requires a separate explicit user approval after the ticket is shown.

If the user approves implementation:

- restate the approved scope
- note remaining risks
- then proceed according to the normal Codex workflow

If the user chooses plan refinement:

- produce a patch plan, not code
- wait for approval before implementation

## Good Examples

Targeting weakest dimension:

```text
Scores: Goal=0.9, Constraints=0.4, Criteria=0.7
Next question targets Constraints because it is lowest at 0.4:
"You mentioned this should work on mobile. Does that mean native app, responsive web app, or PWA?"
```

Gathering codebase facts before asking:

```text
I found JWT authentication in `src/auth/` using passport.js. For this feature, should we extend that auth middleware or intentionally create a separate authentication flow?
```

Contrarian mode:

```text
Round 5 | Contrarian Mode | Ambiguity: 42%
You said this needs to support 10,000 concurrent users. What if it only needed to handle 100? Would the architecture change fundamentally, or is the 10K number an assumption?
```

Early exit warning:

```text
Current ambiguity is 35% (threshold: 5%). Areas still unclear:
- Success Criteria: 0.5
- Constraints: 0.6

Proceeding may require rework. Continue anyway?
```

Ontology convergence:

```text
Round 3 entities: User, Task, Project
Round 4 entities: User, Task, Project, Tag
Round 5 entities: User, Task, Project, Tag

Ontology has converged: the same 4 entities appeared in two consecutive rounds.
```

## Bad Examples

Batching multiple questions:

```text
What is the target audience? What tech stack? How should auth work? Where is it deployed?
```

Asking about facts the repo reveals:

```text
What database does your project use?
```

Proceeding despite high ambiguity:

```text
Ambiguity is 45% but we have done 5 rounds, so let's build.
```

## Completion Checklist

- [ ] Phase 0 completed before Round 0.
- [ ] First user-visible line was the threshold marker.
- [ ] User language was preserved.
- [ ] Work structure gate completed before ambiguity scoring.
- [ ] Every active component has goal, constraints, criteria, and context scores as applicable.
- [ ] Ambiguity score displayed after every answer.
- [ ] Every round named the weakest component and weakest dimension.
- [ ] Brownfield questions cite repo evidence before asking the user to decide.
- [ ] Oversized context was summarized before scoring or final-ticket generation.
- [ ] Challenge modes activated at the correct thresholds when needed.
- [ ] Ontology snapshots tracked key entities and convergence.
- [ ] Auto fragments were used only at their documented hooks.
- [ ] Final ticket includes work structure, goal, constraints, non-goals, acceptance criteria, technical context, ontology, transcript, risks, and proposed next step.
- [ ] No implementation, mutation command, commit, push, PR, or execution delegation occurred before explicit approval.

## Configuration

Optional Gajae-style settings can be represented by user instruction or an explicitly supplied config:

```json
{
  "deepInterview": {
    "ambiguityThreshold": 0.05,
    "maxRounds": 20,
    "softWarningRounds": 10,
    "minRoundsBeforeExit": 3,
    "enableChallengeModes": true,
    "autoExecuteOnComplete": false,
    "defaultExecutionMode": null,
    "scoringModel": "consistent-low-temperature-reasoning"
  }
}
```

Do not silently read or write config files. If a config path matters, ask for or cite it.

## Resume

If interrupted, resume from the conversation state:

- threshold and source
- project type
- locked work structure
- prior rounds
- latest scores
- ontology snapshots
- remaining gaps
- pending final ticket if already produced

If the state is unavailable, say that explicitly and restart from Round 0 rather than pretending prior answers are known.

## Ambiguity Score Interpretation

| Ambiguity | Meaning | Action |
| ---: | --- | --- |
| 0.0-0.1 | Crystal clear | Proceed to pending ticket |
| At or below threshold | Clear enough | Proceed to pending ticket |
| Slightly above threshold | Minor gaps | Continue targeted questioning |
| Moderate | Significant gaps | Focus weakest dimensions |
| High | Very unclear | Consider challenge/ontology mode |
| Extreme | Almost nothing known | Continue early rounds |

Task: {{ARGUMENTS}}
