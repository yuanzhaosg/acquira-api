# Acquira Current State

Last updated: 2026-05-12

## Current Production Backend Release

`ccs-explicit-sa3-benchmark-20260511`

Health endpoint:

`https://web-production-c3589.up.railway.app/health`

Expected release marker:

`ccs-explicit-sa3-benchmark-20260511`

## Current Backend Capability

Backend can attach:

`workflow.market_audit.public_market_benchmark`

when all conditions are true:

1. Railway has CCS workbook env vars configured.
2. CCS workbook parses successfully.
3. Extracted payload contains explicit target SA3 code or SA3 name.
4. SA3 matches parsed CCS benchmark data.

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

## local_demand_supply

Current behaviour:

`workflow.market_audit.local_demand_supply` remains absent unless manually supplied elsewhere.

Do not auto-attach it yet.

## Recently Completed Commits

- `3165b23 attach ccs public market benchmark`
- `3825f3c chore: mark ccs benchmark attach release`
- `be55a96 extract explicit target sa3 fields`
- `e982423 chore: mark explicit sa3 benchmark release`

## Current Tests

Latest known result:

`95 tests OK, 1 skipped`

## Next Likely Slices

### Option A - preferred next

Add admin/manual SA3 override field.

Purpose:

Allow user/admin to supply SA3 explicitly without postcode-to-SA3 inference.

### Option B

Verify Railway CCS env vars and real workbook path.

Purpose:

Ensure production can parse the real CCS workbook.

### Option C

Run one real IM/report with explicit SA3 in source document.

Purpose:

Confirm Evidence mode displays Public Market Benchmark in real report.

### Option D

Later: authoritative postcode/address/suburb to SA3 mapping.

Do not implement this until separately scoped and tested.

### Option E

Later: production local_demand_supply attach.

Only attach when ABS 0-5 population and approved CBDC places are available.
