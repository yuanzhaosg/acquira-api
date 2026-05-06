"""Manual DA / pipeline supply normalization for Acquira.

This module is deterministic and intentionally source-agnostic. It converts
structured project entries and legacy count-only inputs into a consistent audit
object for market scoring and investor review.
"""

from __future__ import annotations

import re
from typing import Any, Optional


VALID_STATUSES = {
    "lodged",
    "approved",
    "under_construction",
    "opened",
    "refused",
    "withdrawn",
    "unknown",
}
VALID_CONFIDENCE = {"high", "medium", "low"}
DEFAULT_APPROVED_PLACES = 90
DEFAULT_LODGED_PLACES = 75
DEFAULT_PERMIT_SITE_PLACES = 90
LODGED_RISK_WEIGHT = 0.5
PLACE_TEXT_RE = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?)\s*(?:places?)?\s*$", re.I)


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    match = PLACE_TEXT_RE.match(str(value))
    if not match:
        return None
    try:
        parsed = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(parsed) if parsed >= 0 else None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _clean_status(value: Any) -> str:
    status = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    return status if status in VALID_STATUSES else "unknown"


def _clean_confidence(value: Any, fallback: str = "medium") -> str:
    confidence = str(value or fallback).strip().lower()
    return confidence if confidence in VALID_CONFIDENCE else fallback


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stable_project_id(project: dict[str, Any], index: int) -> str:
    seed = "|".join(
        str(project.get(key) or "")
        for key in ("name", "address", "status", "proposed_places", "source_date")
    ).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", seed).strip("-")[:48]
    return f"pipe_{slug or index + 1}"


def normalize_pipeline_project(project: Any, index: int = 0, *, source_type: str = "manual_structured") -> Optional[dict[str, Any]]:
    proposed_places = _as_int(_get_attr(project, "proposed_places"))
    normalized = {
        "id": _clean_text(_get_attr(project, "id")),
        "name": _clean_text(_get_attr(project, "name")),
        "address": _clean_text(_get_attr(project, "address")),
        "distance_km": _as_float(_get_attr(project, "distance_km")),
        "status": _clean_status(_get_attr(project, "status")),
        "proposed_places": proposed_places,
        "source_url": _clean_text(_get_attr(project, "source_url")),
        "source_file": _clean_text(_get_attr(project, "source_file")),
        "source_date": _clean_text(_get_attr(project, "source_date")),
        "confidence": _clean_confidence(_get_attr(project, "confidence"), "medium"),
        "notes": _clean_text(_get_attr(project, "notes")),
        "source_type": source_type,
    }
    has_signal = any(
        normalized.get(key)
        for key in ("id", "name", "address", "source_url", "source_file", "source_date", "notes")
    ) or proposed_places is not None
    if not has_signal and normalized["status"] == "unknown":
        return None
    normalized["id"] = normalized["id"] or _stable_project_id(normalized, index)
    return normalized


def projects_from_legacy_pipeline_intel(pipeline_intel: Any) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    notes = _get_attr(pipeline_intel, "notes")

    approved_count = _as_int(_get_attr(pipeline_intel, "approved_das")) or 0
    for i in range(approved_count):
        project = normalize_pipeline_project({
            "id": f"legacy_approved_da_{i + 1}",
            "name": f"Legacy approved DA #{i + 1}",
            "status": "approved",
            "proposed_places": DEFAULT_APPROVED_PLACES,
            "confidence": "medium",
            "notes": notes or "Legacy manual count. Assumes 90 places per approved DA.",
        }, len(projects), source_type="manual_legacy_count")
        if project:
            projects.append(project)

    lodged_count = _as_int(_get_attr(pipeline_intel, "lodged_applications")) or 0
    for i in range(lodged_count):
        project = normalize_pipeline_project({
            "id": f"legacy_lodged_da_{i + 1}",
            "name": f"Legacy lodged application #{i + 1}",
            "status": "lodged",
            "proposed_places": DEFAULT_LODGED_PLACES,
            "confidence": "low",
            "notes": notes or "Legacy manual count. Lodged places are risk-adjusted, not treated as approved supply.",
        }, len(projects), source_type="manual_legacy_count")
        if project:
            projects.append(project)

    permit_count = _as_int(_get_attr(pipeline_intel, "permit_sites")) or 0
    for i in range(permit_count):
        project = normalize_pipeline_project({
            "id": f"legacy_permit_site_{i + 1}",
            "name": f"Legacy permit site #{i + 1}",
            "status": "unknown",
            "proposed_places": DEFAULT_PERMIT_SITE_PLACES,
            "confidence": "low",
            "notes": notes or "Legacy permit-site count. Status must be verified before underwriting as pipeline supply.",
        }, len(projects), source_type="manual_legacy_count")
        if project:
            projects.append(project)

    return projects


