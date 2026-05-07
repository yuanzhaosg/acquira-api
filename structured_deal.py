import hashlib
import re
from typing import Any

from narrative_guard import build_narrative_guard


BLOCKED_VALUATION_MESSAGE = (
    "Valuation unavailable until P&L, payroll, and occupancy history are verified."
)
EXTRACTOR_VERSION = "document-extraction-hardening-20260507"
PROMPT_VERSION = "extraction-system-v20260507"
VALUATION_REQUIRED_FIELDS = {"revenue", "ebitda", "normalised_ebitda", "payroll_labour_cost", "rent_pa", "avg_4wk_occupancy_pct", "avg_13wk_occupancy_pct", "latest_week_occupancy_pct"}
CANONICAL_FIELD_ALIASES = {
    "revenue": ["revenue"],
    "ebitda": ["ebitda", "operating_profit"],
    "normalised_ebitda": ["normalised_ebitda", "normalized_ebitda"],
    "payroll_labour_cost": ["payroll_labour_cost", "labour_cost", "labor_cost", "wages_salaries"],
    "rent": ["rent_pa", "rent", "lease_rent"],
    "current_occupancy": ["current_occupancy_pct", "current_month_occupancy_pct"],
    "monthly_occupancy": ["monthly_avg_occupancy_pct", "fy25_avg_occupancy_pct"],
    "avg_4wk_occupancy": ["avg_4wk_occupancy_pct"],
    "avg_13wk_occupancy": ["avg_13wk_occupancy_pct"],
    "licensed_places": ["licensed_places"],
    "asking_price": ["asking_price", "price_guide"],
    "lease_expiry": ["lease_expiry", "lease_term", "lease_remaining_term"],
}
UNDERWRITING_USE_RANK = {"accepted": 4, "review_required": 3, "blocked": 2, "excluded": 1}
SOURCE_QUALITY_RANK = {"authoritative": 5, "supporting": 4, "broker_summary": 3, "manual": 2, "unknown": 1, "template_or_forecast": 0}
TRUST_RANK = {"high": 5, "medium": 4, "low": 3, "unknown": 2, "disputed": 1}


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    if not isinstance(parent.get(key), dict):
        parent[key] = {}
    return parent[key]


