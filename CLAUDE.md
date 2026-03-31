# CLAUDE.md — Yuanyuan's Projects

## Codex MCP Review Loop

When asked to **review** or **modify** code, follow this workflow:

1. Use the `codex` tool to initiate a review — send the `git diff` as context
2. Analyse Codex's feedback and identify what needs fixing
3. Make the changes yourself
4. Use `codex-reply` (same `threadId`) to send the new diff for re-review
5. Iterate if needed — **maximum 3 rounds**

Trigger phrase: **"review"** → automatically invokes this workflow.

---

## Conventions

- **Never push to `main` directly** — always branch + PR (except comply-ai: direct push is fine)
- Branch naming: `fix/description` or `feat/description`
- Always run build/lint locally before pushing — must pass clean
- Commit messages: imperative, lowercase (e.g. `fix: state detection bug`)

## See global rules

Global instructions are in `~/.claude/CLAUDE.md`
