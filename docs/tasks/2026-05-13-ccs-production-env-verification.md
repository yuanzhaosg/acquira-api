# Task: Verify CCS Production Env and Real Report Behaviour

Date: 2026-05-13

## Goal

Verify that the deployed backend can attach `workflow.market_audit.public_market_benchmark` in a real production report when CCS workbook env vars and explicit target SA3 are available.

## Context

Production backend release is live:

`ccs-explicit-sa3-benchmark-20260511`

Backend supports:

`workflow.market_audit.public_market_benchmark`

when all conditions are true:

1. Railway has CCS workbook env vars configured.
2. CCS workbook parses successfully.
3. Extracted payload contains explicit `centre.sa3_code` or `centre.sa3_name`.
4. SA3 matches parsed CCS benchmark data.

## Non-goals

Do not:

- change scoring
- change valuation gate
- change recommendation logic
- change IC Pack/export
- change Memo mode
- change frontend
- infer SA3 from postcode/suburb/address
- auto-attach `local_demand_supply`
- inject Forest Hill fixture data into production

## Required Checks

1. Confirm production health endpoint shows:

   `ccs-explicit-sa3-benchmark-20260511`

2. Confirm whether Railway variables are configured:

   - `ACQUIRA_CCS_WORKBOOK_PATH`
   - `ACQUIRA_CCS_QUARTER`
   - `ACQUIRA_CCS_SOURCE_URL` optional

3. Confirm the workbook path is accessible in Railway runtime.

4. Run a real or controlled report where the source document explicitly states either:

   - `SA3 code: 21104`, or
   - `SA3 name: Whitehorse-East`

5. Inspect resulting report JSON for:

   `workflow.market_audit.public_market_benchmark`

6. Confirm:

   - `workflow.market_audit.public_market_benchmark` is present
   - SA3 is correct
   - `children_0_5_per_cbdc_service` is populated
   - `workflow.market_audit.local_demand_supply` is absent

## Tests / Commands

Run locally:

```bash
python3 -m py_compile ccs_market_data.py demand_service.py main.py structured_deal.py
python3 -m unittest discover
python3 scripts/qa_public_market_context.py --report-payload --check --out /tmp/acquira-public-market-report-payload-ccs-sa3.json
```

Check production:

```bash
curl https://web-production-c3589.up.railway.app/health
```

## Acceptance Criteria

The task is complete when:

- production health marker is correct
- Railway CCS env vars are confirmed or explicitly noted as missing
- real/controlled report JSON confirms whether `public_market_benchmark` appears
- no `local_demand_supply` auto-attach occurs
- no postcode-to-SA3 inference occurs
- no fixture data is injected

## Deliverable

Report:

- env var status
- report JSON result
- files changed, if any
- tests run
- whether a code change is needed
- recommended next task
