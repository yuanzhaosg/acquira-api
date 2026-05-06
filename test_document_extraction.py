import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

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

if importlib.util.find_spec("anthropic") is None:
    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = lambda *args, **kwargs: types.SimpleNamespace(messages=types.SimpleNamespace(create=None))
    sys.modules["anthropic"] = anthropic

if importlib.util.find_spec("supabase") is None:
    supabase = types.ModuleType("supabase")
    supabase.create_client = lambda *args, **kwargs: types.SimpleNamespace()
    sys.modules["supabase"] = supabase

if importlib.util.find_spec("xlrd") is None:
    xlrd = types.ModuleType("xlrd")
    xlrd.open_workbook = lambda *args, **kwargs: None
    sys.modules["xlrd"] = xlrd

import fitz
import openpyxl

from structured_deal import build_structured_deal_intelligence

import main


def make_mixed_pdf(path: Path) -> None:
    image_doc = fitz.open()
    image_page = image_doc.new_page(width=420, height=220)
    image_page.insert_text((24, 34), "Revenue 1,200,000", fontsize=14)
    image_page.insert_text((24, 64), "Wages 620,000", fontsize=14)
    image_page.insert_text((24, 94), "Rent 110,000", fontsize=14)
    image_page.insert_text((24, 124), "EBITDA 210,000", fontsize=14)
    pix = image_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)

    doc = fitz.open()
    normal = doc.new_page(width=595, height=842)
    normal.insert_text((72, 72), "Information Memorandum\nCentre overview and lease summary.", fontsize=12)
    pl_page = doc.new_page(width=595, height=842)
    pl_page.insert_text((72, 72), "PROFIT AND LOSS", fontsize=18)
    pl_page.insert_image(fitz.Rect(72, 110, 520, 380), stream=pix.tobytes("png"))
    doc.save(path)
    doc.close()
    image_doc.close()


def make_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Adjusted Actuals"
    ws.append(["Metric", "FY25"])
    ws.append(["Revenue", 1200000])
    ws.append(["Rent", 110000])
    ws.append(["EBITDA", "=B2-B3-620000"])

    ws = wb.create_sheet("Output Occupancy")
    ws.append(["Month", "Occupancy"])
    ws.append(["Feb-26", 88.5])

    ws = wb.create_sheet("WorkedHours - Employment Hero")
    ws.append(["Role", "Hours", "Wages"])
    ws.append(["Educators", 1520, 620000])
    wb.save(path)


class DocumentExtractionTests(unittest.TestCase):
    def test_missing_anthropic_key_reports_missing_without_secret(self):
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            diagnostics = main.validate_vision_provider_config()
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

        self.assertFalse(diagnostics["configured"])
        self.assertEqual(diagnostics["auth_status"], "missing_api_key")
        self.assertNotIn("test-key", str(diagnostics))

    def test_vision_401_classifies_invalid_auth_without_key_value(self):
        error = "Error code: 401 - authentication_error invalid x-api-key for sk-ant-secret"
        self.assertEqual(main.classify_vision_provider_error(error), "invalid_auth")

    def test_pdf_page_level_fallback_is_invoked_for_sparse_pl_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "synthetic-im.pdf"
            make_mixed_pdf(pdf_path)

            async def fake_vision(_image_b64: str, filename: str, page_number: int, reason: str) -> str:
                return (
                    f"--- Page {page_number} ---\n"
                    f"SOURCE_FILE: {filename}\n"
                    f"PAGE: {page_number}\n"
                    "EXTRACTION_METHOD: pdf_vision\n"
                    f"FALLBACK_REASON: {reason}\n"
                    "| Revenue | 1,200,000 |\n"
                    "| Wages | 620,000 |\n"
                    "| Rent | 110,000 |\n"
                    "| EBITDA | 210,000 |"
                )

            with patch.object(main, "vision_extract_page", side_effect=fake_vision) as vision:
                text = asyncio.run(main.extract_pdf_pages_with_fallback(str(pdf_path), pdf_path.name, "im_pdf"))

            self.assertTrue(vision.called)
            self.assertIn("--- Page 2 ---", text)
            self.assertIn("EXTRACTION_METHOD: pdf_vision", text)
            self.assertIn("Revenue", text)
            self.assertIn("EBITDA", text)

    def test_workbook_digest_includes_sheets_coordinates_and_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / "synthetic-databook.xlsx"
            make_workbook(xlsx_path)

            digest = main.extract_excel_text(str(xlsx_path))

            self.assertIn("SHEET: Adjusted Actuals", digest)
            self.assertIn("DETECTED_CATEGORY: financials", digest)
            self.assertIn("SHEET: Output Occupancy", digest)
            self.assertIn("DETECTED_CATEGORY: occupancy", digest)
            self.assertIn("SHEET: WorkedHours - Employment Hero", digest)
            self.assertIn("DETECTED_CATEGORY: payroll_staffing", digest)
            self.assertIn("A1=Metric", digest)
            self.assertIn("B2=1200000", digest)
            self.assertIn("formula =B2-B3-620000", digest)
            self.assertIn("EVIDENCE_REFS: use synthetic-databook.xlsx / Adjusted Actuals / cell coordinates", digest)

    def test_market_audit_shell_exists_when_market_data_missing(self):
        workflow = build_structured_deal_intelligence(
            extracted={
                "meta": {"source_files": ["synthetic-im.pdf"]},
                "centre": {"name": "Synthetic Childcare"},
                "financials": {"fy25": {}},
                "occupancy": {},
                "lease": {},
                "fees": {},
            },
            scored={"centre_name": "Synthetic Childcare"},
            combined_text="Synthetic report without market data.",
            source_files=["synthetic-im.pdf"],
            file_classes={"synthetic-im.pdf": "im_pdf"},
        )

        self.assertIn("market_audit", workflow)
        self.assertEqual(workflow["market_audit"]["status"], "missing")
        self.assertIn("missing_fields", workflow["market_audit"])
        self.assertTrue(workflow["market_audit"]["warnings"])

    def test_provider_failure_warning_surfaces_in_workflow(self):
        workflow = build_structured_deal_intelligence(
            extracted={
                "meta": {"source_files": ["synthetic-im.pdf"]},
                "centre": {"name": "Synthetic Childcare"},
                "financials": {"fy25": {}},
                "occupancy": {},
                "lease": {},
                "fees": {},
            },
            scored={"centre_name": "Synthetic Childcare"},
            combined_text=(
                "--- Page 12 ---\n"
                "SOURCE_FILE: synthetic-im.pdf\n"
                "PAGE: 12\n"
                "EXTRACTION_METHOD: pdf_vision\n"
                "VISION_PROVIDER_STATUS: invalid_auth\n"
                "VISION_EXTRACTION_ERROR: Error code: 401 - invalid x-api-key\n"
                "FALLBACK_REASON: sparse high-value evidence page 12\n"
            ),
            source_files=["synthetic-im.pdf"],
            file_classes={"synthetic-im.pdf": "im_pdf"},
        )

        messages = [warning["message"] for warning in workflow["extraction_warnings"]]
        self.assertTrue(any("pages 12" in message and "vision provider" in message for message in messages))
        self.assertNotIn("sk-ant", str(workflow["extraction_warnings"]))


if __name__ == "__main__":
    unittest.main()
