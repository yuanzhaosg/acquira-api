# Task: Release Manual SA3 Override

Date: 2026-05-13

## Goal

Mark and verify production release for the manual SA3 override path.

## Context

Latest implementation commit:

`c952802 add manual sa3 benchmark override`

Manual SA3 override now supports:

- `manualContext.sa3Code`
- `manualContext.sa3Name`
- `manual_context.sa3_code`
- `manual_context.sa3_name`
- manual evidence notes with explicit text such as `SA3 code: 21104`

Manual SA3 takes priority over extracted SA3 and is recorded as manual/admin context, not source-document evidence.

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
- inject fixture data into production

## Required Work

1. Update `API_RELEASE` in `main.py` to:

   `ccs-manual-sa3-override-20260513`

2. Run:

```bash
python3 -m py_compile ccs_market_data.py demand_service.py main.py structured_deal.py
python3 -m unittest discover
python3 scripts/qa_public_market_context.py --report-payload --check --out /tmp/acquira-public-market-report-payload-manual-sa3.json
```

3. Commit and push release marker update.

4. After Railway deploys, verify:

```bash
curl https://web-production-c3589.up.railway.app/health
```

Expected:

```text
release = ccs-manual-sa3-override-20260513
```

## Acceptance Criteria

The task is complete when:

- production health marker shows `ccs-manual-sa3-override-20260513`
- tests pass
- manual SA3 override remains working
- no postcode/suburb/address inference exists
- `local_demand_supply` remains absent unless explicitly supplied
- scoring/export/recommendation remain unchanged

## Deliverable

Report:

- release marker commit hash
- tests run
- health response
- confirmation production deployed
