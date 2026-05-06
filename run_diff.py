"""Best-effort underwriting run diff helpers for Acquira."""

from __future__ import annotations

from typing import Any


WATCHED_FACTS: dict[str, tuple[str, ...]] = {
    "revenue": ("financials", "fy25", "revenue"),
    "ebitda": ("financials", "fy25", "ebitda"),
    "payroll_labour_cost": ("financials", "fy25", "total_labour_cost"),
    "asking_price": ("financials", "asking_price"),
    "licensed_places": ("centre", "licensed_places"),
    "current_occupancy_pct": ("occupancy", "current_month_pct"),
    "avg_4wk_occupancy_pct": ("occupancy", "avg_4wk_pct"),
    "avg_13wk_occupancy_pct": ("occupancy", "avg_13wk_pct"),
    "rent_pa": ("financials", "fy25", "rent_pa"),
}


def _get(data: dict[str, Any] | None, *path: str) -> Any:
    cur: Any = data or {}
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _score(scored: dict[str, Any] | None) -> float | None:
    total = _get(scored, "total_score")
    if isinstance(total, (int, float)):
        return float(total)
    overall = _get(scored, "overall_score")
    if isinstance(overall, (int, float)):
        return float(overall) * 10
    return None


def _recommendation(scored: dict[str, Any] | None, workflow: dict[str, Any] | None) -> str | None:
    for value in (
        _get(workflow, "narrative_guard", "recommendation"),
        _get(scored, "next_steps", "verdict_plain"),
        _get(scored, "verdict", "one_liner"),
    ):
        if isinstance(value, str) and value.strip():
            return value
    return None


def _blockers(workflow: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = _get(workflow, "valuation_gate", "blockers")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("field"):
            result[str(row["field"])] = row
    return result


def _missing_fields(workflow: dict[str, Any] | None) -> set[str]:
    fields = _get(workflow, "missing_fields")
    if not isinstance(fields, list):
        fields = []
    blocker_fields = list(_blockers(workflow).keys())
    return {str(field) for field in [*fields, *blocker_fields] if field}


def build_run_diff(
    *,
    base_extracted: dict[str, Any],
    base_scored: dict[str, Any],
    base_workflow: dict[str, Any],
    new_extracted: dict[str, Any],
    new_scored: dict[str, Any],
    new_workflow: dict[str, Any],
    merge_conflicts: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    base_blockers = _blockers(base_workflow)
    new_blockers = _blockers(new_workflow)
    base_score = _score(base_scored)
    new_score = _score(new_scored)
    base_rec = _recommendation(base_scored, base_workflow)
    new_rec = _recommendation(new_scored, new_workflow)

    changed_facts: list[dict[str, Any]] = []
    for field, path in WATCHED_FACTS.items():
        old_value = _get(base_extracted, *path)
        new_value = _get(new_extracted, *path)
        if old_value != new_value:
            changed_facts.append({
                "field": field,
                "path": ".".join(path),
                "old_value": old_value,
                "new_value": new_value,
            })

    return {
        "resolved_blockers": [
            {
                "field": field,
                "old_reason": blocker.get("reason"),
                "required_evidence": blocker.get("required_evidence"),
            }
            for field, blocker in base_blockers.items()
            if field not in new_blockers
        ],
        "new_blockers": [
            {
                "field": field,
                "reason": blocker.get("reason"),
                "required_evidence": blocker.get("required_evidence"),
            }
            for field, blocker in new_blockers.items()
            if field not in base_blockers
        ],
        "changed_facts": changed_facts,
        "changed_confidence": [],
        "valuation_gate_change": {
            "from": _get(base_workflow, "valuation_gate", "status"),
            "to": _get(new_workflow, "valuation_gate", "status"),
            "message_from": _get(base_workflow, "valuation_gate", "message"),
            "message_to": _get(new_workflow, "valuation_gate", "message"),
        },
        "recommendation_change": {
            "from": base_rec,
            "to": new_rec,
        },
        "score_change": {
            "from": base_score,
            "to": new_score,
            "delta": round(new_score - base_score, 1) if base_score is not None and new_score is not None else None,
        },
        "missing_fields_change": {
            "removed": sorted(_missing_fields(base_workflow) - _missing_fields(new_workflow)),
            "added": sorted(_missing_fields(new_workflow) - _missing_fields(base_workflow)),
        },
        "checklist_changes": {
            "resolved_item_ids": [],
            "new_items": [],
            "unchanged_open_items": [],
        },
        "warnings": [*(warnings or []), *[
            f"Selected diligence evidence conflicts with base value for {conflict.get('field') or conflict.get('path')}."
            for conflict in (merge_conflicts or [])
        ]],
    }
