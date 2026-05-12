import os
import importlib.machinery
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ccs_market_data import parse_ccs_workbook

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

if importlib.util.find_spec("fastapi") is None:
    fastapi = types.ModuleType("fastapi")
    fastapi.__spec__ = importlib.machinery.ModuleSpec("fastapi", loader=None)

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
    middleware.__spec__ = importlib.machinery.ModuleSpec("fastapi.middleware", loader=None)
    cors = types.ModuleType("fastapi.middleware.cors")
    cors.__spec__ = importlib.machinery.ModuleSpec("fastapi.middleware.cors", loader=None)
    cors.CORSMiddleware = object
    responses = types.ModuleType("fastapi.responses")
    responses.__spec__ = importlib.machinery.ModuleSpec("fastapi.responses", loader=None)
    responses.StreamingResponse = object
    responses.JSONResponse = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.middleware"] = middleware
    sys.modules["fastapi.middleware.cors"] = cors
    sys.modules["fastapi.responses"] = responses

if importlib.util.find_spec("anthropic") is None:
    anthropic = types.ModuleType("anthropic")
    anthropic.__spec__ = importlib.machinery.ModuleSpec("anthropic", loader=None)
    anthropic.Anthropic = lambda *args, **kwargs: types.SimpleNamespace(messages=types.SimpleNamespace(create=None))
    sys.modules["anthropic"] = anthropic

if importlib.util.find_spec("supabase") is None:
    supabase = types.ModuleType("supabase")
    supabase.__spec__ = importlib.machinery.ModuleSpec("supabase", loader=None)
    supabase.create_client = lambda *args, **kwargs: types.SimpleNamespace()
    sys.modules["supabase"] = supabase

if importlib.util.find_spec("xlrd") is None:
    xlrd = types.ModuleType("xlrd")
    xlrd.__spec__ = importlib.machinery.ModuleSpec("xlrd", loader=None)
    xlrd.open_workbook = lambda *args, **kwargs: None
    sys.modules["xlrd"] = xlrd

from main import (
    ManualContext,
    ManualEvidenceNote,
    attach_ccs_public_market_benchmark_from_env,
    extract_manual_sa3_override,
    extract_target_sa3_from_extracted,
    load_ccs_public_market_benchmark_from_env,
    resolve_target_sa3_for_public_market,
)
from structured_deal import build_structured_deal_intelligence
from test_ccs_market_data import make_synthetic_workbook


