# Task: Add Manual SA3 Override for Public Market Benchmark

Date: 2026-05-13

## Goal

Allow an explicit manually supplied SA3 code or SA3 name to be used for attaching `workflow.market_audit.public_market_benchmark`.

This enables real reports to show the CCS Public Market Benchmark even when the IM/source documents do not state SA3.

## Context

Production verification confirmed:

- backend release is live: `ccs-explicit-sa3-benchmark-20260511`
- CCS parser works
- attach path works
- frontend is not the issue
- real production report JSON does not contain `workflow.market_audit.public_market_benchmark`
- real extracted payloads do not contain `centre.sa3_code` or `centre.sa3_name`

Current failing condition:

`explicit target SA3 is not present in real extracted payloads`

## Non-goals

Do not:

- infer SA3 from postcode
- infer SA3 from suburb
- infer SA3 from address
- add full geocoding
- change scoring
- change valuation gate
- change recommendation logic
- change IC Pack/export
- change Memo mode
- change frontend unless absolutely required by existing API shape
- auto-attach `local_demand_supply`
- inject Forest Hill fixture data into production

## Required Behaviour

Add a backend-only manual/admin override path.

The system should be able to accept explicit SA3 override values from an existing safe input location if available.

Preferred input priority:

1. Manual/admin context SA3 override
2. Explicit SA3 extracted from source documents
3. No SA3

Do not infer from postcode/suburb/address.

Suggested override field names:

```text
manual_context.sa3_code
manual_context.sa3_name
```

or, if current request schema uses another manual context object, use that existing object instead.

## Behaviour Rules

- If manual SA3 code is supplied, use it for CCS benchmark matching.
- If manual SA3 name is supplied, use it for CCS benchmark matching.
- Manual SA3 should be treated as user/admin-supplied context, not source-document evidence.
- Preserve provenance/semantics clearly.
- Do not write manual SA3 into extracted source facts as if it came from the IM.
- If manual SA3 is absent, fall back to explicit extracted SA3.
- If neither manual nor extracted SA3 exists, do nothing.
- If SA3 does not match parsed CCS data, do nothing or surface non-user-facing debug warning consistent with repo style.
- Do not create missing-field noise when SA3 is absent.

## Public Market Benchmark Provenance

When manual override is used, the attached benchmark should make clear that:

- CCS benchmark data is public aggregate market evidence
- SA3 selection came from manual/admin context
- it is not target-level evidence
- it is not proof of occupancy, waitlist, revenue, EBITDA, or unmet demand
- it is not licensed capacity

## Tests

Add or update tests for:

1. manual SA3 code attaches `public_market_benchmark`
2. manual SA3 name attaches `public_market_benchmark`
3. manual SA3 takes priority over extracted SA3 if both are present
4. extracted SA3 still works when manual SA3 is absent
5. postcode/suburb/address alone still does not attach benchmark
6. no `local_demand_supply` auto-attach
7. no Forest Hill fixture injection
8. no SA3 missing-field noise
9. manual SA3 provenance is not treated as source-document extraction

## Commands

Run:

```bash
python3 -m py_compile ccs_market_data.py demand_service.py main.py structured_deal.py
python3 -m unittest discover
python3 scripts/qa_public_market_context.py --report-payload --check --out /tmp/acquira-public-market-report-payload-manual-sa3.json
```

## Acceptance Criteria

The task is complete when:

- manual SA3 code/name can trigger benchmark attachment
- extracted SA3 fallback still works
- no postcode/suburb/address inference exists
- local demand-supply remains absent
- scoring/export/recommendation remain unchanged
- tests pass

## Deliverable

Report:

- files changed
- tests run
- input path used for manual SA3
- benchmark attach path confirmed
- provenance treatment
- commit hash