def build_pipeline_supply(
    pipeline_projects: Optional[list[Any]] = None,
    pipeline_intel: Any = None,
    *,
    search_radius_km: Optional[float] = None,
) -> dict[str, Any]:
    """Return normalized pipeline projects and an underwriting audit.

    Legacy approved DA counts deliberately remain stable: each approved DA is
    still treated as 90 approved places, matching prior `/pipeline` behavior.
    Lodged projects receive a 50% risk-adjusted weighting and do not affect the
    existing approved-places score directly.
    """
    structured: list[dict[str, Any]] = []
    ignored_count = 0
    for i, project in enumerate(pipeline_projects or []):
        normalized = normalize_pipeline_project(project, i, source_type="manual_structured")
        if normalized:
            structured.append(normalized)
        else:
            ignored_count += 1
    legacy = projects_from_legacy_pipeline_intel(pipeline_intel)
    projects = structured + legacy

    approved_places = 0
    lodged_places = 0
    risk_adjusted_places = 0.0
    warnings: list[str] = []

    for project in projects:
        status = project.get("status") or "unknown"
        places = project.get("proposed_places")
        places_count = places or 0
        if places is None and status in {"approved", "under_construction", "lodged"}:
            warnings.append(
                f"{project.get('name') or project.get('address') or 'Pipeline project'} is marked {status.replace('_', ' ')} but proposed places are missing; not counted until capacity is verified."
            )
        if status in {"approved", "under_construction"}:
            approved_places += places_count
            risk_adjusted_places += places_count
        elif status == "lodged":
            lodged_places += places_count
            risk_adjusted_places += places_count * LODGED_RISK_WEIGHT
        elif status == "opened":
            warnings.append(
                f"{project.get('name') or project.get('address') or 'Opened project'} is marked opened; treat as existing competitor supply and avoid double-counting as pipeline."
            )
        elif status in {"refused", "withdrawn"}:
            continue
        else:
            warnings.append(
                f"{project.get('name') or project.get('address') or 'Pipeline project'} has unknown status and is not counted in base pipeline places."
            )

    has_structured = bool(structured)
    has_legacy = bool(legacy)
    searched = has_structured or has_legacy
    source_type = "manual_structured" if has_structured else "manual_legacy_count" if has_legacy else "none"
    confidence = "medium" if has_structured else "medium" if approved_places and has_legacy else "low"

    if not searched:
        warnings.append("No DA/pipeline source was provided; DA search is required before treating pipeline places as zero.")
    if has_legacy:
        warnings.append("Legacy count-only DA inputs were converted to placeholder projects; verify addresses, places, and status.")
    if ignored_count:
        warnings.append(f"{ignored_count} empty pipeline project row(s) were ignored.")

    return {
        "pipeline_projects": projects,
        "pipeline_audit": {
            "source_type": source_type,
            "searched": searched,
            "search_required": not searched,
            "search_radius_km": search_radius_km,
            "approved_places": approved_places,
            "lodged_places": lodged_places,
            "risk_adjusted_places": round(risk_adjusted_places),
            "confidence": confidence,
            "warnings": warnings,
            "lodged_weight": LODGED_RISK_WEIGHT,
        },
    }
