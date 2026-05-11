# AGENTS.md - Acquira Codex Rules

Before changing code, read:

1. PRODUCT_BOUNDARIES.md
2. AI_WORKFLOW.md
3. docs/handoffs/current-state.md
4. The active task file under docs/tasks/

Do not rely on stale chat history over repo docs.

## Working Rules

- Keep each change narrow.
- Do not change product logic outside the active task.
- Do not refactor unrelated files.
- Do not silently change scoring, valuation, recommendation, export, or frontend behaviour.
- Add or update tests for every backend behaviour change.
- Run the tests listed in the task before committing.
- Report:
  - files changed
  - tests run
  - commit hash
  - deployment / health check result if relevant

## Commit Rules

Use small descriptive commits.

Examples:

- `attach ccs public market benchmark`
- `extract explicit target sa3 fields`
- `mark explicit sa3 benchmark release`

Do not commit secrets, local files, generated reports, or fixture outputs unless explicitly requested.
