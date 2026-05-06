from typing import Any


ILLUSTRATIVE_NOTE = "Illustrative only — not underwritten pending financial evidence."
BLOCKED_SUMMARY_PREFIX = "Cannot underwrite yet."
PIPELINE_SEARCH_REQUIRED_NOTE = (
    "DA pipeline not verified; search required before treating pipeline as zero."
)
COMPETITOR_METHOD_WARNING = "Competitor supply methodology requires verification."


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _get(data: dict[str, Any] | None, *path: str) -> Any:
    current: Any = data or {}
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _current_utilisation(extracted: dict[str, Any], deal_summary: dict[str, Any]) -> float | None:
    candidates = [
        deal_summary.get("current_occupancy_pct"),
        _get(extracted, "occupancy", "current_month_pct"),
        _get(extracted, "occupancy", "latest_week_pct"),
        _get(extracted, "occupancy", "avg_4wk_pct"),
    ]
    for value in candidates:
        try:
            if value is None:
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _legacy_texts(scored: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for path in [
        ("analyst_summary",),
        ("overall_verdict",),
        ("verdict", "one_liner"),
        ("verdict", "recommended_buyer_profile"),
        ("next_steps", "verdict_plain"),
        ("next_steps", "deal_structuring_notes"),
    ]:
        value = _get(scored, *path)
        if _present(value):
            texts.append(str(value))
    for dimension in (scored.get("dimensions") or {}).values():
        if isinstance(dimension, dict) and _present(dimension.get("summary")):
            texts.append(str(dimension["summary"]))
    return texts


def build_narrative_guard(
    *,
    extracted: dict[str, Any],
    scored: dict[str, Any],
    deal_summary: dict[str, Any],
    missing_fields: list[str],
    valuation_gate: dict[str, Any],
    pipeline_audit: dict[str, Any] | None = None,
    market_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic narrative-safe language without mutating LLM output."""
    can_underwrite = bool(valuation_gate.get("can_show_confident_valuation"))
    blockers = [b.get("field") for b in valuation_gate.get("blockers") or [] if b.get("field")]
    missing_l = {str(field).lower() for field in missing_fields or []}
    core_missing = any(
        marker in missing_l or marker in blockers
        for marker in ["revenue", "ebitda", "payroll", "payroll_labour_cost", "occupancy_history"]
    )
    utilisation = _current_utilisation(extracted, deal_summary)
    low_utilisation = utilisation is not None and utilisation < 50
    pipeline_search_required = bool((pipeline_audit or {}).get("search_required"))
    competitor_supply = (market_audit or {}).get("competitor_supply") or {}
    competitor_mismatch = bool(competitor_supply.get("material_difference"))

    warnings: list[str] = []
    replacement_reasons: list[str] = []

    if not can_underwrite:
        replacement_reasons.append("valuation_gate_blocked")
        warnings.append("Legacy valuation prose must be treated as illustrative only until required financial evidence is verified.")
    if core_missing:
        replacement_reasons.append("core_evidence_missing")
    if low_utilisation:
        replacement_reasons.append("low_current_utilisation")
    if pipeline_search_required:
        replacement_reasons.append("pipeline_search_required")
        warnings.append(PIPELINE_SEARCH_REQUIRED_NOTE)
    if competitor_mismatch:
        replacement_reasons.append("competitor_supply_mismatch")
        warnings.append(COMPETITOR_METHOD_WARNING)

    if not can_underwrite:
        if low_utilisation:
            recommendation = "Operator-led turnaround only — cannot underwrite yet."
        else:
            recommendation = "Investigate only with conditions — cannot underwrite yet."
    elif low_utilisation:
        recommendation = "Operator-led turnaround only."
    else:
        recommendation = _get(scored, "next_steps", "verdict_plain") or _get(scored, "verdict", "one_liner") or "Proceed to IC review subject to evidence."

    analyst_parts: list[str] = []
    if not can_underwrite or core_missing:
        analyst_parts.append(BLOCKED_SUMMARY_PREFIX)
        analyst_parts.append("Revenue, EBITDA, payroll/labour cost, and occupancy history must be verified before price or returns can be underwritten.")
    else:
        analyst_parts.append(str(_get(scored, "analyst_summary") or _get(scored, "verdict", "one_liner") or "Source-backed evidence supports further IC review."))
    if low_utilisation:
        analyst_parts.append(f"Current utilisation is about {utilisation:g}%, so this should be framed as a distressed/operator-led turnaround rather than a normal acquisition.")
    if pipeline_search_required:
        analyst_parts.append(PIPELINE_SEARCH_REQUIRED_NOTE)
    if competitor_mismatch:
        analyst_parts.append(COMPETITOR_METHOD_WARNING)

    valuation_note = (
        ILLUSTRATIVE_NOTE
        if not can_underwrite
        else valuation_gate.get("message") or "Valuation may be considered evidence-supported after source documents are confirmed."
    )
    pipeline_note = (
        PIPELINE_SEARCH_REQUIRED_NOTE
        if pipeline_search_required
        else "DA pipeline evidence reviewed; confirm source dates before IC reliance."
    )
    utilisation_note = (
        f"Current utilisation is about {utilisation:g}%; underwrite as a turnaround/operator-led recovery."
        if low_utilisation
        else None
    )
    market_note = COMPETITOR_METHOD_WARNING if competitor_mismatch else None

    legacy_may_conflict = False
    if not can_underwrite:
        risky_terms = [
            "underwritten valuation",
            "worth $",
            "base valuation",
            "upside valuation",
            "5yr return",
            "5-year return",
            "five year return",
            "proceed with caution",
        ]
        legacy_blob = " ".join(_legacy_texts(scored)).lower()
        legacy_may_conflict = any(term in legacy_blob for term in risky_terms)
        if legacy_may_conflict:
            warnings.append("Legacy scored narrative contained valuation or recommendation language that has been superseded by the workflow guard.")

    return {
        "recommendation": recommendation,
        "analyst_summary": " ".join(part for part in analyst_parts if part),
        "valuation_note": valuation_note,
        "pipeline_note": pipeline_note,
        "utilisation_note": utilisation_note,
        "market_note": market_note,
        "can_use_legacy_valuation_language": can_underwrite,
        "legacy_may_conflict": legacy_may_conflict,
        "replacement_reasons": sorted(set(replacement_reasons)),
        "warnings": warnings,
    }
