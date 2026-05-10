#!/usr/bin/env python3
"""Generate a deterministic public market context QA payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demand_service import build_market_audit  # noqa: E402
from local_demand_supply import compute_local_demand_supply  # noqa: E402
from structured_deal import build_structured_deal_intelligence  # noqa: E402


PUBLIC_MARKET_BENCHMARK = {
    "source": "Department of Education CCS quarterly data",
    "source_quality": "authoritative_public_aggregate",
    "as_of_quarter": "Dec 2025",
    "sa3_code": "21104",
    "sa3_name": "Whitehorse-East",
    "children_0_5_using_care": 2320,
    "children_6_plus_using_care": 1390,
    "total_children_using_care": 3710,
    "families_using_care": 2840,
    "all_approved_services": 38,
    "cbdc_services": 28,
    "children_0_5_per_cbdc_service": 82.86,
    "total_children_per_all_service": 97.63,
    "cbdc_density_per_1000_children_0_5": 12.07,
    "cbdc_mean_fee_per_hour": 14.52,
    "cbdc_fee_growth_yoy_pct": 4.69,
    "cbdc_services_above_cap_pct": 46.43,
    "caveats": [
        "CCS data is public aggregate market evidence, not target-level evidence.",
        "Children using care measures realised CCS usage, not total latent demand.",
        "SA3 metrics are macro-local benchmarks, not micro-catchment proof.",
        "Approved services are operating services, not licensed places or actual vacancies.",
        "CBDC service count is a service-count proxy, not approved-place capacity.",
        "High fees may indicate pricing power or affordability / gap-fee pressure.",
        "Metrics are as of the published CCS quarter and may be stale.",
    ],
    "underwriting_use": [
        "market_depth_benchmark",
        "cbdc_pricing_benchmark",
        "fee_cap_pressure_screen",
        "new_entrant_plausibility_context",
    ],
    "not_underwriting_use": [
        "target_occupancy",
        "target_waitlist",
        "target_revenue",
        "target_ebitda",
        "definitive_unmet_demand",
        "licensed_place_capacity",
        "actual_vacancies",
    ],
}


FOREST_HILL_LOCAL_MODEL_INPUT = {
    "target_address": "303 Springvale Rd, Forest Hill VIC 3131",
    "target_sa3_code": "21104",
    "target_sa3_name": "Whitehorse-East",
    "as_of_quarter": "Dec 2025",
    "sa3_ccs_children_0_5": 2320,
    "sa3_abs_population_0_5": 5200,
    "sa3_population_age_band_used": "0_5",
    "state_median_children_0_5_per_cbdc_service": 101.6,
    "national_median_children_0_5_per_cbdc_service": 95.4,
    "sa3_children_0_5_per_cbdc_service": 82.86,
    "catchment_type": "drive_time",
    "catchment_minutes": 10,
    "catchment_density_class": "middle_ring",
    "catchment_abs_population_0_5": 4200,
    "catchment_population_age_band_used": "0_5",
    "current_cbdc_approved_places": 1250,
    "current_cbdc_service_count": 15,
    "approved_places_source": "fixture_placeholder_state_register_or_estimate",
    "approved_places_confidence": "medium",
    "proposed_new_places": 100,
    "approved_pipeline_places": 80,
    "under_construction_places": 0,
    "lodged_places": 120,
    "lodged_places_probability_weight": 0.4,
}


DEMAND_CONTEXT = {
    "radius_km": 3.0,
    "is_regional": False,
    "estimated_kids_0_to_4": 1200,
    "total_licensed_places": 900,
    "ldc_util_rate": {"mid": 0.475},
    "adj_kids_per_place": {"mid": 0.63},
    "zone": "balanced",
    "confidence": "medium",
    "abs_hit": True,
    "detail": {"coverage_pct": 82, "year_estimate": 2026, "postcode_area_km2": 28},
}


MARKET_CONTEXT = {
    "edr_mid": 0.63,
    "competitor_count": 5,
    "approved_pipeline_places": 40,
    "confidence": "medium",
}


FORBIDDEN_PHRASES = [
    "demand per centre",
    "proof of demand",
    "proof of occupancy",
    "definitive unmet demand",
    "actual demand",
    "true demand",
]


def build_forest_hill_market_audit_sample() -> dict[str, Any]:
    local_demand_supply = compute_local_demand_supply(FOREST_HILL_LOCAL_MODEL_INPUT)
    return build_market_audit(
        DEMAND_CONTEXT,
        MARKET_CONTEXT,
        competitor_source="ACECQA fixture context",
        included_centres=6,
        pipeline_source="Manual DA fixture context",
        pipeline_searched=True,
        public_market_benchmark=PUBLIC_MARKET_BENCHMARK,
        local_demand_supply=local_demand_supply,
    )


def build_forest_hill_report_payload_sample() -> dict[str, Any]:
    market_audit = build_forest_hill_market_audit_sample()
    pipeline_audit = {
        "source_type": "manual_structured",
        "searched": True,
        "search_required": False,
        "approved_places": 80,
        "lodged_places": 120,
        "risk_adjusted_places": 48,
        "confidence": "medium",
        "warnings": [],
    }
    extracted = {
        "centre": {
            "name": "Forest Hill Fixture ELC",
            "address": "303 Springvale Rd, Forest Hill VIC 3131",
            "suburb": "Forest Hill",
            "state": "VIC",
            "postcode": "3131",
            "licensed_places": 55,
        },
        "financials": {"fy25": {"revenue": 1576862, "ebitda": 407682}},
        "key_ratios": {"licensed_places": 55, "revenue_fy25": 1576862, "ebitda_fy25": 407682},
        "occupancy": {"current_month_pct": 60},
        "_market_audit": market_audit,
        "_pipeline_audit": pipeline_audit,
        "_pipeline_projects": [],
        "meta": {"missing_fields": ["asking_price"]},
    }
    scored = {
        "centre_name": "Forest Hill Fixture ELC",
        "total_score": 55,
        "verdict": {"category": "turnaround", "one_liner": "QA fixture only."},
        "deal_breaker_flags": {"flags": []},
        "market_audit": market_audit,
        "pipeline_audit": pipeline_audit,
        "pipeline_projects": [],
    }
    workflow = build_structured_deal_intelligence(
        extracted=extracted,
        scored=scored,
        combined_text="QA fixture payload for public aggregate market evidence inspection.",
        source_files=["qa_public_market_context_fixture.json"],
        file_classes={"qa_public_market_context_fixture.json": "qa_fixture"},
    )
    return {"extracted": extracted, "scored": scored, "workflow": workflow}


def validate_sample_market_audit(audit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "public_market_benchmark" not in audit:
        errors.append("public_market_benchmark missing")
    if "local_demand_supply" not in audit:
        errors.append("local_demand_supply missing")
    for field in ("edr", "warnings", "competitor_count", "pipeline_places"):
        if field not in audit:
            errors.append(f"existing market audit field missing: {field}")
    if "target_facts" in audit or "deal_facts" in audit:
        errors.append("public market benchmark was placed under target/deal facts")
    benchmark = audit.get("public_market_benchmark") or {}
    model = audit.get("local_demand_supply") or {}
    if not benchmark.get("caveats"):
        errors.append("public_market_benchmark caveats missing")
    if not model.get("caveats"):
        errors.append("local_demand_supply caveats missing")
    if "target_occupancy" not in (benchmark.get("not_underwriting_use") or []):
        errors.append("public_market_benchmark not_underwriting_use missing target_occupancy")
    ledger_not_use = ((model.get("evidence_ledger_entry") or {}).get("not_underwriting_use") or [])
    if "target_revenue" not in ledger_not_use:
        errors.append("local_demand_supply not_underwriting_use missing target_revenue")

    text = json.dumps(audit).lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            errors.append(f"forbidden phrase present: {phrase}")
    return errors


def validate_report_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    workflow = payload.get("workflow") if isinstance(payload, dict) else None
    if not isinstance(workflow, dict):
        return ["workflow missing from report payload"]
    market_audit = workflow.get("market_audit")
    if not isinstance(market_audit, dict):
        return ["workflow.market_audit missing from report payload"]
    errors.extend(validate_sample_market_audit(market_audit))
    if "public_market_benchmark" in workflow:
        errors.append("public_market_benchmark should live under workflow.market_audit")
    if "local_demand_supply" in workflow:
        errors.append("local_demand_supply should live under workflow.market_audit")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Forest Hill public market context QA JSON.")
    parser.add_argument("--out", help="Optional path to write JSON output.")
    parser.add_argument("--check", action="store_true", help="Validate the generated sample before writing.")
    parser.add_argument("--report-payload", action="store_true", help="Generate a report-style payload with workflow.market_audit context.")
    args = parser.parse_args()

    sample = build_forest_hill_report_payload_sample() if args.report_payload else build_forest_hill_market_audit_sample()
    if args.check:
        errors = validate_report_payload(sample) if args.report_payload else validate_sample_market_audit(sample)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1

    rendered = json.dumps(sample, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
