# AI_WORKFLOW.md - Acquira AI Development Workflow

Repo docs are the source of truth.

Chat history is not the source of truth.

## Roles

### ChatGPT

Use for:

- product strategy
- task decomposition
- evidence semantics
- investor/report wording
- deciding the next narrow slice
- reviewing whether a change fits the product direction

### Claude

Use for:

- long-context critique
- second-opinion review
- red-team review
- overclaiming checks
- narrative polish
- edge-case analysis

### Codex

Use for:

- repo implementation
- tests
- commits
- deployment verification

## Branch Discipline

Only one coding agent should edit a branch at a time.

If comparing alternatives:

- Codex branch: `codex/task-name`
- Claude branch: `claude/task-name`

Then compare diffs or PRs.

## Daily Loop

1. Define the next narrow slice.
2. Save the task spec under:

   `docs/tasks/YYYY-MM-DD-task-name.md`

3. Codex reads:

   - AGENTS.md
   - PRODUCT_BOUNDARIES.md
   - docs/handoffs/current-state.md
   - active task file

4. Codex implements only that task.
5. Codex runs tests.
6. Codex commits and pushes.
7. ChatGPT or Claude reviews the diff.
8. Codex fixes review comments.
9. Update `docs/handoffs/current-state.md`.

## Standard Codex Prompt

Read:

- AGENTS.md
- PRODUCT_BOUNDARIES.md
- AI_WORKFLOW.md
- docs/handoffs/current-state.md
- docs/tasks/[ACTIVE_TASK].md

Implement only the active task.

Do not change scoring, valuation, recommendation, export, Memo mode, frontend, evidence basket, or re-underwrite unless the task explicitly says so.

Run listed tests.

Commit and push.

Report:

- files changed
- tests run
- commit hash
- deployment verification if relevant
