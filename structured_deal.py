import hashlib
import re
from typing import Any

from narrative_guard import build_narrative_guard


BLOCKED_VALUATION_MESSAGE = (
    "Valuation unavailable until P&L, payroll, and occupancy history are verified."
)


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
    return audit

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
        "asking_price": ["im_pdf", "im_docx"],
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


FIELD_META: dict[str, dict[str, str | None]] = {
    "centre_name": {"category": "centre", "label": "Centre name", "unit": None},
    "address": {"category": "centre", "label": "Address", "unit": None},
    "licensed_places": {"category": "centre", "label": "Licensed places", "unit": "places"},
    "nqs_rating": {"category": "regulatory", "label": "NQS rating", "unit": None},
    "current_occupancy_pct": {"category": "occupancy", "label": "Current occupancy / utilisation", "unit": "percent"},
    "latest_week_occupancy_pct": {"category": "occupancy", "label": "Latest week occupancy / utilisation", "unit": "percent"},
    "avg_4wk_occupancy_pct": {"category": "occupancy", "label": "4-week average occupancy", "unit": "percent"},
    "avg_13wk_occupancy_pct": {"category": "occupancy", "label": "13-week average occupancy", "unit": "percent"},
    "avg_52wk_occupancy_pct": {"category": "occupancy", "label": "52-week average occupancy", "unit": "percent"},
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
    })


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


def _money_from_line(line: str, prefer: str = "largest_abs") -> float | int | None:
    values = _cell_values_from_line(line)
    if not values:
        values = [
            parsed for parsed in (
                _parse_number(match.group(0))
                for match in re.finditer(r"\(?-?\$?\s*\d{2,3}(?:,\d{3})+(?:\.\d+)?\)?", line or "")
            )
            if parsed is not None
        ]
    values = [value for value in values if abs(value) >= 100]
    if not values:
        return None
    if prefer == "last":
        chosen = values[-1]
    elif prefer == "positive_largest":
        positives = [value for value in values if value > 0]
        chosen = max(positives or values, key=abs)
    else:
        chosen = max(values, key=abs)
    return int(chosen) if float(chosen).is_integer() else round(chosen, 2)


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
        "address": [r"address"],
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
        derived_specs = [
            ("avg_4wk_occupancy_pct", values[-4:], "Derived 4-week average occupancy"),
        ]
        if len(values) >= 13:
            derived_specs.append(("avg_13wk_occupancy_pct", values[-13:], "Derived 13-week average occupancy"))
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
            re.compile(r"\b(?:normalised|normalized)\s+ebitda\b", re.IGNORECASE),
            "Normalised EBITDA",
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
                    "label": label,
                })
                seen.add(field)
                break
        if {"revenue", "payroll_labour_cost", "rent_pa", "ebitda"}.issubset(seen):
            break
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


