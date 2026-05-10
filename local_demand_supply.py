"""Deterministic local demand-supply capacity screen.

The model estimates realised CCS demand against approved-place supply. It is a
capacity screen and new entrant plausibility tool, not proof of vacancies,
target occupancy, waitlists, revenue, EBITDA, or definitive market capacity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


FORBIDDEN_PHRASES = [
    "demand per centre",
    "proof of demand",
    "proof of occupancy",
    "definitive unmet demand",
]

LOCAL_DEMAND_SUPPLY_CAVEATS = [
    "CCS demand is allocated from SA3 to catchment using population assumptions.",
    "Approved places measure licensed capacity, not actual vacancies.",
    "Approved places may come from state regulator data, manual upload, or low-confidence fallback.",
    "Drive-time/radius catchment does not fully capture parent preferences, road barriers, school pathways, fees, quality, or willingness to switch.",
    "Results are as of the CCS quarter and may be stale.",
]

UNDERWRITING_USE = [
    "local_demand_supply_screen",
    "new_entrant_plausibility",
    "occupancy_upside_plausibility",
    "supply_pressure_benchmark",
]

NOT_UNDERWRITING_USE = [
    "target_occupancy",
    "target_waitlist",
    "target_revenue",
    "target_ebitda",
    "definitive_unmet_demand",
    "actual_vacancies",
]


@dataclass(frozen=True)
class DemandSupplyThresholds:
    strong_child_per_place: float = 2.5
    balanced_child_per_place_min: float = 1.5
    weak_child_per_place: float = 1.2
    high_supply_dilution_pct: float = 10.0
    material_pipeline_dilution_pct: float = 10.0


DEFAULT_THRESHOLDS = DemandSupplyThresholds()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _safe_div(numerator: Any, denominator: Any) -> float | None:
    n = _number(numerator)
    d = _number(denominator)
    if n is None or d in (None, 0):
        return None
    return n / d


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _input(data: dict[str, Any], key: str) -> Any:
    return data.get(key)


def _population_value(data: dict[str, Any], primary_key: str, fallback_key: str, band_key: str, caveats: list[str], caveat: str) -> float | None:
    primary = _number(_input(data, primary_key))
    if primary is not None:
        return primary
    fallback = _number(_input(data, fallback_key))
    if fallback is not None:
        data[band_key] = "0_4_approx"
        caveats.append(caveat)
    return fallback


def _critical_inputs_missing(data: dict[str, Any], sa3_population: float | None, catchment_population: float | None) -> list[str]:
    missing = []
    if _number(_input(data, "sa3_ccs_children_0_5")) is None:
        missing.append("sa3_ccs_children_0_5")
    if sa3_population is None:
        missing.append("sa3_abs_population_0_5_or_0_4")
    if catchment_population is None:
        missing.append("catchment_abs_population_0_5_or_0_4")
    if _number(_input(data, "current_cbdc_approved_places")) in (None, 0):
        missing.append("current_cbdc_approved_places")
    return missing


def _confidence(data: dict[str, Any], caveats: list[str], critical_missing: list[str]) -> str:
    if critical_missing:
        return "unknown"
    approved_confidence = str(_input(data, "approved_places_confidence") or "unknown").lower()
    catchment_type = str(_input(data, "catchment_type") or "").lower()
    uses_age_approx = any("0-4 approximation" in caveat for caveat in caveats)
    if approved_confidence == "high" and not uses_age_approx and catchment_type == "drive_time":
        return "high"
    if approved_confidence == "medium" and not uses_age_approx and catchment_type == "drive_time":
        return "medium"
    if approved_confidence == "low" or uses_age_approx or catchment_type in {"radius", "manual"}:
        return "low"
    return "unknown"


def _capacity_signal(
    *,
    current_child_per_place: float | None,
    post_entry_child_per_place: float | None,
    supply_dilution_pct: float | None,
    future_child_per_place: float | None,
    market_capacity_confidence: str,
    critical_missing: list[str],
    thresholds: DemandSupplyThresholds,
) -> str:
    if critical_missing or market_capacity_confidence in {"low", "unknown"}:
        return "inconclusive"
    ratio = post_entry_child_per_place if post_entry_child_per_place is not None else current_child_per_place
    if ratio is None:
        return "inconclusive"
    high_dilution = supply_dilution_pct is not None and supply_dilution_pct >= thresholds.high_supply_dilution_pct
    pipeline_pressure = future_child_per_place is not None and current_child_per_place is not None and future_child_per_place < current_child_per_place and (
        current_child_per_place - future_child_per_place
    ) / current_child_per_place * 100 >= thresholds.material_pipeline_dilution_pct
    if ratio < thresholds.weak_child_per_place:
        return "crowded"
    if ratio >= thresholds.strong_child_per_place and not high_dilution and not pipeline_pressure:
        return "supportive"
    if high_dilution or pipeline_pressure:
        return "stretched"
    if ratio >= thresholds.balanced_child_per_place_min:
        return "balanced"
    return "stretched"


def _signal_reasons(data: dict[str, Any], output: dict[str, Any], critical_missing: list[str]) -> list[str]:
    if critical_missing:
        return [f"Critical input missing: {', '.join(critical_missing)}."]
    reasons: list[str] = []
    sa3_benchmark = _number(_input(data, "sa3_children_0_5_per_cbdc_service"))
    state_median = _number(_input(data, "state_median_children_0_5_per_cbdc_service"))
    national_median = _number(_input(data, "national_median_children_0_5_per_cbdc_service"))
    sa3_name = _input(data, "target_sa3_name") or "Target SA3"
    if sa3_benchmark is not None and state_median is not None:
        direction = "below" if sa3_benchmark < state_median else "above"
        reasons.append(f"{sa3_name} SA3 benchmark is {direction} state median for 0-5/CBDC.")
    elif sa3_benchmark is not None and national_median is not None:
        direction = "below" if sa3_benchmark < national_median else "above"
        reasons.append(f"{sa3_name} SA3 benchmark is {direction} national median for 0-5/CBDC.")
    if output.get("supply_dilution_pct") is not None:
        reasons.append(f"Proposed new places create {output['supply_dilution_pct']:.1f}% supply dilution.")
    if output.get("future_child_per_place") is not None and output.get("current_child_per_place") is not None:
        if output["future_child_per_place"] < output["current_child_per_place"]:
            reasons.append("Future pipeline further lowers child-per-place ratio.")
    reasons.append("This is a capacity screen, not centre-level performance evidence.")
    return reasons


def _evidence_ledger_entry(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "field": "local_demand_supply",
        "label": "Local demand-supply capacity screen",
        "value": output.get("market_capacity_signal"),
        "provenance": "derived",
        "source_quality": "mixed_public_sources",
        "trust": "medium",
        "underwriting_use": list(UNDERWRITING_USE),
        "not_underwriting_use": list(NOT_UNDERWRITING_USE),
        "reason": "Derived from CCS realised usage, ABS population assumptions, approved-place supply inputs, and optional pipeline/new entrant assumptions.",
        "caveats": list(output.get("caveats") or []),
    }


def compute_local_demand_supply(data: dict[str, Any], thresholds: DemandSupplyThresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """Compute a local realised-demand / supply-capacity screen from prepared inputs."""
    working = dict(data)
    caveats = list(LOCAL_DEMAND_SUPPLY_CAVEATS)
    sa3_population = _population_value(
        working,
        "sa3_abs_population_0_5",
        "sa3_abs_population_0_4",
        "sa3_population_age_band_used",
        caveats,
        "Participation rate uses CCS 0-5 against ABS 0-4 approximation; this may overstate participation.",
    )
    catchment_population = _population_value(
        working,
        "catchment_abs_population_0_5",
        "catchment_abs_population_0_4",
        "catchment_population_age_band_used",
        caveats,
        "Catchment demand estimate uses ABS 0-4 approximation against CCS 0-5 participation.",
    )
    critical_missing = _critical_inputs_missing(working, sa3_population, catchment_population)
    participation_rate = _safe_div(_input(working, "sa3_ccs_children_0_5"), sa3_population)
    estimated_realised_demand = catchment_population * participation_rate if catchment_population is not None and participation_rate is not None else None
    current_places = _number(_input(working, "current_cbdc_approved_places"))
    proposed_places = _number(_input(working, "proposed_new_places")) or 0.0
    approved_pipeline = _number(_input(working, "approved_pipeline_places")) or 0.0
    under_construction = _number(_input(working, "under_construction_places")) or 0.0
    lodged = _number(_input(working, "lodged_places")) or 0.0
    lodged_weight = _number(_input(working, "lodged_places_probability_weight"))
    if lodged_weight is None:
        lodged_weight = 0.0

    current_child_per_place = _safe_div(estimated_realised_demand, current_places)
    post_entry_child_per_place = _safe_div(estimated_realised_demand, (current_places or 0) + proposed_places) if current_places is not None else None
    supply_dilution_pct = (proposed_places / current_places * 100) if current_places not in (None, 0) and proposed_places else None
    future_supply_places = current_places + approved_pipeline + under_construction + lodged * lodged_weight if current_places is not None else None
    future_child_per_place = _safe_div(estimated_realised_demand, future_supply_places)
    market_capacity_confidence = _confidence(working, caveats, critical_missing)
    output: dict[str, Any] = {
        "target_address": _input(working, "target_address"),
        "target_sa3_code": _input(working, "target_sa3_code"),
        "target_sa3_name": _input(working, "target_sa3_name"),
        "as_of_quarter": _input(working, "as_of_quarter"),
        "sa3_ccs_participation_rate_0_5": _round(participation_rate),
        "estimated_realised_ccs_demand_0_5": _round(estimated_realised_demand),
        "current_child_per_place": _round(current_child_per_place),
        "post_entry_child_per_place": _round(post_entry_child_per_place),
        "supply_dilution_pct": _round(supply_dilution_pct),
        "future_supply_places": _round(future_supply_places),
        "future_child_per_place": _round(future_child_per_place),
        "market_capacity_signal": "inconclusive",
        "market_capacity_confidence": market_capacity_confidence,
        "signal_reasons": [],
        "caveats": caveats,
    }
    output["market_capacity_signal"] = _capacity_signal(
        current_child_per_place=current_child_per_place,
        post_entry_child_per_place=post_entry_child_per_place,
        supply_dilution_pct=supply_dilution_pct,
        future_child_per_place=future_child_per_place,
        market_capacity_confidence=market_capacity_confidence,
        critical_missing=critical_missing,
        thresholds=thresholds,
    )
    output["signal_reasons"] = _signal_reasons(working, output, critical_missing)
    output["evidence_ledger_entry"] = _evidence_ledger_entry(output)
    return output
