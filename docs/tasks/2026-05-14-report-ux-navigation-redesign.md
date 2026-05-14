# Task: Redesign Report UX Navigation and Section Relationships

Date: 2026-05-14

## Goal

Redesign the report output UX so users understand the relationship between Memo, Underwriting, Evidence, Diligence, and Runs.

The report should feel like an investment decision workflow, not separate repeated report sections.

## Problem

Current report UX has overlapping sections. Underwriting feels repetitive with Evidence, and users may not understand how Memo, Underwriting, Diligence, Evidence, and Runs connect.

The key UX issue:

- Memo tells the story
- Underwriting explains the decision logic
- Evidence proves or challenges the facts
- Diligence tells the user what to do next
- Runs show what changed over time

These relationships are not currently obvious enough.

## Product principle

Use this model:

```text
Memo = decision story
Underwriting = decision logic
Evidence = proof layer
Diligence = next actions
Runs = version history / audit trail
```

The intended report journey is:

```text
Decision -> Story -> Logic -> Proof -> Action -> Version history
```

## Non-goals

Do not:

- change backend scoring logic
- change valuation gate logic
- change recommendation logic
- change extraction logic
- change Evidence Ledger semantics
- change public market benchmark semantics
- change local_demand_supply behaviour
- remove any existing data from the report payload
- change backend APIs unless absolutely required

This is primarily a frontend/UX information architecture slice.

## Required UX changes

### 1. Add or improve Decision Dashboard

Create a clear top-level report summary that shows:

- recommendation
- confidence
- valuation readiness
- evidence readiness
- top blockers
- top risks
- next best action

The dashboard should answer quickly:

- Is this worth looking at?
- Why?
- What is missing?
- What should the user do next?

### 2. Clarify section roles

Make each section clearly answer one question:

- Decision: What is the answer and what blocks confidence?
- Memo: What is the investment story?
- Underwriting: How did the system reach the decision?
- Evidence: What supports or challenges the facts?
- Diligence: What should the user ask for next?
- Runs: What changed over time?

### 3. Reduce repetition between Underwriting and Evidence

Underwriting should not duplicate raw evidence tables.

Underwriting should show:

- score / status
- rationale
- accepted evidence count or summary
- needs-review evidence count or summary
- blockers
- links to evidence
- links to diligence actions

Evidence should remain the source-of-truth proof layer.

Evidence should answer:

- What do we know?
- Where did it come from?
- Can we trust it?

Underwriting should answer:

- How does that evidence affect the investment decision?

### 4. Add cross-navigation

Add clear links/actions between sections.

From Memo:

- View evidence
- View underwriting impact
- Add diligence request

From Underwriting:

- View supporting evidence
- View blockers
- Open diligence item

From Evidence:

- Used in underwriting dimension
- Used in valuation gate
- Create diligence request

From Diligence:

- View missing evidence
- Upload follow-up document
- Re-run underwriting

From Runs:

- Compare to previous run
- View evidence changes
- View memo changes
- Promote this run

### 5. Improve tab/navigation labels

Use user-facing labels:

- Decision
- Memo
- Underwriting
- Evidence
- Diligence
- Run History

Avoid exposing overly technical labels unless in debug mode.

## Suggested layout

```text
Decision Dashboard
|
+-- Memo
+-- Underwriting
+-- Evidence
+-- Diligence
+-- Run History
```

## Recommended section roles

### Decision

Purpose:

Tell the user the answer and what blocks confidence.

Content:

- overall recommendation
- confidence level
- valuation readiness
- evidence readiness summary
- top risks
- top missing evidence items
- next best action

### Memo

Purpose:

Tell the investment story in human-readable form.

Memo should not repeat all evidence tables.

Suggested structure:

- investment thesis
- key facts
- market context
- financial summary
- key risks
- what must be verified
- recommendation

Important claims should link to evidence, underwriting impact, or diligence actions.

### Underwriting

Purpose:

Show how the system reached the decision.

Underwriting should show:

- dimension score
- why the score was given
- which evidence was used
- what changed the score
- what is blocked or needs review

### Evidence

Purpose:

Show the source of truth.

Organise by evidence status:

- Accepted evidence
- Derived evidence
- Needs review
- Conflicting evidence
- Missing evidence
- Excluded evidence

Each evidence item should show, where available:

- fact
- value
- source
- page / sheet / cell
- trust level
- underwriting use
- used in which section

### Diligence

Purpose:

Tell the user what to do next.

Organise by priority:

- Critical before valuation
- Required before offer
- Nice to have
- Post-offer diligence

Each item should connect to evidence and underwriting.

### Runs

Purpose:

Show version history and change impact.

Runs should answer:

- What changed since the last upload?
- Why did the recommendation change?
- Which run is current?
- Which evidence was added?
- Which blockers were resolved?

## Acceptance criteria

The task is complete when:

- user can understand the report journey from Decision -> Memo -> Underwriting -> Evidence -> Diligence -> Runs
- Underwriting no longer feels like a duplicate of Evidence
- Evidence remains the proof layer
- Diligence becomes the next-action layer
- Runs clearly show version/change history
- no backend scoring or evidence semantics change
- existing tests pass
- frontend build passes

## Files likely to inspect

```text
src/components/report/ReportView.tsx
src/types/workflow.ts
src/components/report/*
scripts/qa_frontend_regressions.mjs
```

## Tests

Run frontend tests/build commands used in this repo, likely:

```bash
npm test -- --runInBand
npm run build
```

If the repo has a specific QA regression command, run that too.

## Deliverable

Report:

- UX changes made
- files changed
- tests run
- screenshots or description of new navigation
- confirmation no scoring/backend semantics changed