def build_valuation_gate(extracted: dict[str, Any], combined_text: str | None = None) -> dict[str, Any]:
    fy25 = _as_dict(_get(extracted, "financials", "fy25"))
    ratios = _as_dict(extracted.get("key_ratios"))

    revenue = fy25.get("revenue") or ratios.get("revenue_fy25")
    ebitda = fy25.get("ebitda") or fy25.get("normalised_ebitda") or ratios.get("ebitda_fy25") or ratios.get("ebitda_3yr_avg")
    labour = fy25.get("total_labour_cost")
    occupancy_history = _has_occupancy_history(extracted, combined_text)

    required = {
        "revenue": _present(revenue),
        "ebitda": _present(ebitda),
        "payroll_labour_cost": _present(labour),
        "occupancy_history": occupancy_history,
    }

    blockers = []
    if not required["revenue"]:
        blockers.append({
            "field": "revenue",
            "reason": "Revenue missing",
            "required_evidence": "P&L or management accounts showing revenue.",
        })
    if not required["ebitda"]:
        blockers.append({
            "field": "ebitda",
            "reason": "EBITDA missing",
            "required_evidence": "P&L or normalised EBITDA bridge.",
        })
    if not required["payroll_labour_cost"]:
        blockers.append({
            "field": "payroll_labour_cost",
            "reason": "Payroll/labour cost missing",
            "required_evidence": "Payroll summary or P&L labour cost detail.",
        })

    warnings = []
    if not occupancy_history:
        blockers.append({
            "field": "occupancy_history",
            "reason": "Occupancy history missing",
            "required_evidence": "Weekly or monthly occupancy history, preferably 4-week and 13-week averages.",
        })

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
            "reason": "Occupancy history not verified",
            "message": "Valuation may be shown as illustrative only until occupancy history is verified.",
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

    for field in missing_fields[:12]:
        label = str(field).replace("_", " ")
        add("missing_field", f"Provide evidence for missing field: {label}.", "medium", "missing_fields", [str(field)])
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
    text_fact_by_field: dict[str, dict[str, Any]] = {}
    for fact in [*occupancy_text_facts, *support_text_facts, *financial_text_facts]:
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

    centre = _ensure_dict(extracted, "centre")
    if text_fact_by_field.get("licensed_places") and not _present(centre.get("licensed_places")):
        centre["licensed_places"] = text_fact_by_field["licensed_places"]["value"]
    financials = _ensure_dict(extracted, "financials")
    fy25 = _ensure_dict(financials, "fy25")
    if text_fact_by_field.get("rent_pa") and not _present(fy25.get("rent_pa")):
        fy25["rent_pa"] = text_fact_by_field["rent_pa"]["value"]
    if text_fact_by_field.get("revenue") and not _present(fy25.get("revenue")):
        fy25["revenue"] = text_fact_by_field["revenue"]["value"]
    if text_fact_by_field.get("payroll_labour_cost") and not _present(fy25.get("total_labour_cost")):
        fy25["total_labour_cost"] = text_fact_by_field["payroll_labour_cost"]["value"]
    if text_fact_by_field.get("ebitda") and not _present(fy25.get("ebitda")):
        fy25["ebitda"] = text_fact_by_field["ebitda"]["value"]
    if text_fact_by_field.get("normalised_ebitda") and not _present(fy25.get("normalised_ebitda")):
        fy25["normalised_ebitda"] = text_fact_by_field["normalised_ebitda"]["value"]
    if text_fact_by_field.get("asking_price") and not _present(_get(extracted, "financials", "asking_price")):
        financials["asking_price"] = text_fact_by_field["asking_price"]["value"]

    field_specs = [
        ("centre_name", _get(extracted, "centre", "name"), "extracted_json"),
        ("address", _get(extracted, "centre", "address"), "extracted_json"),
        ("licensed_places", _get(extracted, "centre", "licensed_places"), "extracted_json"),
        ("nqs_rating", _get(extracted, "centre", "nqs_rating"), "extracted_json"),
        ("current_occupancy_pct", _get(extracted, "occupancy", "current_month_pct"), "extracted_json"),
        ("latest_week_occupancy_pct", _get(extracted, "occupancy", "latest_week_pct"), "extracted_json"),
        ("avg_4wk_occupancy_pct", _get(extracted, "occupancy", "avg_4wk_pct"), "extracted_json"),
        ("avg_13wk_occupancy_pct", _get(extracted, "occupancy", "avg_13wk_pct"), "extracted_json"),
        ("avg_52wk_occupancy_pct", _get(extracted, "occupancy", "avg_52wk_pct"), "extracted_json"),
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
    valuation_gate = build_valuation_gate(extracted, combined_text)
    if valuation_gate.get("required_evidence", {}).get("occupancy_history"):
        missing_fields = [
            field for field in missing_fields
            if "occupancy_history" not in str(field).lower()
            and "occupancy history" not in str(field).lower()
        ]
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
        "diligence_checklist": diligence_requests,
        "diligence_requests": diligence_requests,
        "extraction_warnings": extraction_warnings,
        "evidence": evidence,
        "narrative_guard": narrative_guard,
        "market_audit": market_audit,
        "pipeline_projects": scored.get("pipeline_projects") or extracted.get("_pipeline_projects") or [],
        "pipeline_audit": pipeline_audit,
    }
