# Acquira Current State

Last updated: 2026-05-14

## Current Production Backend Release

`ccs-manual-sa3-override-20260513`

Health endpoint:

`https://web-production-c3589.up.railway.app/health`

Expected release marker:

`ccs-manual-sa3-override-20260513`

Production health verified:

`release = ccs-manual-sa3-override-20260513`

## Current Backend Capability

Backend can attach:

`workflow.market_audit.public_market_benchmark`

when all conditions are true:

1. Railway has CCS workbook env vars configured.
2. CCS workbook parses successfully.
3. Manual context or extracted payload contains explicit target SA3 code or SA3 name.
4. SA3 matches parsed CCS benchmark data.

Manual SA3 override is live and supports:

- `manualContext.sa3Code`
- `manualContext.sa3Name`
- `manual_context.sa3_code`
- `manual_context.sa3_name`

Manual context takes priority over extracted SA3 and is treated as manual/admin context, not source-document evidence.

## CCS Env Vars

Required for production attach path:

- `ACQUIRA_CCS_WORKBOOK_PATH`
- `ACQUIRA_CCS_QUARTER`

Optional:

- `ACQUIRA_CCS_SOURCE_URL`

If missing, the attach path is a safe no-op.

## SA3 Extraction

The extraction prompt now includes:

- `centre.sa3_code`
- `centre.sa3_name`

Rules:

- Extract SA3 only when directly stated in source documents.
- Use manual SA3 only when explicitly supplied through manual/admin context.
- Do not infer SA3 from postcode, suburb, or address.
- Do not add SA3 as a missing-field requirement when absent.

## Public Market Benchmark

Expected frontend path:

`workflow.market_audit.public_market_benchmark`

Expected Evidence mode behaviour:

- Show Public Market Benchmark when present.
- Do not show it in Memo mode.
- Do not change IC Pack/export.
- Do not affect scoring, valuation gate, or recommendation.
- Can attach from explicit manual/admin SA3 context.

## local_demand_supply

Current behaviour:

`workflow.market_audit.local_demand_supply` remains absent unless manually supplied elsewhere.

Do not auto-attach it yet.

## Latest Relevant Commits

- `c952802 add manual sa3 benchmark override`
- `547e212 chore: mark manual sa3 override release`

## Current Tests

Latest known result:

- `py_compile` passed
- `python -m unittest discover`: 100 tests OK, 1 skipped
- `scripts/qa_public_market_context.py --report-payload --check` manual SA3 payload passed

## Next Likely Slices

### Option A - preferred next

Run one real report with manual SA3 validation.

Purpose:

Confirm a real report can attach `public_market_benchmark` from explicit manual SA3 context without postcode, suburb, or address inference.

Task:

`docs/tasks/2026-05-14-real-report-manual-sa3-validation.md`

### Option B

Verify Railway CCS env vars and real workbook path.

Purpose:

Ensure production can parse the real CCS workbook.

### Option C

Later: authoritative postcode/address/suburb to SA3 mapping.

Do not implement this until separately scoped and tested.

### Option D

Later: production local_demand_supply attach.

Only attach when ABS 0-5 population and approved CBDC places are available.
