# PRODUCT_BOUNDARIES.md - Acquira Product Constraints

Acquira is an evidence-led childcare acquisition workflow.

Core principle:

> Acquira should do the analyst work and force the user to do the partner work.

This means:

- Extract and organise evidence.
- Separate facts, derived metrics, broker claims, assumptions, conflicts, missing evidence, and blocked underwriting.
- Do not overclaim.
- Do not treat public aggregate market data as target-level proof.

## Permanent No-Change Boundaries Unless Explicitly Requested

Do not change:

- scoring logic
- valuation gate logic
- recommendation logic
- IC Pack/export behaviour
- Memo mode behaviour
- frontend behaviour
- evidence basket semantics
- re-underwrite behaviour

## CCS Public Market Benchmark Rules

CCS quarterly data is:

- public aggregate market evidence
- realised CCS usage evidence
- useful for CBDC market benchmarking
- useful for pricing and fee-cap pressure context

CCS quarterly data is not:

- target-level evidence
- licensed capacity
- proof of target occupancy
- proof of target waitlist
- proof of revenue
- proof of EBITDA
- proof of unmet demand
- proof of vacancies

Preferred wording:

- realised CCS usage
- public aggregate market evidence
- CBDC pricing benchmark
- market benchmark
- capacity screen, when approved-place data is also available

Avoid wording:

- demand per centre
- proof of demand
- proof of occupancy
- actual demand
- true demand
- definitive unmet demand

## SA3 Rules

- Do not infer SA3 from postcode, suburb, or address unless explicitly requested.
- SA3 may only be used when:
  - explicitly stated in source documents, or
  - supplied through an explicit admin/manual field, or
  - produced later by an authoritative tested mapping/geocoding slice.

## Fixture Rules

- Do not inject Forest Hill or any fixture data into production flow.
- Fixtures are test-only.
- Normal report flow must not silently include test market data.

## local_demand_supply Rules

`workflow.market_audit.local_demand_supply` must not auto-attach unless all required inputs are available and the task explicitly asks for it.

Required inputs include, at minimum:

- ABS 0-5 population
- approved CBDC places
- current/proposed supply assumptions
- clear geography/catchment basis

The model is a capacity screen, not proof of demand.
