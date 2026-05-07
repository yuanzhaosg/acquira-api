#!/usr/bin/env python3
"""Local extraction QA harness for real Acquira PDF/XLSX failure samples."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import os
import importlib.util
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "qa-script-missing-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "qa-script-missing-service-key")

if importlib.util.find_spec("fastapi") is None:
    fastapi = types.ModuleType("fastapi")

    class FastAPI:
        def __init__(self, *args, **kwargs):
            pass

        def add_middleware(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

    fastapi.FastAPI = FastAPI
    fastapi.Query = lambda *args, **kwargs: None
    middleware = types.ModuleType("fastapi.middleware")
    cors = types.ModuleType("fastapi.middleware.cors")
    cors.CORSMiddleware = object
    responses = types.ModuleType("fastapi.responses")
    responses.StreamingResponse = object
    responses.JSONResponse = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.middleware"] = middleware
    sys.modules["fastapi.middleware.cors"] = cors
    sys.modules["fastapi.responses"] = responses

if importlib.util.find_spec("supabase") is None:
    supabase = types.ModuleType("supabase")
    supabase.create_client = lambda *args, **kwargs: types.SimpleNamespace()
    sys.modules["supabase"] = supabase

if importlib.util.find_spec("xlrd") is None:
    xlrd = types.ModuleType("xlrd")
    xlrd.open_workbook = lambda *args, **kwargs: None
    sys.modules["xlrd"] = xlrd

import pdfplumber  # noqa: E402

from main import (  # noqa: E402
    classify_vision_provider_error,
    extract_excel_text,
    extract_pdf_pages_with_fallback,
    should_vision_extract_page,
    validate_vision_provider_config,
    vision_provider_smoke_test,
)


FINANCIAL_PATTERNS = {
    "revenue": r"\b(revenue|income|turnover|sales)\b",
    "labour_payroll": r"\b(labou?r|wages|payroll|superannuation|employment hero)\b",
    "rent": r"\b(rent|occupancy cost)\b",
    "ebitda_profit": r"\b(ebitda|profit|net income|normalised|normalized)\b",
}
OCCUPANCY_PATTERNS = {
    "occupancy": r"\b(occupancy|utilisation|utilization|enrolment|xplor)\b",
}
PAYROLL_PATTERNS = {
    "payroll_staffing": r"\b(pay run|payroll|workedhours|worked hours|staffing|employment hero|leave)\b",
}
MONTH_RE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"


def sheet_category(sheet_name: str) -> str:
    s = sheet_name.lower()
    if any(k in s for k in ("adjusted actuals", "myob", "source", "management", "actuals", "p&l", "profit", "loss")):
        return "financials"
    if any(k in s for k in ("occupancy", "utilisation", "utilization", "xplor", "enrol")):
        return "occupancy"
    if any(k in s for k in ("workedhours", "worked hours", "staff", "pay run", "payroll", "employment hero", "leave")):
        return "payroll_staffing"
    if any(k in s for k in ("summary", "details")):
        return "summary"
    return "other"


def matches(text: str, patterns: dict[str, str]) -> list[str]:
    return [field for field, pattern in patterns.items() if re.search(pattern, text, re.I)]


def redact_errors(errors: list[str]) -> list[str]:
    redacted = []
    for error in errors:
        redacted.append(re.sub(r"sk-ant-[A-Za-z0-9_-]+", "[redacted-api-key]", error))
    return redacted


def detect_periods(text: str) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    if re.search(r"\b(forecast|budget|model|pro[-\s]?forma|scenario|template|assumption)\b", text, re.I):
        periods.append({"period_label": "forecast/template/model", "coverage_status": "unknown", "underwriting_use": "excluded", "reason": "Forecast/template/model wording detected."})
    for match in re.finditer(r"\b(?:fy|financial year)\s*['-]?(20)?(\d{2})\b|\b(full\s+year|annual|12\s+months?)\b", text, re.I):
        periods.append({"period_label": match.group(0), "coverage_status": "complete", "underwriting_use": "accepted_or_review_required", "reason": "Annual/full-year period label detected."})
    for match in re.finditer(r"\b(ytd|year\s+to\s+date)\b", text, re.I):
        periods.append({"period_label": match.group(0), "coverage_status": "partial", "underwriting_use": "review_required", "reason": "YTD period detected; annualisation/reconciliation required."})
    for match in re.finditer(r"\b(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)\s*[-–—]\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b", text, re.I):
        periods.append({"period_label": match.group(0), "coverage_status": "partial", "underwriting_use": "review_required", "reason": "Pay-run/date range detected."})
    month_count = len(set(re.findall(rf"\b{MONTH_RE}[-\s']?\d{{2,4}}\b|\b{MONTH_RE}\b", text.lower())))
    if month_count:
        periods.append({"period_label": f"{month_count} monthly markers", "coverage_status": "complete" if month_count >= 11 else "partial", "underwriting_use": "review_required" if month_count < 11 else "accepted_or_review_required", "reason": f"{month_count} monthly markers detected."})
    week_count = len(re.findall(r"\b(?:week\s*(?:ending)?|w/e|weekly)\b", text, re.I))
    if week_count:
        periods.append({"period_label": f"{week_count} weekly observations", "coverage_status": "complete" if week_count >= 13 else "partial", "underwriting_use": "review_required", "reason": f"{week_count} weekly markers detected; 13-week average requires 13."})
    return periods[:20]


def evidence_refs_from_text(text: str, *, file_name: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    current_page: int | None = None
    current_sheet: str | None = None
    current_method: str | None = None
    for line in text.splitlines():
        page_match = re.match(r"--- Page (\d+) ---", line)
        if page_match:
            current_page = int(page_match.group(1))
            current_sheet = None
        if line.startswith("SHEET: "):
            current_sheet = line.removeprefix("SHEET: ").strip()
            current_page = None
        if line.startswith("EXTRACTION_METHOD: "):
            current_method = line.removeprefix("EXTRACTION_METHOD: ").strip()
        if current_page and re.search(r"\b(revenue|income|profit|loss|wages|rent|ebitda|payroll|labou?r)\b", line, re.I):
            refs.append({
                "file_name": file_name,
                "page": current_page,
                "extraction_method": current_method or ("pdf_vision" if "pdf_vision" in text else "pdf_text"),
                "excerpt": line[:240],
            })
        if current_page and line.startswith("VISION_EXTRACTION_ERROR:"):
            refs.append({
                "file_name": file_name,
                "page": current_page,
                "extraction_method": current_method or "pdf_vision",
                "excerpt": line[:240],
            })
        if current_sheet and re.search(r"\b[A-Z]{1,3}\d+\s*=", line):
            refs.append({
                "file_name": file_name,
                "sheet_name": current_sheet,
                "cell_range": "coordinates in excerpt",
                "extraction_method": "excel_cell",
                "excerpt": line[:240],
            })
    return refs[:40]


async def inspect_pdf(path: Path) -> dict[str, Any]:
    high_value_pages: list[int] = []
    predicted_vision_pages: list[int] = []
    pages_seen = 0
    with pdfplumber.open(path) as pdf:
        pages_seen = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            should_extract, _reason = should_vision_extract_page(text, index, text)
            if re.search(r"\b(profit and loss|p&l|income statement|management accounts)\b", text, re.I):
                high_value_pages.append(index)
            if should_extract:
                predicted_vision_pages.append(index)

    extracted_text = await extract_pdf_pages_with_fallback(str(path), path.name, "qa")
    actual_vision_pages: list[int] = []
    for match in re.finditer(r"--- Page (\d+) ---\n([\s\S]*?)(?=\n--- Page \d+ ---|\Z)", extracted_text):
        if re.search(r"^EXTRACTION_METHOD:\s*pdf_vision\s*$", match.group(2), re.M):
            actual_vision_pages.append(int(match.group(1)))
    vision_errors = re.findall(r"VISION_EXTRACTION_ERROR:\s*(.+)", extracted_text)
    financial_fields = matches(extracted_text, FINANCIAL_PATTERNS)
    warnings = []
    warnings.extend(f"Vision extraction failed: {error[:220]}" for error in vision_errors)
    if vision_errors:
        warnings.append("Vision fallback was correctly invoked, but provider authentication/configuration failed, so image-table extraction could not complete.")
    for field in ("revenue", "labour_payroll", "rent", "ebitda_profit"):
        if field not in financial_fields:
            warnings.append(f"{field} was not confidently detected in extracted PDF text; review page-level vision output and source table quality.")

    return {
        "pages_seen": pages_seen,
        "high_value_pages_detected": sorted(set(high_value_pages)),
        "vision_fallback_pages": sorted(set(actual_vision_pages or predicted_vision_pages)),
        "vision_extraction_errors": vision_errors,
        "extracted_financial_fields": financial_fields,
        "detected_periods": detect_periods(extracted_text),
        "coverage_status_by_field": {
            field: "review_required" if detect_periods(extracted_text) else "unknown"
            for field in financial_fields
        },
        "extraction_warnings": warnings,
        "evidence_refs": evidence_refs_from_text(extracted_text, file_name=path.name),
    }


def inspect_workbook(path: Path) -> dict[str, Any]:
    digest = extract_excel_text(str(path))
    sheets_seen = re.findall(r"^SHEET:\s*(.+)$", digest, re.M)
    categories = {
        sheet: category
        for sheet, category in re.findall(r"^SHEET:\s*(.+)\nDETECTED_CATEGORY:\s*(.+)$", digest, re.M)
    }
    warnings = re.findall(r"^(?:EXTRACTION_)?WARNING:\s*(.+)$", digest, re.M)
    skipped = [warning for warning in warnings if re.search(r"\b(skipped|hidden|empty)\b", warning, re.I)]
    periods_by_sheet: dict[str, list[dict[str, Any]]] = {}
    for sheet in sheets_seen:
        sheet_match = re.search(rf"^SHEET:\s*{re.escape(sheet)}\n([\s\S]*?)(?=^SHEET:\s|\Z)", digest, re.M)
        if sheet_match:
            periods_by_sheet[sheet] = detect_periods(sheet_match.group(0))
    if len(sheets_seen) <= 1:
        warnings.append("Workbook digest read one or fewer sheets; expected multi-sheet coverage for databooks.")
    for expected_category in ("financials", "occupancy", "payroll_staffing"):
        if expected_category not in categories.values():
            warnings.append(f"No sheet was categorised as {expected_category}.")

    return {
        "sheets_seen": sheets_seen,
        "sheets_skipped": skipped,
        "detected_categories_by_sheet": categories,
        "detected_periods_by_sheet": periods_by_sheet,
        "coverage_status_by_field": {
            "financials": "review_required",
            "payroll": "review_required",
            "occupancy": "complete" if any(any(p.get("coverage_status") == "complete" for p in periods) for sheet, periods in periods_by_sheet.items() if sheet_category(sheet) == "occupancy") else "review_required",
        },
        "underwriting_use_by_field": {
            "financials": "review_required",
            "payroll": "review_required",
            "occupancy": "review_required",
        },
        "occupancy_observation_count": max([int(p.get("period_label", "0").split(" ")[0]) for periods in periods_by_sheet.values() for p in periods if "weekly" in str(p.get("period_label")) or "monthly" in str(p.get("period_label"))] or [0]),
        "extracted_financial_fields": matches(digest, FINANCIAL_PATTERNS),
        "extracted_occupancy_fields": matches(digest, OCCUPANCY_PATTERNS),
        "extracted_payroll_fields": matches(digest, PAYROLL_PATTERNS),
        "extraction_warnings": warnings,
        "evidence_refs": evidence_refs_from_text(digest, file_name=path.name),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--xlsx", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--vision-smoke-test", action="store_true")
    args = parser.parse_args()

    provider_diagnostics = validate_vision_provider_config()
    if args.vision_smoke_test:
        provider_diagnostics = await vision_provider_smoke_test()
    provider_diagnostics["errors"] = redact_errors(provider_diagnostics.get("errors", []))

    summary: dict[str, Any] = {"vision_provider": provider_diagnostics}
    if args.pdf:
        summary["pdf"] = await inspect_pdf(args.pdf)
        errors = summary["pdf"].get("vision_extraction_errors", [])
        if errors and provider_diagnostics["auth_status"] == "unknown":
            status = classify_vision_provider_error(errors[0])
            provider_diagnostics["auth_status"] = status
            provider_diagnostics["configured"] = status != "missing_api_key"
            provider_diagnostics["errors"] = redact_errors(errors)
        elif summary["pdf"].get("vision_fallback_pages") and provider_diagnostics["auth_status"] == "unknown":
            provider_diagnostics["auth_status"] = "ok"
    if args.xlsx:
        summary["workbook"] = inspect_workbook(args.xlsx)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