class CcsPublicMarketAttachmentTests(unittest.TestCase):
    def tearDown(self):
        load_ccs_public_market_benchmark_from_env.cache_clear()

    def test_extract_target_sa3_uses_explicit_fields_only(self):
        self.assertEqual(extract_target_sa3_from_extracted({"centre": {"postcode": "3131"}}), (None, None))
        self.assertEqual(
            extract_target_sa3_from_extracted({"centre": {"sa3_code": 21104, "sa3_name": "Whitehorse - East"}}),
            ("21104", "Whitehorse - East"),
        )
        self.assertEqual(
            extract_target_sa3_from_extracted({"location": {"sa3_code": "21104"}, "market": {"sa3_name": "Whitehorse - East"}}),
            ("21104", "Whitehorse - East"),
        )
        self.assertEqual(
            extract_target_sa3_from_extracted({"demographics": {"sa3_name": "Whitehorse - East"}}),
            (None, "Whitehorse - East"),
        )

    def test_manual_sa3_override_can_use_context_or_notes(self):
        self.assertEqual(
            extract_manual_sa3_override(ManualContext(sa3Code="21104", sa3Name="Whitehorse - East")),
            ("21104", "Whitehorse - East"),
        )
        self.assertEqual(
            extract_manual_sa3_override({"sa3_code": "21104"}),
            ("21104", None),
        )
        self.assertEqual(
            extract_manual_sa3_override(
                manual_notes=[ManualEvidenceNote(notes="SA3 code: 21104\nSA3 name: Whitehorse - East")]
            ),
            ("21104", "Whitehorse - East"),
        )

    def test_manual_sa3_takes_priority_over_extracted_sa3(self):
        self.assertEqual(
            resolve_target_sa3_for_public_market(
                {"centre": {"sa3_code": "99999", "sa3_name": "Wrong SA3"}},
                manual_context=ManualContext(sa3Code="21104", sa3Name="Whitehorse - East"),
            ),
            ("21104", "Whitehorse - East", "manual_context"),
        )
        self.assertEqual(
            resolve_target_sa3_for_public_market({"centre": {"sa3_code": "21104"}}),
            ("21104", None, "source_document_explicit"),
        )

    def test_env_missing_path_is_noop(self):
        load_ccs_public_market_benchmark_from_env.cache_clear()
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(load_ccs_public_market_benchmark_from_env())
            audit = {"warnings": ["existing"]}
            attached = attach_ccs_public_market_benchmark_from_env(
                {"centre": {"sa3_code": "21104"}},
                audit,
            )
        self.assertEqual(attached, audit)

    def test_env_parse_failure_is_noop(self):
        load_ccs_public_market_benchmark_from_env.cache_clear()
        with patch.dict(os.environ, {
            "ACQUIRA_CCS_WORKBOOK_PATH": "/tmp/acquira-missing-ccs.xlsx",
            "ACQUIRA_CCS_QUARTER": "Dec 2025",
        }, clear=True):
            self.assertIsNone(load_ccs_public_market_benchmark_from_env())

    def test_env_workbook_and_explicit_sa3_attach_public_benchmark_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "ccs.xlsx"
            make_synthetic_workbook(workbook_path)
            load_ccs_public_market_benchmark_from_env.cache_clear()
            with patch.dict(os.environ, {
                "ACQUIRA_CCS_WORKBOOK_PATH": str(workbook_path),
                "ACQUIRA_CCS_QUARTER": "Dec 2025",
                "ACQUIRA_CCS_SOURCE_URL": "https://example.test/ccs.xlsx",
            }, clear=True):
                audit = {"warnings": []}
                attached = attach_ccs_public_market_benchmark_from_env(
                    {"centre": {"sa3_code": "21104"}},
                    audit,
                )

        self.assertIn("public_market_benchmark", attached)
        self.assertEqual(attached["public_market_benchmark"]["sa3_code"], "21104")
        self.assertAlmostEqual(attached["public_market_benchmark"]["children_0_5_per_cbdc_service"], 82.86, places=2)
        self.assertNotIn("local_demand_supply", attached)
        self.assertNotIn("public_market_benchmark", audit)

    def test_env_workbook_and_manual_sa3_code_attach_public_benchmark_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "ccs.xlsx"
            make_synthetic_workbook(workbook_path)
            load_ccs_public_market_benchmark_from_env.cache_clear()
            with patch.dict(os.environ, {
                "ACQUIRA_CCS_WORKBOOK_PATH": str(workbook_path),
                "ACQUIRA_CCS_QUARTER": "Dec 2025",
            }, clear=True):
                audit = {"warnings": []}
                attached = attach_ccs_public_market_benchmark_from_env(
                    {"centre": {"postcode": "3131", "sa3_code": "99999"}},
                    audit,
                    manual_context=ManualContext(sa3Code="21104"),
                )

        benchmark = attached["public_market_benchmark"]
        self.assertEqual(benchmark["sa3_code"], "21104")
        self.assertEqual(benchmark["sa3_selection_source"], "manual_context")
        self.assertIn("manual/admin context", " ".join(benchmark["caveats"]))
        self.assertNotIn("local_demand_supply", attached)
        self.assertNotIn("public_market_benchmark", audit)

    def test_env_workbook_and_manual_sa3_name_attach_public_benchmark_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "ccs.xlsx"
            make_synthetic_workbook(workbook_path)
            load_ccs_public_market_benchmark_from_env.cache_clear()
            with patch.dict(os.environ, {
                "ACQUIRA_CCS_WORKBOOK_PATH": str(workbook_path),
                "ACQUIRA_CCS_QUARTER": "Dec 2025",
            }, clear=True):
                attached = attach_ccs_public_market_benchmark_from_env(
                    {"centre": {"postcode": "3131"}},
                    {"warnings": []},
                    manual_context=ManualContext(sa3Name="whitehorse - east"),
                )

        self.assertEqual(attached["public_market_benchmark"]["sa3_code"], "21104")
        self.assertEqual(attached["public_market_benchmark"]["sa3_selection_source"], "manual_context")
        self.assertNotIn("local_demand_supply", attached)

    def test_manual_sa3_does_not_become_source_document_fact(self):
        workflow = build_structured_deal_intelligence(
            extracted={
                "centre": {
                    "name": "Manual SA3 Fixture",
                    "postcode": "3131",
                    "licensed_places": 55,
                },
                "meta": {"missing_fields": []},
            },
            scored={"centre_name": "Manual SA3 Fixture", "total_score": 50, "deal_breaker_flags": {"flags": []}},
            combined_text="Postcode: 3131",
            source_files=["qa.txt"],
            file_classes={"qa.txt": "qa_fixture"},
        )

        fields = {fact["field"] for fact in workflow["facts"]}
        self.assertNotIn("sa3_code", fields)
        self.assertNotIn("sa3_name", fields)
        self.assertFalse(any("sa3" in str(field).lower() for field in workflow["missing_fields"]))

    def test_structured_workflow_preserves_public_benchmark_under_market_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "ccs.xlsx"
            make_synthetic_workbook(workbook_path)
            parsed = parse_ccs_workbook(str(workbook_path), "Dec 2025")
        from ccs_market_data import attach_ccs_public_market_benchmark_if_available

        market_audit = attach_ccs_public_market_benchmark_if_available(
            {"warnings": []},
            parsed,
            target_sa3_code="21104",
        )
        workflow = build_structured_deal_intelligence(
            extracted={
                "centre": {
                    "name": "Whitehorse Fixture",
                    "sa3_code": "21104",
                    "licensed_places": 55,
                },
                "_market_audit": market_audit,
                "_pipeline_audit": {"source_type": "none", "approved_places": 0, "risk_adjusted_places": 0, "warnings": []},
                "meta": {"missing_fields": []},
            },
            scored={"centre_name": "Whitehorse Fixture", "total_score": 50, "deal_breaker_flags": {"flags": []}},
            combined_text="Public market benchmark QA.",
            source_files=["qa.json"],
            file_classes={"qa.json": "qa_fixture"},
        )

        self.assertIn("public_market_benchmark", workflow["market_audit"])
        self.assertNotIn("local_demand_supply", workflow["market_audit"])
        self.assertNotIn("public_market_benchmark", workflow)
        self.assertEqual(workflow["market_audit"]["public_market_benchmark"]["sa3_code"], "21104")

    def test_structured_deal_preserves_explicit_sa3_fields_as_facts(self):
        workflow = build_structured_deal_intelligence(
            extracted={
                "centre": {
                    "name": "Whitehorse Fixture",
                    "sa3_code": "21104",
                    "sa3_name": "Whitehorse - East",
                    "postcode": "3131",
                    "licensed_places": 55,
                },
                "meta": {"missing_fields": []},
            },
            scored={"centre_name": "Whitehorse Fixture", "total_score": 50, "deal_breaker_flags": {"flags": []}},
            combined_text="SA3 code: 21104\nSA3 name: Whitehorse - East\nPostcode: 3131",
            source_files=["qa.txt"],
            file_classes={"qa.txt": "qa_fixture"},
        )

        facts_by_field = {fact["field"]: fact for fact in workflow["facts"]}
        self.assertEqual(facts_by_field["sa3_code"]["value"], "21104")
        self.assertEqual(facts_by_field["sa3_name"]["value"], "Whitehorse - East")

    def test_structured_deal_extracts_only_explicit_sa3_from_source_text(self):
        extracted = {
            "centre": {
                "name": "Whitehorse Fixture",
                "postcode": "3131",
                "licensed_places": 55,
            },
            "meta": {"missing_fields": []},
        }
        workflow = build_structured_deal_intelligence(
            extracted=extracted,
            scored={"centre_name": "Whitehorse Fixture", "total_score": 50, "deal_breaker_flags": {"flags": []}},
            combined_text="SA3 code: 21104\nSA3 name: Whitehorse - East\nPostcode: 3131",
            source_files=["qa.txt"],
            file_classes={"qa.txt": "qa_fixture"},
        )

        self.assertEqual(extracted["centre"]["sa3_code"], "21104")
        self.assertEqual(extracted["centre"]["sa3_name"], "Whitehorse - East")
        facts_by_field = {fact["field"]: fact for fact in workflow["facts"]}
        self.assertEqual(facts_by_field["sa3_code"]["extraction_method"], "regex:supplemental_identity:unknown")
        self.assertEqual(facts_by_field["sa3_name"]["extraction_method"], "regex:supplemental_identity:unknown")

    def test_postcode_alone_does_not_attach_public_market_benchmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "ccs.xlsx"
            make_synthetic_workbook(workbook_path)
            load_ccs_public_market_benchmark_from_env.cache_clear()
            with patch.dict(os.environ, {
                "ACQUIRA_CCS_WORKBOOK_PATH": str(workbook_path),
                "ACQUIRA_CCS_QUARTER": "Dec 2025",
            }, clear=True):
                audit = {"warnings": []}
                attached = attach_ccs_public_market_benchmark_from_env(
                    {"centre": {"postcode": "3131", "suburb": "Forest Hill"}},
                    audit,
                )

        self.assertEqual(attached, audit)
        self.assertNotIn("public_market_benchmark", attached)
        self.assertNotIn("local_demand_supply", attached)


if __name__ == "__main__":
    unittest.main()