def _get(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current

def _market_audit_shell(market_audit: dict[str, Any] | None, pipeline_audit: dict[str, Any] | None) -> dict[str, Any]:
    market_audit = _as_dict(market_audit)
    pipeline_audit = _as_dict(pipeline_audit)
    required = {
        "catchment radius": _get(market_audit or {}, "catchment_radius_km"),
        "kids 0-4 count": _get(market_audit or {}, "kids_0_4", "value"),
        "kids 0-4 source": _get(market_audit or {}, "kids_0_4", "source"),
        "LDC utilisation assumption": _get(market_audit or {}, "ldc_utilisation_rate", "value"),
        "centre licensed places": _get(market_audit or {}, "licensed_places", "value"),
        "competitor count": _get(market_audit or {}, "competitor_count", "value") or _get(market_audit or {}, "competitor_supply", "competitor_count"),
        "competitor licensed places": _get(market_audit or {}, "competitor_supply", "total_licensed_places"),
        "geocode method": _get(market_audit or {}, "competitor_supply", "target_geocode_method"),
        "exclusion method": _get(market_audit or {}, "competitor_supply", "exclusion_method"),
        "postcode fallback comparison": _get(market_audit or {}, "competitor_supply", "compared_to_postcode"),
        "pipeline projects count": _get(pipeline_audit or {}, "approved_places"),
        "approved/under construction places": _get(pipeline_audit or {}, "approved_places"),
        "lodged risk-adjusted places": _get(pipeline_audit or {}, "risk_adjusted_places"),
        "EDR formula and result": _get(market_audit or {}, "edr", "value"),
    }
    missing = [label for label, value in required.items() if not _present(value)]
    audit = dict(market_audit)
    audit.setdefault("warnings", [])
    if not market_audit:
        audit["warnings"].append("Market audit inputs were not returned; render missing state and request demographic, competitor, geocode, and pipeline evidence.")
    if not _get(audit, "competitor_supply", "source"):
        audit["warnings"].append("Geospatial competitor data is unavailable; use postcode fallback only as a warning-level comparison.")
    audit["missing_fields"] = missing
    audit["status"] = "complete" if not missing else "missing" if not market_audit else "partial"
    audit["warnings"] = _sanitize_warning_list(audit.get("warnings") or [])
    if isinstance(audit.get("competitor_supply"), dict):
        supply = audit["competitor_supply"]
        supply["warnings"] = _sanitize_warning_list(supply.get("warnings") or [])
        if supply.get("source") in {None, "unavailable"} and supply.get("confidence") in {None, "low"}:
            if (supply.get("competitor_count") in {0, None}) and supply.get("compared_to_postcode"):
                supply["competitor_count"] = None
                supply["total_licensed_places"] = None
                supply["source"] = "unavailable"
                supply["warnings"].append(
                    "Geospatial competitor supply was unavailable. Postcode fallback was retained for scoring comparison and should be reviewed."
                )
    return audit


def _sanitize_warning(message: Any) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if not text:
        return ""
    if re.search(r"\b(42703|column .* does not exist|postgres|supabase|rpc|schema cache|PGRST|KeyError|Traceback|Exception|\{.*\})\b", text, re.IGNORECASE):
        if re.search(r"competitor|acecqa|geospatial|service_approval", text, re.IGNORECASE):
            return "Competitor lookup failed due to market-data configuration. Postcode fallback was used; verify competitor methodology before relying on market score."
        return "A data provider lookup failed. Review methodology before relying on this section."
    return text


def _sanitize_warning_list(messages: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for message in messages:
        clean = _sanitize_warning(message)
        if clean and clean.lower() not in seen:
            output.append(clean)
            seen.add(clean.lower())
    return output

def _vision_failure_pages(combined_text: str) -> list[int]:
    pages: list[int] = []
    for match in re.finditer(r"--- Page (\d+) ---\n([\s\S]*?)(?=\n--- Page \d+ ---|\Z)", combined_text or ""):
        block = match.group(2)
        if "VISION_EXTRACTION_ERROR:" in block:
            pages.append(int(match.group(1)))
    return sorted(set(pages))


def _source_for_field(field: str, source_files: list[str], file_classes: dict[str, str]) -> str:
    if not source_files:
        return "extracted output"

    preferred_classes: dict[str, list[str]] = {
        "revenue": ["pl_excel", "im_pdf", "im_docx"],
        "ebitda": ["pl_excel", "im_pdf", "im_docx"],
        "payroll_labour_cost": ["payroll_excel", "pl_excel", "im_pdf", "im_docx"],
        "occupancy_current": ["occupancy_excel", "im_pdf", "im_docx"],
        "occupancy_history": ["occupancy_excel", "im_pdf", "im_docx"],
        "lease_expiry": ["lease_pdf", "lease_docx", "im_pdf", "im_docx"],
        "rent_pa": ["lease_pdf", "lease_docx", "pl_excel", "im_pdf", "im_docx"],
        "licensed_places": ["service_approval_pdf", "im_pdf", "im_docx"],
        "nqs_rating": ["nqs_pdf", "service_approval_pdf", "im_pdf", "im_docx"],
        "asking_price": ["manual_user_note", "im_pdf", "im_docx"],
    }

    for cls in preferred_classes.get(field, []):
        for filename in source_files:
            if file_classes.get(filename) == cls:
                return filename
    return source_files[0]


def _confidence(value: Any, data_quality: str | None = None) -> str:
    if not _present(value):
        return "missing"
    if data_quality and "HIGH" in data_quality.upper():
        return "high"
    return "medium"


def _source_quality(source_label: str, extraction_method: str) -> str:
    text = f"{source_label} {extraction_method}".lower()
    if "manual_user_note" in text:
        return "manual"
    if re.search(r"\b(template|forecast|budget|model|pro[-\s]?forma|scenario|assumption)\b", text):
        return "template_or_forecast"
    if extraction_method.startswith("excel_digest:derived"):
        return "authoritative"
    if re.search(r"\b(adjusted actuals|actuals|myob|xero|management accounts|profit and loss|p&l|pay run|payroll|workedhours|employment hero|utilisation|utilization|occupancy|xplor|lease|rent ledger|service approval|nqs)\b", text):
        return "authoritative"
    if re.search(r"\b(im_pdf|im_docx|information memorandum|broker|brochure|teaser|summary)\b", text):
        return "broker_summary"
    if re.search(r"\b(roster|floor plan|vendor|schedule|supplemental|approval)\b", text):
        return "supporting"
    return "unknown"


def _source_type(source_label: str, extraction_method: str) -> str:
    method = (extraction_method or "").lower()
    if method.startswith("excel_digest:derived"):
        return "workbook_derived"
    if method == "manual_user_note":
        return "manual_context"
    if "pdf_vision" in method:
        return "pdf_vision"
    if method.startswith("regex:supplemental_identity") or re.search(r"\b(approval|lease|nqs|service_approval)\b", f"{source_label} {method}".lower()):
        return "supplemental_doc"
    if "excel" in method or "workbook" in method or re.search(r"\.xlsx|\.xls|sheet:", source_label.lower()):
        return "excel_cell"
    if "pdf" in method:
        return "pdf_text"
    if method.startswith("regex:"):
        return "pdf_text"
    return "system_derived" if method.startswith("system") else "pdf_text"


def _provenance(value: Any, source_type: str) -> str:
    if not _present(value):
        return "missing"
    if source_type in {"workbook_derived", "system_derived", "market_model"}:
        return "derived"
    if source_type == "manual_context":
        return "manual_context"
    return "found"


MONTH_RE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"


def _infer_period_context(text: str, field: str, source_label: str = "") -> dict[str, Any]:
    haystack = f"{source_label}\n{text or ''}"
    lower = haystack.lower()
    if re.search(r"\b(forecast|budget|model|pro[-\s]?forma|scenario|assumption|template)\b", lower):
        return {
            "coverage_status": "partial" if "template" not in lower else "unknown",
            "coverage_reason": "Forecast/template/model source detected; not accepted as actual historical period evidence.",
            "period_label": "forecast/template",
            "observation_count": None,
        }
    fy_match = re.search(r"\b(?:fy|financial year)\s*['-]?(20)?(\d{2})\b", lower)
    if fy_match or re.search(r"\b(full\s+year|annual|12\s+months?)\b", lower):
        fy = f"FY20{fy_match.group(2)}" if fy_match else None
        return {
            "coverage_status": "complete",
            "coverage_reason": "Annual/full-year period label detected.",
            "fiscal_year": fy,
            "period_label": fy or "annual actuals",
            "observation_count": None,
        }
    if re.search(r"\b(ytd|year\s+to\s+date)\b", lower):
        return {
            "coverage_status": "partial",
            "coverage_reason": "YTD financial period detected; annualisation or reconciliation required.",
            "period_label": "YTD actuals",
            "observation_count": None,
        }
    pay_range = re.search(r"\b(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)\s*[-–—]\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b", lower)
    if pay_range:
        return {
            "coverage_status": "partial",
            "coverage_reason": f"Pay-run/date range detected ({pay_range.group(0)}); full-year reconciliation is required.",
            "period_label": pay_range.group(0),
            "observation_count": None,
        }
    month_tokens = re.findall(rf"\b{MONTH_RE}[-\s']?\d{{2,4}}\b|\b{MONTH_RE}\b", lower)
    unique_months = list(dict.fromkeys(month_tokens))
    if len(unique_months) >= 11:
        return {
            "coverage_status": "complete",
            "coverage_reason": "Monthly columns appear to cover approximately a full year.",
            "period_label": "monthly actuals full-year coverage",
            "observation_count": len(unique_months),
        }
    if len(unique_months) >= 2:
        return {
            "coverage_status": "partial",
            "coverage_reason": f"{len(unique_months)} monthly period markers detected; annualisation/reconciliation required.",
            "period_label": f"{len(unique_months)} monthly observations",
            "observation_count": len(unique_months),
        }
    if field in {"avg_4wk_occupancy_pct", "avg_13wk_occupancy_pct", "avg_52wk_occupancy_pct", "latest_week_occupancy_pct"}:
        week_count = len(re.findall(r"\b(?:week\s*(?:ending)?|w/e|weekly)\b", lower))
        if week_count:
            return {
                "coverage_status": "complete" if (field == "avg_4wk_occupancy_pct" and week_count >= 4) or (field == "avg_13wk_occupancy_pct" and week_count >= 13) else "partial",
                "coverage_reason": f"{week_count} weekly observations detected.",
                "period_label": f"{week_count} weekly observations",
                "observation_count": week_count,
            }
    return {
        "coverage_status": "unknown",
        "coverage_reason": "Period coverage was not fully established from the extracted evidence.",
        "period_label": None,
        "observation_count": None,
    }


def _period_for_fact(field: str, extraction_method: str, source_quality: str, period_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if period_context:
        return period_context
    coverage = "not_applicable"
    reason = None
    if field in {"revenue", "ebitda", "normalised_ebitda", "payroll_labour_cost", "rent_pa"}:
        coverage = "unknown"
        reason = "Financial period coverage was not fully established from the extracted evidence."
        if source_quality == "template_or_forecast":
            reason = "Template/forecast source is not accepted as actual historical period evidence."
    if field in {"avg_4wk_occupancy_pct", "avg_13wk_occupancy_pct", "avg_52wk_occupancy_pct", "latest_week_occupancy_pct"}:
        coverage = "complete" if extraction_method.startswith("excel_digest:derived_occupancy") else "unknown"
        reason = "Occupancy period count was derived from available utilisation rows." if coverage == "complete" else "Occupancy period coverage needs review."
    return {
        "coverage_status": coverage,
        "coverage_reason": reason,
    }


def _derivation_recipe(field: str, extraction_method: str, excerpt: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if not extraction_method.startswith("excel_digest:derived"):
        return None, None
    if field == "normalised_ebitda":
        return (
            "Normalised EBITDA = selected normalised EBITDA/add-back row from workbook digest.",
            {
                "included_lines": [excerpt] if excerpt else [],
                "excluded_lines": [],
                "assumptions": ["Workbook normalisation row is treated as candidate evidence and needs review."],
                "convention": "Use verified add-backs only before IC reliance.",
                "calculation_steps": ["Locate normalised EBITDA row.", "Select the total/latest numeric value exposed by the workbook digest."],
            },
        )
    if field in {"revenue", "ebitda", "payroll_labour_cost", "rent_pa"}:
        return (
            f"{field.replace('_', ' ')} = selected total/latest numeric row from workbook digest.",
            {
                "included_lines": [excerpt] if excerpt else [],
                "excluded_lines": [],
                "assumptions": ["Workbook row label is treated as the evidence convention; verify period coverage."],
                "convention": "Prefer actuals/management-account tabs over broker summaries.",
                "calculation_steps": ["Find labelled workbook row.", "Use the total/latest numeric value from the row."],
            },
        )
    if field.startswith("avg_"):
        weeks = "13" if "13" in field else "4" if "4" in field else "52"
        return (
            f"{weeks}-week occupancy average = average of latest {weeks} occupancy observations.",
            {
                "included_lines": [excerpt] if excerpt else [],
                "excluded_lines": [],
                "assumptions": ["Values are interpreted as utilisation/occupancy percentages."],
                "convention": "Use weekly observations from occupancy/utilisation exports where available.",
                "calculation_steps": [f"Collect latest {weeks} occupancy observations.", "Average observations."],
            },
        )
    return None, None


def _trust(value: Any, confidence: str, source_quality: str, provenance: str, conflicts: list[dict[str, Any]] | None = None) -> str:
    if not _present(value):
        return "unknown"
    if conflicts:
        return "disputed"
    if source_quality in {"manual", "template_or_forecast"} or provenance == "manual_context":
        return "low"
    if confidence == "high" and source_quality == "authoritative":
        return "high"
    if source_quality == "unknown":
        return "unknown"
    return "medium"


def _underwriting_use(field: str, provenance: str, trust: str, source_quality: str, value: Any, period: dict[str, Any] | None = None) -> str:
    if not _present(value):
        return "blocked" if field in VALUATION_REQUIRED_FIELDS else "excluded"
    if source_quality == "template_or_forecast":
        return "excluded"
    if provenance == "manual_context":
        return "review_required"
    coverage = (period or {}).get("coverage_status")
    if coverage == "partial" and field in VALUATION_REQUIRED_FIELDS:
        return "review_required"
    if coverage == "unknown" and field in VALUATION_REQUIRED_FIELDS:
        return "review_required"
    if trust == "high" or (trust == "medium" and source_quality == "authoritative" and coverage == "complete" and provenance == "found"):
        return "accepted"
    if trust in {"medium", "low", "disputed", "unknown"}:
        return "review_required"
    return "blocked"


FIELD_META: dict[str, dict[str, str | None]] = {
    "centre_name": {"category": "centre", "label": "Centre name", "unit": None},
    "trading_name": {"category": "centre", "label": "Trading name", "unit": None},
    "address": {"category": "centre", "label": "Address", "unit": None},
    "suburb": {"category": "centre", "label": "Suburb", "unit": None},
    "postcode": {"category": "centre", "label": "Postcode", "unit": None},
    "licensed_places": {"category": "centre", "label": "Licensed places", "unit": "places"},
    "nqs_rating": {"category": "regulatory", "label": "NQS rating", "unit": None},
    "current_occupancy_pct": {"category": "occupancy", "label": "Current occupancy / utilisation", "unit": "percent"},
    "latest_week_occupancy_pct": {"category": "occupancy", "label": "Latest week occupancy / utilisation", "unit": "percent"},
    "avg_4wk_occupancy_pct": {"category": "occupancy", "label": "4-week average occupancy", "unit": "percent"},
    "avg_13wk_occupancy_pct": {"category": "occupancy", "label": "13-week average occupancy", "unit": "percent"},
    "avg_52wk_occupancy_pct": {"category": "occupancy", "label": "52-week average occupancy", "unit": "percent"},
    "monthly_avg_occupancy_pct": {"category": "occupancy", "label": "Monthly average occupancy", "unit": "percent"},
    "fy23_avg_occupancy_pct": {"category": "occupancy", "label": "FY23 average occupancy", "unit": "percent"},
    "fy24_avg_occupancy_pct": {"category": "occupancy", "label": "FY24 average occupancy", "unit": "percent"},
    "fy25_avg_occupancy_pct": {"category": "occupancy", "label": "FY25 average occupancy", "unit": "percent"},
    "revenue": {"category": "financials", "label": "FY25 revenue", "unit": "aud"},
    "ebitda": {"category": "financials", "label": "FY25 EBITDA", "unit": "aud"},
    "normalised_ebitda": {"category": "financials", "label": "FY25 normalised EBITDA", "unit": "aud"},
    "payroll_labour_cost": {"category": "financials", "label": "FY25 payroll / labour cost", "unit": "aud"},
    "rent_pa": {"category": "lease", "label": "Annual rent", "unit": "aud"},
    "asking_price": {"category": "valuation", "label": "Asking price", "unit": "aud"},
    "lease_expiry": {"category": "lease", "label": "Lease expiry", "unit": "date"},
}


def _normalise_value(value: Any, unit: str | None) -> Any:
    if not _present(value):
        return None
    if unit in {"aud", "percent", "places"}:
        return value
    return value


def _stable_token(*parts: Any, length: int = 12) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def _fact_meta(field: str) -> dict[str, str | None]:
    if field.startswith("fee_daily"):
        return {"category": "fees", "label": "Daily fee", "unit": "aud"}
    if field.startswith("fee_weekly"):
        return {"category": "fees", "label": "Weekly fee", "unit": "aud"}
    return FIELD_META.get(field, {"category": "other", "label": field.replace("_", " ").title(), "unit": None})


def _add_fact(
    facts: list[dict[str, Any]],
    field: str,
    value: Any,
    source_label: str,
    confidence: str,
    extraction_method: str,
    evidence: list[dict[str, Any]],
    excerpt: str | None = None,
    page: int | None = None,
    blocker: bool = False,
    label_override: str | None = None,
    cell_range: str | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    period_context: dict[str, Any] | None = None,
) -> None:
    evidence_id = f"ev_{_stable_token(field, source_label, page, excerpt, value)}"
    fact_id = f"fact_{_stable_token(field, value, evidence_id)}"
    meta = _fact_meta(field)
    unit = meta.get("unit")
    source = {
        "label": source_label,
        "file": source_label,
        "page": page,
        "excerpt": excerpt,
        "evidence_id": evidence_id,
    }
    if cell_range:
        source["cell_range"] = cell_range
    source_type = _source_type(source_label, extraction_method)
    source_quality = _source_quality(source_label, extraction_method)
    provenance = _provenance(value, source_type)
    trust = _trust(value, confidence, source_quality, provenance, conflicts)
    period = _period_for_fact(field, extraction_method, source_quality, period_context)
    underwriting_use = _underwriting_use(field, provenance, trust, source_quality, value, period)
    derivation_formula, derivation_recipe = _derivation_recipe(field, extraction_method, excerpt)
    derivation_note = "Derived from workbook digest rows/cells; review against the original spreadsheet before IC reliance." if provenance == "derived" else None
    source_ref = {
        "file_name": source_label,
        "page": page,
        "cell_range": cell_range,
        "extraction_method": extraction_method,
        "excerpt": excerpt,
        "extractor_version": EXTRACTOR_VERSION,
        "prompt_version": PROMPT_VERSION,
    }
    reason = None
    next_action = None
    if underwriting_use == "excluded":
        reason = "Observed evidence is not suitable for underwriting use."
    elif underwriting_use == "review_required":
        reason = period.get("coverage_reason") or "Evidence exists, but source quality, extraction certainty, coverage, or conflicts require review."
    if trust == "disputed":
        next_action = "Resolve conflicting source values before relying on this field."
    elif field in {"avg_4wk_occupancy_pct", "avg_13wk_occupancy_pct"} and underwriting_use == "blocked":
        next_action = "Upload occupancy/utilisation export covering at least 13 weeks."
    elif underwriting_use == "review_required" and field == "asking_price" and source_type == "manual_context":
        reason = "User-provided asking price; verify against broker/vendor."
    if underwriting_use == "review_required" and field == "payroll_labour_cost" and period.get("coverage_status") in {"partial", "unknown"}:
        next_action = "Upload full FY payroll export or payroll summary matching the revenue period."
    if underwriting_use == "review_required" and field in {"revenue", "ebitda", "normalised_ebitda"} and period.get("coverage_status") in {"partial", "unknown"}:
        next_action = "Upload annual P&L or management accounts for the same period as payroll evidence."
    facts.append({
        "id": fact_id,
        "field": field,
        "category": meta.get("category"),
        "label": label_override or meta.get("label"),
        "value": value,
        "unit": unit,
        "normalized_value": _normalise_value(value, unit),
        "source": source,
        "source_label": source_label,
        "confidence": confidence,
        "status": "extracted" if confidence != "missing" else "needs_review",
        "blocker": blocker,
        "extraction_method": extraction_method,
        "source_type": source_type,
        "source_quality": source_quality,
        "provenance": provenance,
        "trust": trust,
        "underwriting_use": underwriting_use,
        "period": period,
        "source_refs": [source_ref],
        "derivation_note": derivation_note,
        "derivation_formula": derivation_formula,
        "derivation_recipe": derivation_recipe,
        "conflicts": conflicts or [],
        "reason": reason,
        "next_action": next_action,
        "extractor_version": EXTRACTOR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "evidence_id": evidence_id,
    })
    evidence.append({
        "id": evidence_id,
        "fact_id": fact_id,
        "field": field,
        "source_label": source_label,
        "source": source,
        "excerpt": excerpt,
        "confidence": confidence,
        "extraction_method": extraction_method,
        "source_type": source_type,
        "source_quality": source_quality,
        "provenance": provenance,
        "trust": trust,
        "underwriting_use": underwriting_use,
        "derivation_note": derivation_note,
        "derivation_formula": derivation_formula,
        "derivation_recipe": derivation_recipe,
        "source_refs": [source_ref],
    })
    if source.get("cell_range"):
        evidence[-1]["cell_range"] = source.get("cell_range")


def _iter_source_sections(combined_text: str) -> list[tuple[str, str, str]]:
    marker = re.compile(r"\n*===\s*(.*?)\s*\((.*?)\)\s*===\n")
    matches = list(marker.finditer(combined_text or ""))
    sections: list[tuple[str, str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(combined_text)
        sections.append((match.group(1).strip(), match.group(2).strip(), combined_text[start:end]))
    if not sections and combined_text:
        sections.append(("uploaded documents", "unknown", combined_text))
    return sections


def _iter_source_pages(combined_text: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    page_marker = re.compile(r"\n*---\s*Page\s+(\d+)\s*---\n", re.IGNORECASE)
    for source_label, source_class, text in _iter_source_sections(combined_text):
        matches = list(page_marker.finditer(text or ""))
        if not matches:
            pages.append({
                "source_label": source_label,
                "source_class": source_class,
                "page": None,
                "text": text,
            })
            continue
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            pages.append({
                "source_label": source_label,
                "source_class": source_class,
                "page": int(match.group(1)),
                "text": text[start:end],
            })
    return pages


def _line_windows(text: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    windows: list[str] = []
    for idx, line in enumerate(lines):
        windows.append(line)
        if idx + 1 < len(lines):
            windows.append(f"{line} {lines[idx + 1]}")
        if idx + 2 < len(lines):
            windows.append(f"{line} {lines[idx + 1]} {lines[idx + 2]}")
    return windows


def _parse_number(value: str) -> float | None:
    cleaned = re.sub(r"[,$\s]", "", value or "")
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return -parsed if negative else parsed


def _cell_values_from_line(line: str) -> list[float]:
    values: list[float] = []
    for cell in re.finditer(r"\b[A-Z]{1,3}\d+\s*=\s*([^|]+)", line or ""):
        raw = cell.group(1).strip()
        if re.search(r"[A-Za-z]", raw) and not re.search(r"\$\s*\d", raw):
            continue
        for number in re.findall(r"\(?-?\$?\s*\d[\d,]*(?:\.\d+)?\)?", raw):
            parsed = _parse_number(number)
            if parsed is not None:
                values.append(parsed)
    return values


def _cell_range_from_line(line: str) -> str | None:
    refs = re.findall(r"\b([A-Z]{1,3}\d+)\s*=", line or "")
    if not refs:
        return None
    return refs[0] if len(refs) == 1 else f"{refs[0]}:{refs[-1]}"


def _money_from_line(line: str, prefer: str = "largest_abs") -> float | int | None:
    values = _cell_values_from_line(line)
    if not values:
        values = [
            parsed for parsed in (
                _parse_number(match.group(0))
                for match in re.finditer(r"\(?-?\$?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?", line or "")
            )
            if parsed is not None
        ]
    values = [value for value in values if abs(value) >= 100]
    if not values:
        return None
    if prefer == "last":
        chosen = values[-1]
    elif prefer == "first":
        chosen = values[0]
    elif prefer == "positive_largest":
        positives = [value for value in values if value > 0]
        chosen = max(positives or values, key=abs)
    else:
        chosen = max(values, key=abs)
    return int(chosen) if float(chosen).is_integer() else round(chosen, 2)


def _similar_value(left: Any, right: Any) -> bool:
    try:
        l_val = float(left)
        r_val = float(right)
    except (TypeError, ValueError):
        return str(left).strip().lower() == str(right).strip().lower()
    tolerance = max(1.0, abs(l_val) * 0.01)
    return abs(l_val - r_val) <= tolerance


def _context_excerpt(text: str, start: int, end: int, radius: int = 120) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[lo:hi]).strip()[:500]


def _find_source_hint(field: str, value: Any, combined_text: str) -> dict[str, Any]:
    if not _present(value):
        return {}
    value_patterns: list[str] = []
    if isinstance(value, (int, float)):
        value_patterns.extend([
            re.escape(str(int(value))) if float(value).is_integer() else re.escape(str(value)),
            f"{int(value):,}" if float(value).is_integer() else "",
        ])
    else:
        value_patterns.append(re.escape(str(value)))
    value_patterns = [p for p in value_patterns if p]
    if not value_patterns:
        return {}

    keyword_map = {
        "centre_name": [r"centre", r"name"],
        "trading_name": [r"trading", r"name"],
        "address": [r"address"],
        "suburb": [r"suburb"],
        "postcode": [r"postcode", r"post\s*code"],
        "licensed_places": [r"licensed", r"capacity", r"places", r"approved"],
        "nqs_rating": [r"nqs", r"rating"],
        "current_occupancy_pct": [r"current", r"utili[sz]ation", r"occupancy"],
        "latest_week_occupancy_pct": [r"latest", r"week", r"utili[sz]ation", r"occupancy"],
        "avg_4wk_occupancy_pct": [r"4", r"week", r"utili[sz]ation", r"occupancy"],
        "avg_13wk_occupancy_pct": [r"13", r"week", r"utili[sz]ation", r"occupancy"],
        "avg_52wk_occupancy_pct": [r"52", r"week", r"utili[sz]ation", r"occupancy"],
        "revenue": [r"revenue"],
        "ebitda": [r"ebitda"],
        "payroll_labour_cost": [r"payroll", r"labou?r", r"wages"],
        "rent_pa": [r"rent"],
        "asking_price": [r"asking", r"price"],
        "lease_expiry": [r"expiry", r"lease"],
    }
    keywords = keyword_map.get(field, [field.replace("_", r"\s+")])
    value_re = re.compile("|".join(value_patterns), re.IGNORECASE)

    best_hint: dict[str, Any] = {}
    for page in _iter_source_pages(combined_text):
        text = page["text"]
        for match in value_re.finditer(text):
            excerpt = _context_excerpt(text, match.start(), match.end())
            if any(re.search(keyword, excerpt, re.IGNORECASE) for keyword in keywords):
                return {
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "excerpt": excerpt,
                }
            if not best_hint:
                best_hint = {
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "excerpt": excerpt,
                }
    return best_hint


def extract_fee_facts_from_text(combined_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int | None]] = set()

    # Capture common IM table/text patterns:
    #   Daily fee 0-2 $185
    #   3-5 years: $162 per day
    #   Weekly fee $650
    #   CURRENT FEES Daily $135 to $150 / Weekly $675-$750
    amount_pattern = (
        r"\$?\s*(?P<amount>\d{2,4}(?:,\d{3})?(?:\.\d{1,2})?)"
        r"(?:\s*(?:[-–—]|to)\s*\$?\s*(?P<amount_high>\d{2,4}(?:,\d{3})?(?:\.\d{1,2})?))?"
    )
    fee_patterns = [
        re.compile(
            r"(?P<label>(?:daily|weekly)\s+fees?|(?:daily|weekly)\s+fee|"
            r"fees?\s+(?:per\s+)?(?:day|week)|centre\s+fees?\s+(?:per\s+)?(?:day|week)|"
            r"(?:0\s*[-–—]\s*2|2\s*[-–—]\s*3|3\s*[-–—]\s*5|nursery|toddler|kindergarten|preschool)"
            r".{0,50}?(?:fee|rate)?)"
            r".{0,80}?"
            + amount_pattern
            + r"\s*(?P<period>/?\s*(?:per\s+)?(?:day|week|daily|weekly))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?P<label>(?:nursery|toddler|kindergarten|preschool|0\s*[-–—]\s*2|2\s*[-–—]\s*3|3\s*[-–—]\s*5))"
            r".{0,40}?"
            + amount_pattern
            + r"\s*(?P<period>/?\s*(?:per\s+)?(?:day|week|daily|weekly))",
            re.IGNORECASE,
        ),
    ]

    for page in _iter_source_pages(combined_text):
        source_label = page["source_label"]
        source_class = page["source_class"]
        page_num = page["page"]
        for line in _line_windows(page["text"]):
            line_l = line.lower()
            if not any(token in line_l for token in ["fee", "rate", "daily", "weekly", "per day", "per week"]):
                continue
            for pattern in fee_patterns:
                matches = list(pattern.finditer(line))
                if matches:
                    break
            else:
                matches = []
            for match in matches:
                label = re.sub(r"\s+", " ", match.group("label") or "").strip(" :-–—").lower()
                amount = match.group("amount").replace(",", "")
                amount_high = (match.group("amount_high") or "").replace(",", "")
                period_raw = (match.group("period") or "").lower()
                local_context = line[max(0, match.start() - 40):min(len(line), match.end() + 40)].lower()
                if "weekly" in label:
                    period = "weekly"
                elif "daily" in label:
                    period = "daily"
                elif "week" in period_raw:
                    period = "weekly"
                elif "day" in period_raw or "per day" in local_context:
                    period = "daily"
                else:
                    period = "weekly" if "per week" in local_context else "daily"
                if not label and "fee" not in line_l and "rate" not in line_l:
                    continue
                value: int | float | str
                if amount_high:
                    value = f"${amount}-${amount_high}"
                else:
                    value = float(amount) if "." in amount else int(amount)
                key = (label, str(value), period, page_num)
                if key in seen:
                    continue
                seen.add(key)
                facts.append({
                    "field": f"fee_{period}_{len(facts) + 1}",
                    "value": value,
                    "source_label": source_label,
                    "page": page_num,
                    "confidence": "medium",
                    "extraction_method": f"regex:{source_class}",
                    "excerpt": line.strip()[:500],
                    "period": period,
                    "label": label or f"{period} fee",
                })
                if len(facts) >= 12:
                    return facts
    return facts


def extract_occupancy_facts_from_text(combined_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, float, int | None]] = set()
    blocked_context = re.compile(
        r"\b(stabili[sz]ed|stabilised|model(?:led)?|upside|target|forecast|pro\s*forma|assumption|maturity|potential)\b",
        re.IGNORECASE,
    )
    patterns = [
        (
            "current_occupancy_pct",
            re.compile(r"\b(?:current|present|now|today)\s+(?:utili[sz]ation|occupancy)\b.{0,70}?(?P<value>\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE),
            "Current occupancy / utilisation",
            "regex:occupancy_current",
        ),
        (
            "latest_week_occupancy_pct",
            re.compile(r"\b(?:latest|most recent)\s+(?:week|weekly)\s+(?:utili[sz]ation|occupancy)\b.{0,70}?(?P<value>\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE),
            "Latest week occupancy / utilisation",
            "regex:occupancy_latest_week",
        ),
        (
            "avg_4wk_occupancy_pct",
            re.compile(r"\b(?:4|four)[-\s]*(?:week|wk)\s+(?:average|avg)?\s*(?:utili[sz]ation|occupancy)?\b.{0,70}?(?P<value>\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE),
            "4-week average occupancy",
            "regex:occupancy_4wk",
        ),
        (
            "avg_13wk_occupancy_pct",
            re.compile(r"\b(?:13|thirteen)[-\s]*(?:week|wk)\s+(?:average|avg)?\s*(?:utili[sz]ation|occupancy)?\b.{0,70}?(?P<value>\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE),
            "13-week average occupancy",
            "regex:occupancy_13wk",
        ),
        (
            "avg_52wk_occupancy_pct",
            re.compile(r"\b(?:52|fifty two)[-\s]*(?:week|wk)\s+(?:average|avg)?\s*(?:utili[sz]ation|occupancy)?\b.{0,70}?(?P<value>\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE),
            "52-week average occupancy",
            "regex:occupancy_52wk",
        ),
    ]
    for page in _iter_source_pages(combined_text):
        for line in _line_windows(page["text"]):
            if not re.search(r"\b(utili[sz]ation|occupancy)\b", line, re.IGNORECASE):
                continue
            if blocked_context.search(line):
                continue
            for field, pattern, label, method in patterns:
                match = pattern.search(line)
                if not match:
                    continue
                value = float(match.group("value"))
                if value > 100:
                    continue
                if value.is_integer():
                    value = int(value)
                key = (field, value, page["page"])
                if key in seen:
                    continue
                seen.add(key)
                facts.append({
                    "field": field,
                    "value": value,
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "medium",
                    "extraction_method": method,
                    "excerpt": line.strip()[:500],
                    "label": label,
                })
    derived = derive_occupancy_average_facts_from_text(combined_text)
    existing_fields = {fact.get("field") for fact in facts}
    for fact in derived:
        if fact.get("field") not in existing_fields:
            facts.append(fact)
            existing_fields.add(fact.get("field"))
    return facts


def derive_occupancy_average_facts_from_text(combined_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for page in _iter_source_pages(combined_text):
        text = page["text"]
        if not re.search(r"\b(utili[sz]ation|occupancy)\b", text, re.IGNORECASE):
            continue
        if not re.search(r"\b(SHEET:\s*(?:Output Occupancy|Utilisation XPLOR)|week|month|weekly|monthly)\b", text, re.IGNORECASE):
            continue
        values: list[float] = []
        lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
        for line in [line for line in lines if line]:
            line_l = line.lower()
            if not re.search(r"\b(utili[sz]ation|occupancy|week|month|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", line_l):
                continue
            for match in re.finditer(r"\b(\d{1,3}(?:\.\d+)?)\s*%", line):
                value = float(match.group(1))
                if 0 <= value <= 100:
                    values.append(value)
            for token in _cell_values_from_line(line):
                if 0.2 <= token <= 1.0:
                    values.append(token * 100)
                elif 20 <= token <= 100:
                    values.append(token)
        if len(values) < 4:
            continue
        excerpt = "Derived from occupancy/utilisation history rows in workbook digest; needs review against the original sheet."
        weekly_signal = bool(re.search(r"\b(week|weekly|w/e|week\s+ending)\b", text, re.IGNORECASE))
        monthly_signal = bool(re.search(rf"\b({MONTH_RE}|month|monthly)\b", text, re.IGNORECASE))
        monthly_values: list[float] = []
        if monthly_signal:
            month_pair_values = [
                float(match.group(1))
                for match in re.finditer(rf"\b{MONTH_RE}\b\s*(?:20\d{{2}})?\s*(\d{{1,3}}(?:\.\d+)?)\s*%", text, re.IGNORECASE)
            ]
            if len(month_pair_values) >= 4:
                monthly_values = [value for value in month_pair_values if 0 <= value <= 100]
            else:
                for line in lines:
                    if not re.search(rf"\b({MONTH_RE})\b", line, re.IGNORECASE):
                        continue
                    for match in re.finditer(r"\b(\d{1,3}(?:\.\d+)?)\s*%", line):
                        value = float(match.group(1))
                        if 0 <= value <= 100:
                            monthly_values.append(value)
        if weekly_signal:
            derived_specs = [
                ("avg_4wk_occupancy_pct", values[-4:], "Derived 4-week average occupancy"),
            ]
            if len(values) >= 13:
                derived_specs.append(("avg_13wk_occupancy_pct", values[-13:], "Derived 13-week average occupancy"))
        else:
            occupancy_values = monthly_values if len(monthly_values) >= 4 else values
            sample = occupancy_values[-min(len(occupancy_values), 12):]
            derived_specs = [("monthly_avg_occupancy_pct", sample, "Derived monthly average occupancy")]
            latest_month = occupancy_values[-1]
            if latest_month.is_integer():
                latest_month = int(latest_month)
            facts.append({
                "field": "current_occupancy_pct",
                "value": latest_month,
                "source_label": page["source_label"],
                "page": page["page"],
                "confidence": "medium",
                "extraction_method": "excel_digest:derived_occupancy_average",
                "excerpt": "Latest monthly occupancy observation from monthly utilisation history; needs review against source report.",
                "cell_range": _cell_range_from_line(text),
                "period": {
                    "coverage_status": "partial",
                    "coverage_reason": "Monthly occupancy history observed; latest month used as current occupancy proxy.",
                    "period_label": f"latest of {len(occupancy_values)} monthly occupancy observations",
                    "observation_count": len(occupancy_values),
                },
                "label": "Latest month occupancy / utilisation",
            })
        for field, sample, label in derived_specs:
            average = round(sum(sample) / len(sample), 1)
            if average.is_integer():
                average = int(average)
            facts.append({
                "field": field,
                "value": average,
                "source_label": page["source_label"],
                "page": page["page"],
                "confidence": "medium",
                "extraction_method": "excel_digest:derived_occupancy_average",
                "excerpt": excerpt,
                "cell_range": _cell_range_from_line(text),
                "period": {
                    "coverage_status": "complete",
                    "coverage_reason": f"{len(values)} occupancy observations found; {len(sample)} used for this average.",
                    "period_label": f"{len(sample)} of {len(values)} occupancy observations",
                    "observation_count": len(values),
                },
                "label": label,
            })
        if weekly_signal and len(values) < 13:
            facts.append({
                "field": "avg_13wk_occupancy_pct",
                "value": None,
                "source_label": page["source_label"],
                "page": page["page"],
                "confidence": "missing",
                "extraction_method": "excel_digest:derived_occupancy_average",
                "excerpt": f"Only {len(values)} weekly/monthly occupancy observations found; 13-week average requires 13.",
                "cell_range": _cell_range_from_line(text),
                "period": {
                    "coverage_status": "partial",
                    "coverage_reason": f"Only {len(values)} occupancy observations found; 13-week average requires 13.",
                    "period_label": f"{len(values)} occupancy observations",
                    "observation_count": len(values),
                },
                "label": "13-week average occupancy unavailable",
            })
        elif monthly_signal and not weekly_signal:
            for missing_field, label, required in [
                ("avg_4wk_occupancy_pct", "4-week average occupancy unavailable", "weekly occupancy observations"),
                ("avg_13wk_occupancy_pct", "13-week average occupancy unavailable", "13 weekly observations"),
            ]:
                facts.append({
                    "field": missing_field,
                    "value": None,
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "missing",
                    "extraction_method": "excel_digest:derived_occupancy_average",
                    "excerpt": f"Monthly occupancy observations found, but {label.lower()} requires {required}.",
                    "cell_range": _cell_range_from_line(text),
                    "period": {
                        "coverage_status": "partial",
                        "coverage_reason": f"Monthly occupancy observations found; {label.lower()} requires {required}.",
                        "period_label": f"{len(values)} monthly occupancy observations",
                        "observation_count": len(values),
                    },
                    "label": label,
                })
        break
    return facts


def _text_has_occupancy_history(combined_text: str) -> bool:
    occupancy_facts = extract_occupancy_facts_from_text(combined_text)
    history_fields = {
        "avg_4wk_occupancy_pct",
        "avg_13wk_occupancy_pct",
        "avg_52wk_occupancy_pct",
        "latest_week_occupancy_pct",
    }
    if sum(1 for fact in occupancy_facts if fact.get("field") in history_fields) >= 2:
        return True
    for page in _iter_source_pages(combined_text):
        text = page["text"]
        if re.search(r"\b(utili[sz]ation|occupancy)\b", text, re.IGNORECASE):
            pct_count = len(re.findall(r"\b\d{1,3}(?:\.\d+)?\s*%", text))
            decimal_pct_count = len(re.findall(r"\b0\.\d{2,4}\b", text))
            row_count = len(re.findall(r"\b(?:week|month|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", text, re.IGNORECASE))
            if (pct_count + decimal_pct_count) >= 3 and row_count >= 2:
                return True
    return False


def extract_financial_facts_from_text(combined_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    specs = [
        (
            "normalised_ebitda",
            re.compile(r"\b(?:normalised|normalized)\s+(?:ebitda|net\s+profit|np|profit)\b", re.IGNORECASE),
            "Normalised net profit / EBITDA proxy",
            "last",
        ),
        (
            "revenue",
            re.compile(r"\b(?:total\s+(?:income|revenue)|revenue|childcare\s+fee\s+income|fees?\s+income)\b", re.IGNORECASE),
            "Revenue",
            "positive_largest",
        ),
        (
            "payroll_labour_cost",
            re.compile(r"\b(?:total\s+employment\s+costs?|employment\s+costs?|wages?\s*(?:&|and)?\s*salaries|payroll|labou?r)\b", re.IGNORECASE),
            "Payroll / labour cost",
            "positive_largest",
        ),
        (
            "rent_pa",
            re.compile(r"\b(?:rent|occupancy\s+costs?)\b", re.IGNORECASE),
            "Rent",
            "positive_largest",
        ),
        (
            "ebitda",
            re.compile(r"\b(?:total\s+ebitda|ebitda|operating\s+profit|net\s+profit)\b", re.IGNORECASE),
            "EBITDA / profit",
            "last",
        ),
    ]
    seen: set[str] = set()
    for page in _iter_source_pages(combined_text):
        if "payroll_labour_cost" not in seen:
            wages_match = re.search(r"\bwages?\s*(?:&|and)?\s*salaries\b.{0,80}?\$?\s*(\d{2,3}(?:,\d{3})+|\d{5,8})(?:\.\d{1,2})?", page["text"], re.IGNORECASE)
            super_match = re.search(r"\bsuperannuation\b.{0,80}?\$?\s*(\d{2,3}(?:,\d{3})+|\d{5,8})(?:\.\d{1,2})?", page["text"], re.IGNORECASE)
            if wages_match and super_match:
                wages = int(wages_match.group(1).replace(",", ""))
                superannuation = int(super_match.group(1).replace(",", ""))
                facts.append({
                    "field": "payroll_labour_cost",
                    "value": wages + superannuation,
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "medium",
                    "extraction_method": "regex:wages_plus_super",
                    "excerpt": f"Wages & salaries {wages}; superannuation {superannuation}.",
                    "cell_range": None,
                    "period": _infer_period_context(page["text"], "payroll_labour_cost", page["source_label"]),
                    "label": "Payroll / labour cost (wages + super)",
                })
                seen.add("payroll_labour_cost")
        if "ebitda" not in seen:
            reported_match = re.search(r"\breported\s+net\s+profit\b.{0,50}?\$?\s*(\d{1,3}(?:,\d{3})+|\d{5,8})(?:\.\d{1,2})?", page["text"], re.IGNORECASE)
            if reported_match:
                facts.append({
                    "field": "ebitda",
                    "value": int(reported_match.group(1).replace(",", "")),
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "medium",
                    "extraction_method": "regex:reported_net_profit",
                    "excerpt": reported_match.group(0)[:500],
                    "cell_range": None,
                    "period": _infer_period_context(page["text"], "ebitda", page["source_label"]),
                    "label": "Reported net profit / EBITDA proxy",
                })
                seen.add("ebitda")
        if "normalised_ebitda" not in seen and not re.search(r"\b[A-Z]{1,3}\d+\s*=", page["text"]):
            normalised_match = re.search(r"\b(?:vendor\s+indicative\s+)?normalised\s+(?:net\s+profit|np|profit|ebitda)\b.{0,50}?\$?\s*(\d{1,3}(?:,\d{3})+|\d{5,8})(?:\.\d{1,2})?", page["text"], re.IGNORECASE)
            if normalised_match:
                facts.append({
                    "field": "normalised_ebitda",
                    "value": int(normalised_match.group(1).replace(",", "")),
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "medium",
                    "extraction_method": "regex:normalised_profit",
                    "excerpt": normalised_match.group(0)[:500],
                    "cell_range": None,
                    "period": _infer_period_context(page["text"], "normalised_ebitda", page["source_label"]),
                    "label": "Normalised net profit / EBITDA proxy",
                })
                seen.add("normalised_ebitda")
        for line in _line_windows(page["text"]):
            line_l = line.lower()
            if "manual_user_note" in line_l:
                continue
            for field, pattern, label, prefer in specs:
                if field in seen or not pattern.search(line):
                    continue
                value = _money_from_line(line, prefer)
                if value is None:
                    continue
                facts.append({
                    "field": field,
                    "value": value,
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "medium",
                    "extraction_method": "excel_digest:derived_financials",
                    "excerpt": line.strip()[:500],
                    "cell_range": _cell_range_from_line(line),
                    "period": _infer_period_context(line, field, page["source_label"]),
                    "label": label,
                })
                seen.add(field)
                break
        if {"revenue", "payroll_labour_cost", "rent_pa", "ebitda"}.issubset(seen):
            break
    return facts


def extract_identity_facts_from_text(combined_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    specs = [
        (
            "trading_name",
            re.compile(r"\b(?:trading\s+name|business\s+name|approved\s+provider\s+name)\b\s*[:\-]?\s*(?P<value>[A-Z0-9][A-Za-z0-9 &'().,\-]{2,120})", re.IGNORECASE),
            "Trading name",
        ),
        (
            "centre_name",
            re.compile(r"\b(?:service\s+name|centre\s+name|center\s+name)\b\s*[:\-]?\s*(?P<value>[A-Z0-9][A-Za-z0-9 &'().,\-]{2,120})", re.IGNORECASE),
            "Centre name",
        ),
        (
            "address",
            re.compile(r"\b(?:address|premises|property)\b\s*[:\-]?\s*(?P<value>\d{1,5}\s+[A-Za-z0-9 &'().,\-/]{5,160})", re.IGNORECASE),
            "Address",
        ),
        (
            "suburb",
            re.compile(r"\bsuburb\b\s*[:\-]?\s*(?P<value>[A-Za-z][A-Za-z '\-]{2,80})", re.IGNORECASE),
            "Suburb",
        ),
        (
            "postcode",
            re.compile(r"\b(?:postcode|post\s*code)\b\s*[:\-]?\s*(?P<value>\d{4})\b", re.IGNORECASE),
            "Postcode",
        ),
    ]
    seen: set[str] = set()
    for page in _iter_source_pages(combined_text):
        for line in _line_windows(page["text"]):
            for field, pattern, label in specs:
                if field in seen:
                    continue
                match = pattern.search(line)
                if not match:
                    continue
                value = re.sub(r"\s+", " ", match.group("value")).strip(" .,:;-")
                if field == "postcode" and not re.fullmatch(r"\d{4}", value):
                    continue
                facts.append({
                    "field": field,
                    "value": value,
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "medium",
                    "extraction_method": f"regex:supplemental_identity:{page['source_class']}",
                    "excerpt": line.strip()[:500],
                    "cell_range": _cell_range_from_line(line),
                    "label": label,
                })
                seen.add(field)
                break
    return facts


def extract_manual_context_facts_from_text(combined_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    pattern = re.compile(r"--- Manual Evidence \d+ ---\n([\s\S]*?)(?=\n--- Manual Evidence \d+ ---|\Z)", re.IGNORECASE)
    for idx, match in enumerate(pattern.finditer(combined_text or ""), start=1):
        block = match.group(1)
        note_match = re.search(r"NOTES:\s*(.*)", block)
        status_match = re.search(r"STATUS:\s*(.*)", block)
        question_match = re.search(r"QUESTION:\s*(.*)", block)
        value = (note_match.group(1).strip() if note_match else "") or (status_match.group(1).strip() if status_match else "")
        if not value:
            continue
        label = "Manual diligence context"
        question = question_match.group(1).strip() if question_match else ""
        asking_match = re.search(r"\basking[_\s-]*price\b\s*[:=\-]?\s*\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*([mk]|million|thousand)?\b", value, re.IGNORECASE)
        if asking_match:
            amount = float(asking_match.group(1).replace(",", ""))
            suffix = (asking_match.group(2) or "").lower()
            if suffix in {"m", "million"}:
                amount *= 1_000_000
            elif suffix in {"k", "thousand"}:
                amount *= 1_000
            facts.append({
                "field": "asking_price",
                "value": int(round(amount)),
                "source_label": "Manual diligence notes",
                "page": None,
                "confidence": "low",
                "extraction_method": "manual_user_note",
                "excerpt": block.strip()[:500],
                "label": "Asking price",
            })
        facts.append({
            "field": f"manual_context_{idx}",
            "value": value[:500],
            "source_label": "Manual diligence notes",
            "page": None,
            "confidence": "low",
            "extraction_method": "manual_user_note",
            "excerpt": block.strip()[:500],
            "label": label if not question else f"{label}: {question[:80]}",
        })
    return facts


def extract_missing_field_support_from_text(combined_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    patterns = [
        (
            "rent_pa",
            re.compile(r"\b(?:annual\s+rent|rent\s*(?:pa|p\.a\.|per\s+annum)|base\s+rent)\b.{0,80}?\$?\s*(?P<value>\d{2,3}(?:,\d{3})+|\d{5,7})(?:\.\d{1,2})?", re.IGNORECASE),
            "Annual rent",
            "aud",
        ),
        (
            "asking_price",
            re.compile(r"\b(?:asking\s+price|business\s+price|purchase\s+price|sale\s+price)\b.{0,80}?\$?\s*(?P<value>\d{2,3}(?:,\d{3})+|\d{5,8})(?:\.\d{1,2})?", re.IGNORECASE),
            "Asking price",
            "aud",
        ),
        (
            "licensed_places",
            re.compile(r"\b(?:licensed\s+(?:capacity|places)|approved\s+places|places\s+approved|capacity)\b.{0,80}?(?P<value>\d{2,4})\b", re.IGNORECASE),
            "Licensed places",
            "places",
        ),
    ]
    for page in _iter_source_pages(combined_text):
        for line in _line_windows(page["text"]):
            for field, pattern, label, _unit in patterns:
                match = pattern.search(line)
                if not match:
                    continue
                value = int(match.group("value").replace(",", ""))
                if field == "licensed_places" and not 5 <= value <= 500:
                    continue
                facts.append({
                    "field": field,
                    "value": value,
                    "source_label": page["source_label"],
                    "page": page["page"],
                    "confidence": "medium",
                    "extraction_method": f"regex:{field}",
                    "excerpt": line.strip()[:500],
                    "label": label,
                })
    deduped: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for fact in facts:
        if fact["field"] in seen_fields:
            continue
        seen_fields.add(fact["field"])
        deduped.append(fact)
    return deduped


def _has_occupancy_history(extracted: dict[str, Any], combined_text: str | None = None) -> bool:
    occupancy = _as_dict(extracted.get("occupancy"))
    history_fields = [
        "avg_4wk_pct",
        "avg_13wk_pct",
        "avg_52wk_pct",
        "fy23_avg_pct",
        "fy24_avg_pct",
        "fy25_avg_pct",
    ]
    if sum(1 for key in history_fields if _present(occupancy.get(key))) >= 2:
        return True
    return _text_has_occupancy_history(combined_text or "") if combined_text else False


def _best_fact_for_gate(facts: list[dict[str, Any]], fields: list[str]) -> dict[str, Any] | None:
    candidates = [
        fact for fact in facts
        if fact.get("field") in fields and _present(fact.get("value"))
    ]
    if not candidates:
        return None
    order = {"accepted": 0, "review_required": 1, "blocked": 2, "excluded": 3}
    return sorted(candidates, key=lambda fact: order.get(str(fact.get("underwriting_use")), 9))[0]


def _gate_support(facts: list[dict[str, Any]], fields: list[str]) -> tuple[bool, bool, dict[str, Any] | None]:
    fact = _best_fact_for_gate(facts, fields)
    if not fact:
        return False, False, None
    use = fact.get("underwriting_use")
    if use == "accepted":
        return True, False, fact
    if use == "review_required":
        return True, True, fact
    return False, False, fact


def build_valuation_gate(extracted: dict[str, Any], combined_text: str | None = None, extracted_facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    facts = extracted_facts or []
    revenue_supported, revenue_review, revenue_fact = _gate_support(facts, ["revenue"])
    ebitda_supported, ebitda_review, ebitda_fact = _gate_support(facts, ["ebitda", "normalised_ebitda"])
    labour_supported, labour_review, labour_fact = _gate_support(facts, ["payroll_labour_cost"])
    occupancy_supported, occupancy_review, occupancy_fact = _gate_support(facts, [
        "avg_4wk_occupancy_pct",
        "avg_13wk_occupancy_pct",
        "avg_52wk_occupancy_pct",
        "latest_week_occupancy_pct",
        "monthly_avg_occupancy_pct",
        "current_occupancy_pct",
        "fy23_avg_occupancy_pct",
        "fy24_avg_occupancy_pct",
        "fy25_avg_occupancy_pct",
    ])
    if not facts:
        fy25 = _as_dict(_get(extracted, "financials", "fy25"))
        ratios = _as_dict(extracted.get("key_ratios"))
        revenue_supported = _present(fy25.get("revenue") or ratios.get("revenue_fy25"))
        ebitda_supported = _present(fy25.get("ebitda") or fy25.get("normalised_ebitda") or ratios.get("ebitda_fy25") or ratios.get("ebitda_3yr_avg"))
        labour_supported = _present(fy25.get("total_labour_cost"))
        occupancy_supported = _has_occupancy_history(extracted, combined_text)
        revenue_review = ebitda_review = labour_review = occupancy_review = False

    required = {
        "revenue": revenue_supported,
        "ebitda": ebitda_supported,
        "payroll_labour_cost": labour_supported,
        "occupancy_history": occupancy_supported,
    }

    blockers = []
    if not required["revenue"]:
        blockers.append({
            "field": "revenue",
            "reason": "Revenue unavailable for underwriting",
            "required_evidence": "P&L or management accounts showing revenue.",
            "underwriting_use": revenue_fact.get("underwriting_use") if revenue_fact else "blocked",
        })
    if not required["ebitda"]:
        blockers.append({
            "field": "ebitda",
            "reason": "EBITDA unavailable for underwriting",
            "required_evidence": "P&L or normalised EBITDA bridge.",
            "underwriting_use": ebitda_fact.get("underwriting_use") if ebitda_fact else "blocked",
        })
    if not required["payroll_labour_cost"]:
        blockers.append({
            "field": "payroll_labour_cost",
            "reason": "Payroll/labour cost unavailable for underwriting",
            "required_evidence": "Payroll summary or P&L labour cost detail.",
            "underwriting_use": labour_fact.get("underwriting_use") if labour_fact else "blocked",
        })

    warnings = []
    if not occupancy_supported:
        blockers.append({
            "field": "occupancy_history",
            "reason": "Occupancy history unavailable for underwriting",
            "required_evidence": "Weekly or monthly occupancy history, preferably 4-week and 13-week averages.",
            "underwriting_use": occupancy_fact.get("underwriting_use") if occupancy_fact else "blocked",
        })
    for field, needs_review, fact in [
        ("revenue", revenue_review, revenue_fact),
        ("ebitda", ebitda_review, ebitda_fact),
        ("payroll_labour_cost", labour_review, labour_fact),
        ("occupancy_history", occupancy_review, occupancy_fact),
    ]:
        if needs_review:
            warnings.append(f"{field.replace('_', ' ')} is available but requires review before confident valuation.")

    if blockers:
        return {
            "status": "blocked",
            "reason": "Insufficient financial evidence",
            "message": BLOCKED_VALUATION_MESSAGE,
            "valuation_label": "illustrative_only",
            "can_show_confident_valuation": False,
            "required_evidence": required,
            "blockers": blockers,
            "warnings": warnings,
        }

    if warnings:
        return {
            "status": "needs_review",
            "reason": "Required evidence exists but needs review",
            "message": "Guarded valuation may be shown, but one or more required facts need partner review before IC reliance.",
            "valuation_label": "illustrative_only",
            "can_show_confident_valuation": False,
            "required_evidence": required,
            "blockers": [],
            "warnings": warnings,
        }

    return {
        "status": "pass",
        "reason": "Required financial evidence present",
        "message": "Core valuation inputs are present. Confirm source documents before IC use.",
        "valuation_label": "evidence_supported",
        "can_show_confident_valuation": True,
        "required_evidence": required,
        "blockers": [],
        "warnings": [],
    }


def build_deal_summary(extracted: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    centre = _as_dict(extracted.get("centre"))
    fy25 = _as_dict(_get(extracted, "financials", "fy25"))
    ratios = _as_dict(extracted.get("key_ratios"))
    occupancy = _as_dict(extracted.get("occupancy"))
    return {
        "centre_name": centre.get("name") or scored.get("centre_name"),
        "address": centre.get("address"),
        "suburb": centre.get("suburb"),
        "state": centre.get("state"),
        "licensed_places": centre.get("licensed_places") or ratios.get("licensed_places"),
        "total_score": scored.get("total_score") or scored.get("overall_score"),
        "verdict": scored.get("verdict"),
        "revenue": fy25.get("revenue") or ratios.get("revenue_fy25"),
        "ebitda": fy25.get("ebitda") or ratios.get("ebitda_fy25"),
        "labour_ratio_pct": fy25.get("labour_ratio_pct") or ratios.get("labour_ratio_fy25_pct"),
        "current_occupancy_pct": (
            occupancy.get("current_month_pct")
            or occupancy.get("latest_week_pct")
            or occupancy.get("avg_4wk_pct")
        ),
        "asking_price": _get(extracted, "financials", "asking_price") or ratios.get("asking_price") or scored.get("asking_price"),
    }


def build_risks(extracted: dict[str, Any], scored: dict[str, Any], valuation_gate: dict[str, Any]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for flag in _as_list(extracted.get("hard_flags")):
        if not isinstance(flag, dict):
            risks.append({
                "id": f"hard_flag_{len(risks) + 1}",
                "title": "Hard flag",
                "severity": "warning",
                "reason": str(flag),
                "source": "extracted.hard_flags",
            })
            continue
        risks.append({
            "id": flag.get("id") or f"hard_flag_{len(risks) + 1}",
            "title": flag.get("id") or "Hard flag",
            "severity": flag.get("severity") or "warning",
            "reason": flag.get("description"),
            "source": "extracted.hard_flags",
        })

    for flag in _as_list(_get(scored, "deal_breaker_flags", "flags")):
        if not isinstance(flag, dict):
            risks.append({
                "id": f"deal_breaker_{len(risks) + 1}",
                "title": "Deal-breaker flag",
                "severity": "warning",
                "reason": str(flag),
                "source": "scored.deal_breaker_flags",
            })
            continue
        if flag.get("triggered", True):
            risks.append({
                "id": flag.get("id") or f"deal_breaker_{len(risks) + 1}",
                "title": flag.get("label") or flag.get("id") or "Deal-breaker flag",
                "severity": flag.get("severity") or "warning",
                "reason": flag.get("reason"),
                "source": "scored.deal_breaker_flags",
            })

    if valuation_gate.get("status") == "blocked":
        risks.append({
            "id": "valuation_blocked",
            "title": "Valuation blocked",
            "severity": "high",
            "reason": valuation_gate.get("message"),
            "source": "valuation_gate",
        })
    return risks


def build_diligence_requests(
    scored: dict[str, Any],
    missing_fields: list[str],
    valuation_gate: dict[str, Any],
    extracted_facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    facts = extracted_facts or []
    facts_by_field: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        facts_by_field.setdefault(str(fact.get("field")), []).append(fact)

    def linked_for_fields(fields: list[str] | None) -> tuple[list[str], list[str]]:
        fact_ids: list[str] = []
        evidence_ids: list[str] = []
        for field in fields or []:
            candidates = facts_by_field.get(str(field), [])
            if field == "occupancy_history":
                candidates = [
                    fact for fact in facts
                    if str(fact.get("field", "")).startswith(("avg_", "latest_week"))
                ]
            for fact in candidates:
                if fact.get("id"):
                    fact_ids.append(str(fact["id"]))
                if fact.get("evidence_id"):
                    evidence_ids.append(str(fact["evidence_id"]))
        return list(dict.fromkeys(fact_ids)), list(dict.fromkeys(evidence_ids))

    def add(category: str, request: str, priority: str, source: str, linked_fields: list[str] | None = None) -> None:
        text = re.sub(r"\s+", " ", request or "").strip()
        if not text or text.lower() in seen:
            return
        if source == "evidence_ledger" and "occupancy/utilisation export" in text.lower():
            if any(item.get("category") == "occupancy" for item in requests):
                return
        seen.add(text.lower())
        linked_fact_ids, linked_evidence_ids = linked_for_fields(linked_fields)
        requests.append({
            "id": f"dd_{len(requests) + 1}",
            "category": category,
            "question": text,
            "request": text,
            "why_it_matters": "This evidence is needed to support deal underwriting.",
            "priority": priority,
            "status": "not_requested",
            "source": source,
            "linked_fact_ids": linked_fact_ids,
            "linked_evidence_ids": linked_evidence_ids,
            "linked_fields": linked_fields or [],
        })

    for blocker in _as_list(valuation_gate.get("blockers")):
        if not isinstance(blocker, dict):
            add("financials", str(blocker), "high", "valuation_gate")
            continue
        add(
            "financials",
            blocker.get("required_evidence") or blocker.get("reason"),
            "high",
            "valuation_gate",
            [blocker.get("field")],
        )

    next_steps = _as_dict(scored.get("next_steps"))
    for item in _as_list(next_steps.get("ask_broker_for")):
        add("broker", str(item), "high", "scored.next_steps.ask_broker_for")
    for item in _as_list(next_steps.get("due_diligence_priorities")):
        add("diligence", str(item), "medium", "scored.next_steps.due_diligence_priorities")

    missing_text = " ".join(str(field).lower() for field in missing_fields)
    grouped_missing_requests = [
        (
            ["latest_week_occupancy", "avg_4wk", "avg_13wk", "monthly_avg", "fy23_avg", "fy24_avg", "fy25_avg", "occupancy_history", "occupancy"],
            "occupancy",
            "Upload occupancy/utilisation export covering FY23, FY24, and the latest 13 weeks, preferably weekly and room-level.",
            "high",
        ),
        (
            ["lease_expiry", "lease_commencement", "lease_term", "lease", "rent_review", "make_good"],
            "lease",
            "Upload executed lease and any variations/options schedule confirming commencement, expiry, options, rent review, assignment, and make-good obligations.",
            "high",
        ),
        (
            ["normalised_ebitda", "normalized_ebitda", "addback", "add_back"],
            "financials",
            "Upload add-back / normalisation schedule showing owner salary, one-off costs, related-party expenses, and accepted/rejected adjustments.",
            "high",
        ),
        (
            ["payroll", "labour", "labor", "wages", "employment_cost"],
            "financials",
            "Upload payroll report or worked-hours export matching the revenue period to verify wages and staffing coverage.",
            "high",
        ),
        (
            ["asking_price", "price_guide", "purchase_price"],
            "valuation",
            "Confirm asking price or price guide.",
            "medium",
        ),
    ]
    matched_fields: set[str] = set()
    for markers, category, request, priority in grouped_missing_requests:
        linked = [str(field) for field in missing_fields if any(marker in str(field).lower() for marker in markers)]
        if linked or any(marker in missing_text for marker in markers):
            add(category, request, priority, "missing_fields_grouped", linked)
            matched_fields.update(linked)
    for field in missing_fields[:12]:
        if str(field) in matched_fields:
            continue
        label = str(field).replace("_", " ")
        if re.search(r"^[a-z0-9_]+$", str(field)) and "_" in str(field):
            add("missing_field", f"Upload source document or schedule supporting {label}.", "medium", "missing_fields", [str(field)])
        else:
            add("missing_field", str(field), "medium", "missing_fields", [str(field)])
    for fact in facts:
        if fact.get("next_action"):
            add("evidence_action", str(fact["next_action"]), "medium", "evidence_ledger", [str(fact.get("field"))])
    return requests


def build_extracted_facts(
    extracted: dict[str, Any],
    combined_text: str,
    source_files: list[str],
    file_classes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    data_quality = str(_get(extracted, "meta", "data_quality") or "")
    occupancy_text_facts = extract_occupancy_facts_from_text(combined_text)
    support_text_facts = extract_missing_field_support_from_text(combined_text)
    financial_text_facts = extract_financial_facts_from_text(combined_text)
    identity_text_facts = extract_identity_facts_from_text(combined_text)
    manual_text_facts = extract_manual_context_facts_from_text(combined_text)
    text_fact_by_field: dict[str, dict[str, Any]] = {}
    for fact in [*occupancy_text_facts, *support_text_facts, *financial_text_facts, *identity_text_facts]:
        text_fact_by_field.setdefault(fact["field"], fact)

    occupancy = _ensure_dict(extracted, "occupancy")
    if text_fact_by_field.get("current_occupancy_pct"):
        occupancy["current_month_pct"] = text_fact_by_field["current_occupancy_pct"]["value"]
    if text_fact_by_field.get("latest_week_occupancy_pct") and not _present(occupancy.get("latest_week_pct")):
        occupancy["latest_week_pct"] = text_fact_by_field["latest_week_occupancy_pct"]["value"]
    if text_fact_by_field.get("avg_4wk_occupancy_pct") and not _present(occupancy.get("avg_4wk_pct")):
        occupancy["avg_4wk_pct"] = text_fact_by_field["avg_4wk_occupancy_pct"]["value"]
    if text_fact_by_field.get("avg_13wk_occupancy_pct") and not _present(occupancy.get("avg_13wk_pct")):
        occupancy["avg_13wk_pct"] = text_fact_by_field["avg_13wk_occupancy_pct"]["value"]
    if text_fact_by_field.get("avg_52wk_occupancy_pct") and not _present(occupancy.get("avg_52wk_pct")):
        occupancy["avg_52wk_pct"] = text_fact_by_field["avg_52wk_occupancy_pct"]["value"]
    if text_fact_by_field.get("monthly_avg_occupancy_pct") and not _present(occupancy.get("monthly_avg_pct")):
        occupancy["monthly_avg_pct"] = text_fact_by_field["monthly_avg_occupancy_pct"]["value"]

    centre = _ensure_dict(extracted, "centre")
    for source_field, centre_key in [
        ("centre_name", "name"),
        ("trading_name", "trading_name"),
        ("address", "address"),
        ("suburb", "suburb"),
        ("postcode", "postcode"),
    ]:
        if text_fact_by_field.get(source_field) and not _present(centre.get(centre_key)):
            centre[centre_key] = text_fact_by_field[source_field]["value"]
    if text_fact_by_field.get("licensed_places") and not _present(centre.get("licensed_places")):
        centre["licensed_places"] = text_fact_by_field["licensed_places"]["value"]
    financials = _ensure_dict(extracted, "financials")
    fy25 = _ensure_dict(financials, "fy25")
    meta = extracted.setdefault("meta", {})
    if not isinstance(meta.get("workbook_derived_conflicts"), list):
        meta["workbook_derived_conflicts"] = []

    def apply_financial_text_fact(field: str, target_key: str) -> None:
        fact = text_fact_by_field.get(field)
        if not fact:
            return
        current = fy25.get(target_key)
        if _present(current) and not _similar_value(current, fact["value"]):
            meta["workbook_derived_conflicts"].append({
                "field": field,
                "existing_value": current,
                "workbook_derived_value": fact["value"],
                "source_label": fact.get("source_label"),
                "cell_range": fact.get("cell_range"),
                "excerpt": fact.get("excerpt"),
                "resolution": "workbook_derived_value_preferred_for_financial_reunderwriting",
            })
        if not _present(current) or not _similar_value(current, fact["value"]):
            fy25[target_key] = fact["value"]

    if text_fact_by_field.get("rent_pa") and not _present(fy25.get("rent_pa")):
        fy25["rent_pa"] = text_fact_by_field["rent_pa"]["value"]
    apply_financial_text_fact("revenue", "revenue")
    apply_financial_text_fact("payroll_labour_cost", "total_labour_cost")
    apply_financial_text_fact("ebitda", "ebitda")
    apply_financial_text_fact("normalised_ebitda", "normalised_ebitda")
    if text_fact_by_field.get("asking_price") and not _present(_get(extracted, "financials", "asking_price")):
        financials["asking_price"] = text_fact_by_field["asking_price"]["value"]

    field_specs = [
        ("centre_name", _get(extracted, "centre", "name"), "extracted_json"),
        ("trading_name", _get(extracted, "centre", "trading_name"), "extracted_json"),
        ("address", _get(extracted, "centre", "address"), "extracted_json"),
        ("suburb", _get(extracted, "centre", "suburb"), "extracted_json"),
        ("postcode", _get(extracted, "centre", "postcode"), "extracted_json"),
        ("licensed_places", _get(extracted, "centre", "licensed_places"), "extracted_json"),
        ("nqs_rating", _get(extracted, "centre", "nqs_rating"), "extracted_json"),
        ("current_occupancy_pct", _get(extracted, "occupancy", "current_month_pct"), "extracted_json"),
        ("latest_week_occupancy_pct", _get(extracted, "occupancy", "latest_week_pct"), "extracted_json"),
        ("avg_4wk_occupancy_pct", _get(extracted, "occupancy", "avg_4wk_pct"), "extracted_json"),
        ("avg_13wk_occupancy_pct", _get(extracted, "occupancy", "avg_13wk_pct"), "extracted_json"),
        ("avg_52wk_occupancy_pct", _get(extracted, "occupancy", "avg_52wk_pct"), "extracted_json"),
        ("monthly_avg_occupancy_pct", _get(extracted, "occupancy", "monthly_avg_pct"), "extracted_json"),
        ("fy23_avg_occupancy_pct", _get(extracted, "occupancy", "fy23_avg_pct"), "extracted_json"),
        ("fy24_avg_occupancy_pct", _get(extracted, "occupancy", "fy24_avg_pct"), "extracted_json"),
        ("fy25_avg_occupancy_pct", _get(extracted, "occupancy", "fy25_avg_pct"), "extracted_json"),
        ("revenue", _get(extracted, "financials", "fy25", "revenue") or _get(extracted, "key_ratios", "revenue_fy25"), "extracted_json"),
        ("ebitda", _get(extracted, "financials", "fy25", "ebitda") or _get(extracted, "key_ratios", "ebitda_fy25"), "extracted_json"),
        ("normalised_ebitda", _get(extracted, "financials", "fy25", "normalised_ebitda"), "extracted_json"),
        ("payroll_labour_cost", _get(extracted, "financials", "fy25", "total_labour_cost"), "extracted_json"),
        ("rent_pa", _get(extracted, "financials", "fy25", "rent_pa") or _get(extracted, "key_ratios", "rent_pa_fy25"), "extracted_json"),
        ("asking_price", _get(extracted, "financials", "asking_price") or _get(extracted, "key_ratios", "asking_price"), "extracted_json"),
        ("lease_expiry", _get(extracted, "lease", "expiry_date"), "extracted_json"),
    ]
    conflicts_by_field: dict[str, list[dict[str, Any]]] = {}
    for conflict in _as_list(_get(extracted, "meta", "workbook_derived_conflicts")):
        if isinstance(conflict, dict):
            conflicts_by_field.setdefault(str(conflict.get("field")), []).append({
                "value": conflict.get("existing_value"),
                "source_ref": {
                    "file_name": "prior extracted value",
                    "extraction_method": "extracted_json",
                    "excerpt": conflict.get("excerpt"),
                },
                "reason": "Existing value differed from workbook-derived evidence.",
            })

    for field, value, method in field_specs:
        hint = text_fact_by_field.get(field) or _find_source_hint(field, value, combined_text)
        source_label = hint.get("source_label") or _source_for_field(field, source_files, file_classes)
        confidence = _confidence(value, data_quality)
        _add_fact(
            facts,
            field,
            value,
            source_label,
            confidence,
            hint.get("extraction_method") or method,
            evidence,
            excerpt=hint.get("excerpt"),
            page=hint.get("page"),
            blocker=field in {"revenue", "ebitda", "payroll_labour_cost"} and confidence == "missing",
            label_override=hint.get("label"),
            cell_range=hint.get("cell_range"),
            conflicts=conflicts_by_field.get(field),
            period_context=hint.get("period") or _infer_period_context(hint.get("excerpt") or "", field, source_label),
        )

    for manual in manual_text_facts:
        _add_fact(
            facts,
            manual["field"],
            manual["value"],
            manual["source_label"],
            manual["confidence"],
            manual["extraction_method"],
            evidence,
            manual.get("excerpt"),
            page=manual.get("page"),
            label_override=manual.get("label"),
        )

    fee_facts = extract_fee_facts_from_text(combined_text)
    for fee in fee_facts:
        _add_fact(
            facts,
            fee["field"],
            fee["value"],
            fee["source_label"],
            fee["confidence"],
            fee["extraction_method"],
            evidence,
            fee.get("excerpt"),
            page=fee.get("page"),
            label_override=str(fee.get("label") or "").title() or None,
        )

    return facts, evidence, fee_facts


def build_missing_fields(extracted: dict[str, Any], extracted_facts: list[dict[str, Any]]) -> list[str]:
    raw_missing = _get(extracted, "meta", "missing_fields")
    missing = [raw_missing] if isinstance(raw_missing, str) else list(_as_list(raw_missing))
    for fact in extracted_facts:
        if fact.get("confidence") == "missing" and fact.get("field") not in missing:
            missing.append(fact["field"])

    has_fee_fact = any(str(f.get("field", "")).startswith("fee_") for f in extracted_facts)
    present_fields = {
        str(f.get("field"))
        for f in extracted_facts
        if f.get("confidence") != "missing" and _present(f.get("value"))
    }
    if has_fee_fact:
        missing = [
            f for f in missing
            if "fee" not in str(f).lower() and "pricing" not in str(f).lower()
        ]
    remove_when_present = {
        "rent_pa": ["rent", "lease_rent", "annual_rent"],
        "revenue": ["revenue", "income"],
        "ebitda": ["ebitda", "profit"],
        "normalised_ebitda": ["normalised_ebitda", "normalized_ebitda"],
        "payroll_labour_cost": ["payroll", "labour", "labor", "wages", "employment_cost"],
        "trading_name": ["trading_name", "trading name", "business_name"],
        "centre_name": ["centre_name", "center_name", "service_name"],
        "address": ["address", "property_address", "premises"],
        "suburb": ["suburb"],
        "postcode": ["postcode", "post_code", "post code"],
        "licensed_places": ["licensed_places", "licensed capacity", "capacity", "approved_places"],
        "asking_price": ["asking_price", "business_price", "purchase_price", "sale_price"],
        "current_occupancy_pct": ["current_occupancy", "current_utilisation", "current_utilization"],
        "latest_week_occupancy_pct": ["latest_week_occupancy", "latest_week_utilisation", "latest_week_utilization"],
        "avg_4wk_occupancy_pct": ["occupancy_history", "occupancy history", "4-week", "4 week"],
        "avg_13wk_occupancy_pct": ["occupancy_history", "occupancy history", "13-week", "13 week"],
    }
    for present_field, markers in remove_when_present.items():
        if present_field not in present_fields:
            continue
        filtered = []
        for field in missing:
            field_l = str(field).lower()
            should_remove = False
            for marker in markers:
                if marker == "rent":
                    should_remove = bool(re.search(r"(^|[_\s-])rent($|[_\s-])", field_l))
                else:
                    should_remove = marker in field_l
                if should_remove:
                    break
            if not should_remove:
                filtered.append(field)
        missing = filtered
    return missing


def remove_fee_missing_markers(extracted: dict[str, Any], fee_facts: list[dict[str, Any]]) -> None:
    if not fee_facts:
        return
    meta = extracted.setdefault("meta", {})
    missing = meta.get("missing_fields") or []
    meta["missing_fields"] = [
        f for f in missing
        if "fee" not in str(f).lower() and "pricing" not in str(f).lower()
    ]
    meta["missing_fields_count"] = len(meta["missing_fields"])


PARTNER_JUDGEMENT_PROMPTS = [
    {
        "id": "vendor_motivation",
        "question": "Why is the vendor selling?",
        "why_it_matters": "Vendor motivation shapes price tension, retention risk, and deal structure.",
        "category": "seller_context",
    },
    {
        "id": "landlord_relationship",
        "question": "Is the vendor related to the landlord/freeholder?",
        "why_it_matters": "Related-party rent may distort maintainable earnings and lease risk.",
        "category": "property",
    },
    {
        "id": "director_retention",
        "question": "Is the director staying post-sale?",
        "why_it_matters": "Director retention can materially affect continuity, occupancy, and staff stability.",
        "category": "staffing",
    },
    {
        "id": "key_staff_retention",
        "question": "Are key staff expected to remain?",
        "why_it_matters": "Staff retention is buyer judgement, not usually solved by document extraction.",
        "category": "staffing",
    },
    {
        "id": "regulatory_history",
        "question": "Any pending NQS reassessment, CCS audit, pause, or compliance issue in the last 24 months?",
        "why_it_matters": "Regulatory downside can override attractive financial metrics.",
        "category": "regulatory",
    },
    {
        "id": "local_pipeline",
        "question": "Any unlisted nearby competitors or DA/pipeline projects?",
        "why_it_matters": "Local intelligence can change supply-demand conclusions.",
        "category": "market",
    },
    {
        "id": "freehold_option",
        "question": "Is freehold available now or later?",
        "why_it_matters": "Freehold optionality can change strategy, capital structure, and lease risk.",
        "category": "property",
    },
    {
        "id": "related_party_adjustments",
        "question": "Any related-party expenses, rent adjustments, or owner salary normalisations?",
        "why_it_matters": "These determine whether normalised EBITDA is acceptable.",
        "category": "financials",
    },
]


def build_evidence_readiness(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {
        "accepted": [],
        "review_required": [],
        "disputed": [],
        "blocked": [],
        "excluded": [],
        "missing": [],
        "manual_context": [],
        "derived": [],
    }
    for fact in facts:
        summary = {
            "fact_id": fact.get("id"),
            "field": fact.get("field"),
            "label": fact.get("label"),
            "value": fact.get("value"),
            "provenance": fact.get("provenance"),
            "trust": fact.get("trust"),
            "underwriting_use": fact.get("underwriting_use"),
            "source_type": fact.get("source_type"),
            "source_quality": fact.get("source_quality"),
            "reason": fact.get("reason"),
            "next_action": fact.get("next_action"),
            "source_refs": fact.get("source_refs") or [],
        }
        if fact.get("provenance") == "manual_context":
            groups["manual_context"].append(summary)
        if fact.get("provenance") == "derived":
            groups["derived"].append(summary)
        if fact.get("trust") == "disputed":
            groups["disputed"].append(summary)
        use = str(fact.get("underwriting_use") or "")
        if use in groups:
            groups[use].append(summary)
        if fact.get("provenance") == "missing":
            groups["missing"].append(summary)
    return groups


def _canonical_fact_score(fact: dict[str, Any]) -> tuple[int, int, int, int, int]:
    use = str(fact.get("underwriting_use") or "")
    quality = str(fact.get("source_quality") or "unknown")
    trust = str(fact.get("trust") or "unknown")
    period = _as_dict(fact.get("period"))
    coverage = str(period.get("coverage_status") or "")
    coverage_rank = {"complete": 3, "not_applicable": 2, "partial": 1, "unknown": 0}.get(coverage, 0)
    has_value = 1 if _present(fact.get("value")) or _present(fact.get("normalized_value")) else 0
    return (
        UNDERWRITING_USE_RANK.get(use, 0),
        SOURCE_QUALITY_RANK.get(quality, 1),
        TRUST_RANK.get(trust, 2),
        coverage_rank,
        has_value,
    )


def _canonical_summary(fact: dict[str, Any], status: str | None = None, conflicts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "fact_id": fact.get("id"),
        "field": fact.get("field"),
        "label": fact.get("label"),
        "value": fact.get("value"),
        "normalized_value": fact.get("normalized_value"),
        "unit": fact.get("unit"),
        "provenance": fact.get("provenance"),
        "trust": "disputed" if status == "conflicting" else fact.get("trust"),
        "underwriting_use": fact.get("underwriting_use"),
        "source_type": fact.get("source_type"),
        "source_quality": fact.get("source_quality"),
        "period": fact.get("period"),
        "source_refs": fact.get("source_refs") or [],
        "derivation_formula": fact.get("derivation_formula"),
        "derivation_recipe": fact.get("derivation_recipe"),
        "reason": fact.get("reason"),
        "next_action": fact.get("next_action"),
        "status": status or fact.get("underwriting_use") or fact.get("provenance"),
        "conflicts": conflicts or fact.get("conflicts") or [],
    }


def build_canonical_facts(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for canonical_field, aliases in CANONICAL_FIELD_ALIASES.items():
        candidates = [
            fact for fact in facts
            if str(fact.get("field")) in aliases and fact.get("provenance") != "missing"
        ]
        if not candidates:
            continue
        non_excluded = [fact for fact in candidates if fact.get("underwriting_use") != "excluded"]
        selectable = non_excluded or candidates
        sorted_candidates = sorted(selectable, key=_canonical_fact_score, reverse=True)
        selected = sorted_candidates[0]
        conflict_candidates = [
            fact for fact in selectable
            if fact is not selected
            and _present(fact.get("value"))
            and _present(selected.get("value"))
            and str(fact.get("value")) != str(selected.get("value"))
        ]
        has_dispute = selected.get("trust") == "disputed" or any(f.get("trust") == "disputed" for f in selectable) or bool(conflict_candidates)
        conflicts = []
        for fact in conflict_candidates[:8]:
            conflicts.append({
                "fact_id": fact.get("id"),
                "value": fact.get("value"),
                "source_ref": (fact.get("source_refs") or [{}])[0],
                "source_type": fact.get("source_type"),
                "source_quality": fact.get("source_quality"),
                "trust": fact.get("trust"),
                "reason": fact.get("reason") or "Alternate source value differs from selected canonical fact.",
            })
        status = "conflicting" if has_dispute else None
        canonical[canonical_field] = _canonical_summary(selected, status, conflicts)
    return canonical


def build_valuation_gate_summary(valuation_gate: dict[str, Any], canonical_facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    field_map = {
        "revenue": ("Revenue", ["revenue"]),
        "ebitda": ("EBITDA / operating profit", ["ebitda", "normalised_ebitda"]),
        "payroll_labour_cost": ("Payroll / labour cost", ["payroll_labour_cost"]),
        "occupancy_history": ("Occupancy history", ["avg_13wk_occupancy", "avg_4wk_occupancy", "monthly_occupancy", "current_occupancy"]),
    }
    blocker_by_field = {
        str(blocker.get("field")): blocker
        for blocker in _as_list(valuation_gate.get("blockers"))
        if isinstance(blocker, dict)
    }
    rows: list[dict[str, Any]] = []
    for gate_field, (label, canonical_keys) in field_map.items():
        fact = next((canonical_facts.get(key) for key in canonical_keys if canonical_facts.get(key)), None)
        blocker = blocker_by_field.get(gate_field)
        observed = bool(fact and fact.get("provenance") != "missing")
        use = str(fact.get("underwriting_use") if fact else blocker.get("underwriting_use") if blocker else "blocked")
        if fact and fact.get("status") == "conflicting":
            use = "review_required"
        if blocker:
            use = str(blocker.get("underwriting_use") or use or "blocked")
        evidence_status = "found" if observed else "missing"
        rows.append({
            "field": gate_field,
            "label": label,
            "evidence": evidence_status,
            "underwriting_use": use or "blocked",
            "value": fact.get("value") if fact else None,
            "unit": fact.get("unit") if fact else None,
            "trust": "disputed" if fact and fact.get("status") == "conflicting" else fact.get("trust") if fact else "unknown",
            "source_quality": fact.get("source_quality") if fact else "unknown",
            "period": fact.get("period") if fact else None,
            "reason": (
                blocker.get("reason") if blocker else
                fact.get("reason") if fact else
                "No credible evidence found for this underwriting input."
            ),
            "next_action": blocker.get("required_evidence") if blocker else fact.get("next_action") if fact else None,
            "fact_id": fact.get("fact_id") if fact else None,
        })
    return {
        "status": valuation_gate.get("status"),
        "valuation_label": valuation_gate.get("valuation_label"),
        "can_show_confident_valuation": valuation_gate.get("can_show_confident_valuation"),
        "rows": rows,
    }


def build_evidence_quality_summary(
    facts: list[dict[str, Any]],
    valuation_gate: dict[str, Any],
    canonical_facts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key_facts = [
        canonical_facts.get("revenue"),
        canonical_facts.get("ebitda") or canonical_facts.get("normalised_ebitda"),
        canonical_facts.get("payroll_labour_cost"),
        canonical_facts.get("rent"),
        canonical_facts.get("avg_13wk_occupancy") or canonical_facts.get("avg_4wk_occupancy") or canonical_facts.get("current_occupancy"),
    ]
    key_facts = [fact for fact in key_facts if fact]
    uses = {str(fact.get("underwriting_use")) for fact in key_facts}
    trusts = {str(fact.get("trust")) for fact in key_facts}
    if valuation_gate.get("status") == "blocked" or "blocked" in uses:
        underwriting_reliability = "Blocked"
    elif "review_required" in uses or "disputed" in trusts or valuation_gate.get("status") == "needs_review":
        underwriting_reliability = "Review required"
    elif key_facts and all(fact.get("underwriting_use") == "accepted" for fact in key_facts):
        underwriting_reliability = "Accepted"
    else:
        underwriting_reliability = "Review required"

    if underwriting_reliability == "Accepted" and all(
        fact.get("trust") == "high" and fact.get("source_quality") == "authoritative"
        for fact in key_facts
    ):
        evidence_quality = "High"
    elif key_facts:
        evidence_quality = "Mixed"
    else:
        evidence_quality = "Low"

    extracted_count = len([fact for fact in facts if fact.get("provenance") in {"found", "derived"} and _present(fact.get("value"))])
    missing_count = len([fact for fact in facts if fact.get("provenance") == "missing"])
    extraction_completeness = "High" if extracted_count >= 6 and missing_count <= 2 else "Medium" if extracted_count >= 3 else "Low"
    return {
        "evidence_quality": evidence_quality,
        "underwriting_reliability": underwriting_reliability,
        "extraction_completeness": extraction_completeness,
        "reason": (
            "Key underwriting facts are accepted and authoritative."
            if underwriting_reliability == "Accepted"
            else "One or more key underwriting facts are missing, disputed, excluded, or require review."
        ),
    }


def build_structured_deal_intelligence(
    extracted: dict[str, Any],
    scored: dict[str, Any],
    combined_text: str,
    source_files: list[str],
    file_classes: dict[str, str],
) -> dict[str, Any]:
    extracted_facts, evidence, fee_facts = build_extracted_facts(
        extracted,
        combined_text,
        source_files,
        file_classes,
    )
    remove_fee_missing_markers(extracted, fee_facts)
    missing_fields = build_missing_fields(extracted, extracted_facts)
    valuation_gate = build_valuation_gate(extracted, combined_text, extracted_facts)
    if valuation_gate.get("required_evidence", {}).get("occupancy_history"):
        missing_fields = [
            field for field in missing_fields
            if "occupancy_history" not in str(field).lower()
            and "occupancy history" not in str(field).lower()
        ]
    canonical_facts = build_canonical_facts(extracted_facts)
    valuation_gate_summary = build_valuation_gate_summary(valuation_gate, canonical_facts)
    evidence_quality = build_evidence_quality_summary(extracted_facts, valuation_gate, canonical_facts)
    risks = build_risks(extracted, scored, valuation_gate)
    diligence_requests = build_diligence_requests(scored, missing_fields, valuation_gate, extracted_facts)
    extraction_warnings = []
    for blocker in _as_list(valuation_gate.get("blockers")):
        if not isinstance(blocker, dict):
            continue
        if blocker.get("field") == "occupancy_history":
            linked_facts = [
                f for f in extracted_facts
                if str(f.get("field", "")).startswith(("avg_", "latest_week"))
            ]
            extraction_warnings.append({
                "id": "occupancy_history_missing",
                "severity": "warning",
                "message": "Occupancy history is missing, so current utilisation should not be substituted with stabilised, peak, upside, or modelled occupancy.",
                "field": "occupancy_history",
                "linked_fact_ids": [f["id"] for f in linked_facts if f.get("id")],
                "linked_evidence_ids": [f["evidence_id"] for f in linked_facts if f.get("evidence_id")],
            })
    if fee_facts:
        fee_workflow_facts = [
            f for f in extracted_facts
            if str(f.get("field", "")).startswith("fee_")
        ]
        extraction_warnings.append({
            "id": "fee_data_found",
            "severity": "info",
            "message": "Daily or weekly fee data was detected in source documents and removed from missing fields.",
            "field": "fees",
            "linked_fact_ids": [f["id"] for f in fee_workflow_facts if f.get("id")],
            "linked_evidence_ids": [f["evidence_id"] for f in fee_workflow_facts if f.get("evidence_id")],
        })
    workbook_conflicts = _as_list(_get(extracted, "meta", "workbook_derived_conflicts"))
    if workbook_conflicts:
        extraction_warnings.append({
            "id": "workbook_financial_conflicts",
            "severity": "warning",
            "message": "Workbook-derived financial evidence conflicted with an existing extracted value; workbook evidence was preferred and should be reviewed.",
            "field": "financials",
            "conflicts": workbook_conflicts[:10],
            "linked_fact_ids": [
                f["id"] for f in extracted_facts
                if f.get("source_type") == "workbook_derived" and f.get("field") in {str(c.get("field")) for c in workbook_conflicts if isinstance(c, dict)}
            ],
            "linked_evidence_ids": [
                f["evidence_id"] for f in extracted_facts
                if f.get("source_type") == "workbook_derived" and f.get("field") in {str(c.get("field")) for c in workbook_conflicts if isinstance(c, dict)}
            ],
        })
    vision_failed_pages = _vision_failure_pages(combined_text)
    if vision_failed_pages:
        page_label = ", ".join(str(page) for page in vision_failed_pages[:8])
        extraction_warnings.append({
            "id": "pdf_vision_financial_pages_failed",
            "severity": "high",
            "message": (
                f"Financial/high-value pages were detected on pages {page_label}, but image-table extraction failed because "
                "the vision provider was not configured/authenticated or returned an error. Re-run with valid provider "
                "credentials or upload the P&L as Excel/CSV."
            ),
            "field": "financials",
            "linked_fact_ids": [],
            "linked_evidence_ids": [],
        })

    deal_summary = build_deal_summary(extracted, scored)
    pipeline_audit = _as_dict(scored.get("pipeline_audit") or extracted.get("_pipeline_audit"))
    market_audit = _market_audit_shell(scored.get("market_audit") or extracted.get("_market_audit"), pipeline_audit)
    narrative_guard = build_narrative_guard(
        extracted=extracted,
        scored=scored,
        deal_summary=deal_summary,
        missing_fields=missing_fields,
        valuation_gate=valuation_gate,
        pipeline_audit=pipeline_audit,
        market_audit=market_audit,
    )

    return {
        "deal_summary": deal_summary,
        "facts": extracted_facts,
        "extracted_facts": extracted_facts,
        "missing_fields": missing_fields,
        "risks": risks,
        "valuation_gate": valuation_gate,
        "valuation_gate_summary": valuation_gate_summary,
        "diligence_checklist": diligence_requests,
        "diligence_requests": diligence_requests,
        "extraction_warnings": extraction_warnings,
        "evidence": evidence,
        "evidence_ledger": extracted_facts,
        "canonical_facts": canonical_facts,
        "evidence_quality": evidence_quality,
        "evidence_readiness": build_evidence_readiness(extracted_facts),
        "partner_judgement_prompts": PARTNER_JUDGEMENT_PROMPTS,
        "narrative_guard": narrative_guard,
        "market_audit": market_audit,
        "pipeline_projects": scored.get("pipeline_projects") or extracted.get("_pipeline_projects") or [],
        "pipeline_audit": pipeline_audit,
    }
