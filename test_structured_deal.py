import unittest

from demand_service import build_market_audit, compute_demand, market_position_score
from geospatial_competitors import get_nearby_competitors, material_supply_difference
from pipeline_supply import build_pipeline_supply
from structured_deal import (
    BLOCKED_VALUATION_MESSAGE,
    build_structured_deal_intelligence,
    build_valuation_gate,
    extract_fee_facts_from_text,
    extract_occupancy_facts_from_text,
)


class MockResult:
    def __init__(self, data):
        self.data = data


class MockQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.limit_count = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def execute(self):
        rows = self.rows
        for field, value in self.filters:
            rows = [row for row in rows if row.get(field) == value]
        if self.limit_count is not None:
            rows = rows[:self.limit_count]
        return MockResult(rows)


class MockSupabase:
    def __init__(self, *, rows=None, rpc_rows=None, rpc_error=None):
        self.rows = rows or []
        self.rpc_rows = rpc_rows or []
        self.rpc_error = rpc_error
        self.rpc_calls = []

    def from_(self, _table):
        return MockQuery(self.rows)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if self.rpc_error:
            raise self.rpc_error
        return MockQuery(self.rpc_rows)


class StructuredDealTests(unittest.TestCase):
    def test_valuation_gate_blocks_when_financial_evidence_missing(self):
        extracted = {
            "financials": {"fy25": {"revenue": None, "ebitda": 120000, "total_labour_cost": None}},
            "key_ratios": {},
            "occupancy": {"avg_4wk_pct": 72, "avg_13wk_pct": 74},
        }

        gate = build_valuation_gate(extracted)

        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["reason"], "Insufficient financial evidence")
        self.assertEqual(gate["message"], BLOCKED_VALUATION_MESSAGE)
        self.assertEqual(gate["valuation_label"], "illustrative_only")
        self.assertFalse(gate["required_evidence"]["revenue"])
        self.assertFalse(gate["required_evidence"]["payroll_labour_cost"])

    def test_valuation_gate_passes_when_core_inputs_and_occupancy_history_exist(self):
        extracted = {
            "financials": {"fy25": {"revenue": 900000, "ebitda": 180000, "total_labour_cost": 510000}},
            "key_ratios": {},
            "occupancy": {"avg_4wk_pct": 72, "avg_13wk_pct": 74},
        }

        gate = build_valuation_gate(extracted)

        self.assertEqual(gate["status"], "pass")
        self.assertEqual(gate["valuation_label"], "evidence_supported")
        self.assertTrue(gate["can_show_confident_valuation"])

    def test_valuation_gate_blocks_when_occupancy_history_missing(self):
        extracted = {
            "financials": {"fy25": {"revenue": 900000, "ebitda": 180000, "total_labour_cost": 510000}},
            "key_ratios": {},
            "occupancy": {"current_month_pct": 22},
        }

        gate = build_valuation_gate(extracted)

        self.assertEqual(gate["status"], "blocked")
        self.assertFalse(gate["can_show_confident_valuation"])
        self.assertTrue(any(b["field"] == "occupancy_history" for b in gate["blockers"]))

    def test_narrative_guard_blocks_confident_valuation_language(self):
        extracted = {
            "meta": {"data_quality": "MEDIUM", "missing_fields": ["revenue", "ebitda", "payroll", "occupancy_history"]},
            "centre": {"name": "Guarded ELC"},
            "financials": {"fy25": {"revenue": None, "ebitda": None, "total_labour_cost": None}},
            "key_ratios": {},
            "occupancy": {"current_month_pct": 72},
            "_pipeline_audit": {"search_required": False, "warnings": []},
        }
        scored = {
            "analyst_summary": "Base valuation looks supportable and the business is worth $1.2m.",
            "verdict": {"one_liner": "Proceed with caution."},
            "next_steps": {"verdict_plain": "Proceed with caution."},
        }

        result = build_structured_deal_intelligence(
            extracted=extracted,
            scored=scored,
            combined_text="",
            source_files=[],
            file_classes={},
        )

        guard = result["narrative_guard"]
        self.assertIn("Cannot underwrite yet", guard["analyst_summary"])
        self.assertEqual(guard["valuation_note"], "Illustrative only — not underwritten pending financial evidence.")
        self.assertFalse(guard["can_use_legacy_valuation_language"])
        self.assertTrue(guard["legacy_may_conflict"])

    def test_narrative_guard_flags_pipeline_search_and_low_utilisation(self):
        extracted = {
            "meta": {"data_quality": "MEDIUM", "missing_fields": ["occupancy_history"]},
            "centre": {"name": "Turnaround ELC"},
            "financials": {"fy25": {"revenue": None, "ebitda": None, "total_labour_cost": None}},
            "key_ratios": {},
            "occupancy": {"current_month_pct": 22},
            "_pipeline_audit": {
                "source_type": "none",
                "searched": False,
                "search_required": True,
                "approved_places": 0,
                "lodged_places": 0,
                "risk_adjusted_places": 0,
                "confidence": "low",
                "warnings": [],
            },
            "_market_audit": {
                "competitor_supply": {"material_difference": True},
            },
        }

        result = build_structured_deal_intelligence(
            extracted=extracted,
            scored={"analyst_summary": "No pipeline supply and normal acquisition opportunity."},
            combined_text="",
            source_files=[],
            file_classes={},
        )

        guard = result["narrative_guard"]
        self.assertIn("Operator-led turnaround", guard["recommendation"])
        self.assertIn("Current utilisation is about 22%", guard["analyst_summary"])
        self.assertEqual(guard["pipeline_note"], "DA pipeline not verified; search required before treating pipeline as zero.")
        self.assertEqual(guard["market_note"], "Competitor supply methodology requires verification.")

    def test_narrative_guard_handles_missing_workflow_context(self):
        extracted = {
            "meta": {"data_quality": "MEDIUM", "missing_fields": []},
            "centre": {"name": "Sparse ELC"},
            "financials": {"fy25": {"revenue": 900000, "ebitda": 180000, "total_labour_cost": 510000}},
            "key_ratios": {},
            "occupancy": {"avg_4wk_pct": 72, "avg_13wk_pct": 74},
        }

        result = build_structured_deal_intelligence(
            extracted=extracted,
            scored={"analyst_summary": "Evidence supports further review."},
            combined_text="",
            source_files=[],
            file_classes={},
        )

        guard = result["narrative_guard"]
        self.assertIn("recommendation", guard)
        self.assertTrue(guard["can_use_legacy_valuation_language"])

    def test_fee_facts_are_extracted_from_daily_and_weekly_fee_text(self):
        text = """
=== Sample IM.pdf (im_pdf) ===
Fee Schedule
Daily Fee 0-2 years: $185 per day
Weekly fee for preschool program: $760 per week
Daily fees: $135-$150
Weekly fees: $675-$750
"""

        facts = extract_fee_facts_from_text(text)

        self.assertGreaterEqual(len(facts), 2)
        self.assertTrue(any(f["period"] == "daily" and f["value"] == 185 for f in facts))
        self.assertTrue(any(f["period"] == "weekly" and f["value"] == 760 for f in facts))
        self.assertTrue(any(f["period"] == "daily" and f["value"] == "$135-$150" for f in facts))
        self.assertTrue(any(f["period"] == "weekly" and f["value"] == "$675-$750" for f in facts))
        self.assertTrue(all(f["source_label"] == "Sample IM.pdf" for f in facts))

    def test_fee_data_removes_fee_missing_markers(self):
        extracted = {
            "meta": {
                "data_quality": "MEDIUM",
                "missing_fields": ["fee_schedule", "pricing_data", "payroll"],
                "missing_fields_count": 3,
            },
            "centre": {"name": "Sample ELC"},
            "financials": {"fy25": {"revenue": 900000, "ebitda": 180000, "total_labour_cost": 510000}},
            "key_ratios": {},
            "occupancy": {"avg_4wk_pct": 72, "avg_13wk_pct": 74},
        }
        scored = {"centre_name": "Sample ELC"}
        text = """
=== Sample IM.pdf (im_pdf) ===
Daily fees: Nursery $180 per day, Toddlers $170 per day
"""

        result = build_structured_deal_intelligence(
            extracted=extracted,
            scored=scored,
            combined_text=text,
            source_files=["Sample IM.pdf"],
            file_classes={"Sample IM.pdf": "im_pdf"},
        )

        self.assertIn("payroll", result["missing_fields"])
        self.assertNotIn("fee_schedule", result["missing_fields"])
        self.assertNotIn("pricing_data", result["missing_fields"])
        self.assertTrue(any(f["field"].startswith("fee_") for f in result["facts"]))
        self.assertTrue(all("id" in f and "category" in f and "source" in f for f in result["facts"]))
        self.assertIn("diligence_checklist", result)
        self.assertIn("extraction_warnings", result)

    def test_dream_big_regression_fixture(self):
        extracted = {
            "meta": {
                "data_quality": "MEDIUM",
                "missing_fields": ["revenue", "ebitda", "payroll", "occupancy_history", "lease", "nqs_compliance"],
                "missing_fields_count": 6,
            },
            "centre": {
                "name": "Dream Big Early Learning Centre",
                "address": "1 Sample Street, Melbourne VIC",
                "licensed_places": 90,
                "nqs_rating": None,
            },
            "financials": {
                "fy25": {"revenue": None, "ebitda": None, "total_labour_cost": None},
            },
            "key_ratios": {},
            "occupancy": {
                "current_month_pct": 22,
                "peak_pct": 88,
                "avg_4wk_pct": None,
                "avg_13wk_pct": None,
            },
            "lease": {"expiry_date": None},
            "hard_flags": [],
        }
        scored = {
            "centre_name": "Dream Big Early Learning Centre",
            "next_steps": {
                "ask_broker_for": [
                    "Executed lease agreement",
                    "NQS/compliance history and latest ACECQA report",
                ],
                "due_diligence_priorities": [],
            },
        }
        text = """
=== Dream Big IM.pdf (im_pdf) ===
--- Page 2 ---
Dream Big Early Learning Centre
Current utilisation is approximately 22%.
Stabilised occupancy assumption is 88%.
Upside occupancy case is 98%.
CURRENT FEES
Daily fees: $135 – $150
Weekly fees: $675 to $750
"""

        result = build_structured_deal_intelligence(
            extracted=extracted,
            scored=scored,
            combined_text=text,
            source_files=["Dream Big IM.pdf"],
            file_classes={"Dream Big IM.pdf": "im_pdf"},
        )

        facts = result["facts"]
        checklist_text = " ".join((i.get("question") or i.get("request") or "") for i in result["diligence_checklist"]).lower()

        self.assertTrue(any(f["field"].startswith("fee_daily") and f["value"] == "$135-$150" for f in facts))
        self.assertTrue(any(f["field"].startswith("fee_weekly") and f["value"] == "$675-$750" for f in facts))
        self.assertFalse(any(f["field"].startswith("fee_") and f["value"] in {88, 98} for f in facts))
        self.assertFalse(any(f["field"].startswith("fee_weekly") and f["value"] == "$135-$150" for f in facts))
        self.assertEqual(result["deal_summary"]["current_occupancy_pct"], 22)
        self.assertTrue(any(f["field"] == "current_occupancy_pct" and f["value"] == 22 for f in facts))
        current_fact = next(f for f in facts if f["field"] == "current_occupancy_pct")
        self.assertEqual(current_fact["source"]["page"], 2)
        self.assertIn("Current utilisation", current_fact["source"]["excerpt"])
        self.assertTrue(current_fact["evidence_id"].startswith("ev_"))
        self.assertTrue(any(ev["id"] == current_fact["evidence_id"] for ev in result["evidence"]))
        self.assertIn(result["valuation_gate"]["status"], ["blocked", "needs_review"])
        self.assertFalse(result["valuation_gate"]["can_show_confident_valuation"])
        self.assertFalse(any("fee" in str(field).lower() for field in result["missing_fields"]))
        for expected in ["p&l", "payroll", "occupancy", "lease", "nqs"]:
            self.assertIn(expected, checklist_text)

    def test_fee_ranges_with_to_and_current_fee_labels_are_preserved(self):
        text = """
=== Fee IM.pdf (im_pdf) ===
--- Page 4 ---
CURRENT FEES
Daily Fees $135 to $150
Weekly Fees $675 – $750
Centre Fees per day $140-$155
"""

        facts = extract_fee_facts_from_text(text)

        self.assertTrue(any(f["period"] == "daily" and f["value"] == "$135-$150" and f["page"] == 4 for f in facts))
        self.assertTrue(any(f["period"] == "weekly" and f["value"] == "$675-$750" and f["page"] == 4 for f in facts))
        self.assertTrue(any(f["period"] == "daily" and f["value"] == "$140-$155" for f in facts))

    def test_current_occupancy_ignores_modelled_or_stabilised_assumptions(self):
        text = """
=== Occupancy IM.pdf (im_pdf) ===
--- Page 3 ---
Current utilisation is approximately 22%.
Modelled stabilised occupancy is 88%.
Upside occupancy assumption 98%.
Latest week utilisation 24%.
4 week average utilisation 23%.
13 week average utilisation 21%.
"""

        facts = extract_occupancy_facts_from_text(text)

        self.assertTrue(any(f["field"] == "current_occupancy_pct" and f["value"] == 22 for f in facts))
        self.assertTrue(any(f["field"] == "latest_week_occupancy_pct" and f["value"] == 24 for f in facts))
        self.assertTrue(any(f["field"] == "avg_4wk_occupancy_pct" and f["value"] == 23 for f in facts))
        self.assertFalse(any(f["value"] in {88, 98} and f["field"] == "current_occupancy_pct" for f in facts))

    def test_occupancy_history_detected_from_utilisation_table_text(self):
        extracted = {
            "financials": {"fy25": {"revenue": 900000, "ebitda": 180000, "total_labour_cost": 510000}},
            "key_ratios": {},
            "occupancy": {"current_month_pct": 22},
        }
        text = """
=== Utilisation Report.pdf (occupancy_excel) ===
Week 1 utilisation 21%
Week 2 utilisation 22%
Week 3 utilisation 23%
Week 4 utilisation 24%
"""

        gate = build_valuation_gate(extracted, text)

        self.assertNotIn("occupancy_history", [b["field"] for b in gate["blockers"]])

    def test_rent_and_licensed_places_are_not_missing_when_source_text_has_them(self):
        extracted = {
            "meta": {
                "data_quality": "MEDIUM",
                "missing_fields": ["annual_rent", "licensed_places", "asking_price", "fee_schedule"],
                "missing_fields_count": 3,
            },
            "centre": {"name": "Source Backed ELC", "licensed_places": None},
            "financials": {"fy25": {"revenue": 900000, "ebitda": 180000, "total_labour_cost": 510000, "rent_pa": None}},
            "key_ratios": {},
            "occupancy": {"avg_4wk_pct": 72, "avg_13wk_pct": 74},
            "lease": {},
            "hard_flags": [],
        }
        text = """
=== Lease Summary.pdf (lease_pdf) ===
--- Page 1 ---
Licensed capacity: 90 places.
Annual rent: $180,000 per annum.
Business price: $150,000.
Daily Fees $135-$150.
"""

        result = build_structured_deal_intelligence(
            extracted=extracted,
            scored={"centre_name": "Source Backed ELC"},
            combined_text=text,
            source_files=["Lease Summary.pdf"],
            file_classes={"Lease Summary.pdf": "lease_pdf"},
        )

        self.assertNotIn("annual_rent", result["missing_fields"])
        self.assertNotIn("licensed_places", result["missing_fields"])
        self.assertNotIn("asking_price", result["missing_fields"])
        self.assertFalse(any("fee" in str(field).lower() for field in result["missing_fields"]))
        rent_fact = next(f for f in result["facts"] if f["field"] == "rent_pa")
        places_fact = next(f for f in result["facts"] if f["field"] == "licensed_places")
        price_fact = next(f for f in result["facts"] if f["field"] == "asking_price")
        self.assertEqual(rent_fact["value"], 180000)
        self.assertEqual(places_fact["value"], 90)
        self.assertEqual(price_fact["value"], 150000)
        self.assertEqual(rent_fact["source"]["page"], 1)
        self.assertEqual(places_fact["source"]["page"], 1)
        self.assertEqual(price_fact["source"]["page"], 1)

    def test_market_audit_documents_edr_formula_and_interpretation(self):
        demand = compute_demand("3186", 1000)
        market = market_position_score(demand, competitor_count=4, approved_pipeline_places=0, subject_licensed_places=80)

        audit = build_market_audit(
            demand,
            market,
            subject_licensed_places=80,
            competitor_source="ACECQA centres table filtered by postcode plus subject centre.",
            included_centres=5,
            pipeline_source="User-provided approved DA count.",
            pipeline_searched=True,
        )

        self.assertIn("estimated kids aged 0-4", audit["edr"]["formula"])
        self.assertIn(audit["edr"]["interpretation"], ["undersupplied", "balanced", "oversupplied", "unknown"])
        self.assertIsNotNone(audit["catchment_radius_km"])
        self.assertTrue(audit["radius_reason"])

    def test_market_audit_warns_when_pipeline_zero_without_source(self):
        demand = {
            "radius_km": 3.0,
            "is_regional": False,
            "estimated_kids_0_to_4": 1000,
            "total_licensed_places": 800,
            "ldc_util_rate": {"mid": 0.475},
            "adj_kids_per_place": {"mid": 0.59},
            "zone": "balanced",
            "confidence": "medium",
            "abs_hit": True,
            "detail": {"coverage_pct": 80, "year_estimate": 2026, "postcode_area_km2": 30},
        }
        market = {"edr_mid": 0.59, "competitor_count": 3, "approved_pipeline_places": 0, "confidence": "medium"}

        audit = build_market_audit(demand, market, competitor_source="ACECQA", included_centres=4)

        self.assertTrue(any("Pipeline places are zero" in warning for warning in audit["warnings"]))
        self.assertEqual(audit["pipeline_places"]["confidence"], "low")

    def test_market_audit_warns_when_vendor_kids_materially_differs(self):
        demand = {
            "radius_km": 2.0,
            "is_regional": False,
            "estimated_kids_0_to_4": 1000,
            "total_licensed_places": 700,
            "ldc_util_rate": {"mid": 0.475},
            "adj_kids_per_place": {"mid": 0.68},
            "zone": "balanced",
            "confidence": "high",
            "abs_hit": True,
            "detail": {"coverage_pct": 90, "year_estimate": 2026, "postcode_area_km2": 12},
        }
        market = {"edr_mid": 0.68, "competitor_count": 4, "approved_pipeline_places": 90, "confidence": "high"}

        audit = build_market_audit(
            demand,
            market,
            competitor_source="ACECQA",
            included_centres=5,
            pipeline_source="Manual DA input",
            pipeline_searched=True,
            vendor_kids_0_4=1600,
        )

        self.assertTrue(any("Vendor supplied kids 0-4 count" in warning for warning in audit["warnings"]))

    def test_market_audit_warns_on_approximated_or_missing_inputs(self):
        demand = {
            "radius_km": 3.0,
            "is_regional": False,
            "estimated_kids_0_to_4": 600,
            "total_licensed_places": 0,
            "ldc_util_rate": {"mid": 0.475},
            "adj_kids_per_place": {"mid": 0.0},
            "zone": "oversupplied",
            "confidence": "high",
            "abs_hit": False,
            "detail": None,
        }
        market = {"edr_mid": 0.0, "competitor_count": 0, "approved_pipeline_places": 0, "confidence": "high"}

        audit = build_market_audit(demand, market)

        warning_text = " ".join(audit["warnings"])
        self.assertIn("approximated postcode allocation", warning_text)
        self.assertIn("Competitor list is empty", warning_text)
        self.assertIn("Demand confidence is high", warning_text)

    def test_geospatial_success_path_uses_rpc_and_excludes_subject(self):
        supabase = MockSupabase(
            rpc_rows=[
                {
                    "service_id": "SE-001",
                    "service_name": "Subject ELC",
                    "address": "1 Main St",
                    "postcode": "3186",
                    "lat": -37.9,
                    "lng": 144.99,
                    "distance_m": 0,
                    "licensed_places": 80,
                },
                {
                    "service_id": "SE-002",
                    "service_name": "Nearby ELC",
                    "address": "5 Main St",
                    "postcode": "3186",
                    "lat": -37.901,
                    "lng": 144.991,
                    "distance_m": 180,
                    "licensed_places": 90,
                    "provider_name": "Provider Pty Ltd",
                },
            ]
        )

        result = get_nearby_competitors(
            supabase,
            postcode="3186",
            radius_km=2,
            subject_name="Subject ELC",
            subject_address="1 Main St",
            service_approval_number="SE-001",
            subject_licensed_places=80,
            lat=-37.9,
            lng=144.99,
        )

        self.assertEqual(result["source"], "geospatial_supabase")
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["target_geocode_method"], "provided_coordinates")
        self.assertEqual(result["exclusion_method"], "service_approval_number")
        self.assertEqual(result["competitor_count"], 1)
        self.assertEqual(result["total_licensed_places"], 170)
        self.assertEqual(result["centres"][0]["provider_name"], "Provider Pty Ltd")

    def test_geospatial_rpc_unavailable_falls_back_to_postcode(self):
        supabase = MockSupabase(
            rows=[
                {"service_id": "SE-010", "service_name": "Postcode ELC", "postcode": "3186", "licensed_places": 90},
                {"service_id": "SE-011", "service_name": "Postcode Two", "postcode": "3186", "licensed_places": 70},
            ],
            rpc_error=RuntimeError("rpc missing"),
        )

        result = get_nearby_competitors(
            supabase,
            postcode="3186",
            radius_km=2,
            subject_licensed_places=80,
            lat=-37.9,
            lng=144.99,
        )

        self.assertEqual(result["source"], "postcode_fallback")
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["total_licensed_places"], 240)
        self.assertTrue(any("geospatial RPC failed" in warning for warning in result["warnings"]))

    def test_geospatial_supabase_match_by_service_approval(self):
        supabase = MockSupabase(
            rows=[
                {
                    "service_id": "SE-123",
                    "service_name": "Matched ELC",
                    "postcode": "3186",
                    "lat": -37.91,
                    "lng": 144.98,
                    "licensed_places": 80,
                }
            ],
            rpc_rows=[],
        )

        result = get_nearby_competitors(
            supabase,
            postcode="3186",
            radius_km=2,
            service_approval_number="SE-123",
            subject_licensed_places=80,
        )

        self.assertEqual(result["source"], "geospatial_supabase")
        self.assertEqual(result["target_geocode_method"], "supabase_match")
        self.assertEqual(supabase.rpc_calls[0][1]["target_lat"], -37.91)

    def test_material_difference_detection(self):
        self.assertTrue(material_supply_difference(
            geospatial={"competitor_count": 8, "total_licensed_places": 900},
            postcode_supply={"competitor_count": 4, "total_licensed_places": 700},
            geospatial_edr=0.45,
            postcode_edr=0.75,
            geospatial_zone="oversupplied",
            postcode_zone="balanced",
        ))

    def test_market_audit_records_competitor_supply_and_scoring_source_warning(self):
        demand = compute_demand("3186", 500)
        market = market_position_score(demand, competitor_count=3, approved_pipeline_places=0, subject_licensed_places=80)
        audit = build_market_audit(
            demand,
            market,
            competitor_source="ACECQA centres table filtered by postcode plus subject centre.",
            included_centres=4,
            competitor_supply={
                "source": "geospatial_supabase",
                "confidence": "medium",
                "radius_km": 2,
                "competitor_count": 6,
                "total_licensed_places": 720,
                "target_geocode_method": "google",
                "exclusion_method": None,
                "scoring_source": "postcode_fallback",
                "scoring_confidence": "medium",
                "compared_to_postcode": {"competitor_count": 3, "total_licensed_places": 500, "edr": 0.86},
                "material_difference": True,
                "warnings": [],
            },
        )

        self.assertEqual(audit["competitor_supply"]["source"], "geospatial_supabase")
        self.assertTrue(audit["competitor_supply"]["material_difference"])
        warning_text = " ".join(audit["warnings"])
        self.assertIn("differs materially", warning_text)
        self.assertIn("retained postcode fallback", warning_text)

    def test_structured_pipeline_projects_count_approved_places_at_full_weight(self):
        supply = build_pipeline_supply([
            {"name": "New ELC", "status": "approved", "proposed_places": 88, "confidence": "high"},
            {"name": "Build ELC", "status": "under_construction", "proposed_places": 72},
        ])

        self.assertEqual(supply["pipeline_audit"]["approved_places"], 160)
        self.assertEqual(supply["pipeline_audit"]["risk_adjusted_places"], 160)
        self.assertFalse(supply["pipeline_audit"]["search_required"])
        self.assertEqual(supply["pipeline_projects"][0]["source_type"], "manual_structured")

    def test_lodged_projects_are_risk_adjusted_but_not_approved_places(self):
        supply = build_pipeline_supply([
            {"name": "Lodged ELC", "status": "lodged", "proposed_places": 80},
        ])

        self.assertEqual(supply["pipeline_audit"]["approved_places"], 0)
        self.assertEqual(supply["pipeline_audit"]["lodged_places"], 80)
        self.assertEqual(supply["pipeline_audit"]["risk_adjusted_places"], 40)
        self.assertEqual(supply["pipeline_audit"]["lodged_weight"], 0.5)

    def test_refused_withdrawn_and_opened_projects_are_not_counted(self):
        supply = build_pipeline_supply([
            {"name": "Refused ELC", "status": "refused", "proposed_places": 90},
            {"name": "Withdrawn ELC", "status": "withdrawn", "proposed_places": 80},
            {"name": "Opened ELC", "status": "opened", "proposed_places": 70},
        ])

        audit = supply["pipeline_audit"]
        self.assertEqual(audit["approved_places"], 0)
        self.assertEqual(audit["risk_adjusted_places"], 0)
        self.assertTrue(any("marked opened" in warning for warning in audit["warnings"]))

    def test_no_pipeline_source_requires_da_search_without_implying_zero_supply(self):
        supply = build_pipeline_supply()

        audit = supply["pipeline_audit"]
        self.assertTrue(audit["search_required"])
        self.assertFalse(audit["searched"])
        self.assertEqual(audit["source_type"], "none")
        self.assertTrue(any("DA search is required" in warning for warning in audit["warnings"]))

    def test_legacy_approved_das_keep_existing_90_place_assumption(self):
        class LegacyIntel:
            approved_das = 2
            lodged_applications = 1
            permit_sites = 1
            notes = "Manual count"

        supply = build_pipeline_supply(pipeline_intel=LegacyIntel())

        audit = supply["pipeline_audit"]
        self.assertEqual(audit["approved_places"], 180)
        self.assertEqual(audit["lodged_places"], 75)
        self.assertEqual(audit["risk_adjusted_places"], 218)
        self.assertEqual(audit["source_type"], "manual_legacy_count")
        self.assertTrue(any(p["source_type"] == "manual_legacy_count" for p in supply["pipeline_projects"]))

    def test_empty_structured_projects_do_not_override_legacy_counts(self):
        class LegacyIntel:
            approved_das = 1
            lodged_applications = 0
            permit_sites = 0
            notes = None

        supply = build_pipeline_supply(
            pipeline_projects=[{}, {"status": "unknown", "name": "   ", "address": ""}],
            pipeline_intel=LegacyIntel(),
        )

        audit = supply["pipeline_audit"]
        self.assertEqual(audit["approved_places"], 90)
        self.assertEqual(audit["source_type"], "manual_legacy_count")
        self.assertEqual(len([p for p in supply["pipeline_projects"] if p["source_type"] == "manual_structured"]), 0)
        self.assertTrue(any("ignored" in warning for warning in audit["warnings"]))

    def test_partial_project_missing_places_is_not_counted(self):
        supply = build_pipeline_supply([
            {"name": "Approved but capacity unknown", "status": "approved", "proposed_places": ""},
            {"name": "Lodged malformed places", "status": "lodged", "proposed_places": "about 80"},
        ])

        audit = supply["pipeline_audit"]
        self.assertEqual(audit["approved_places"], 0)
        self.assertEqual(audit["lodged_places"], 0)
        self.assertEqual(audit["risk_adjusted_places"], 0)
        self.assertTrue(any("proposed places are missing" in warning for warning in audit["warnings"]))

    def test_numeric_string_places_are_accepted_safely(self):
        supply = build_pipeline_supply([
            {"name": "Approved ELC", "status": "approved", "proposed_places": "1,200 places"},
            {"name": "Invalid ELC", "status": "approved", "proposed_places": "abc 90"},
        ])

        audit = supply["pipeline_audit"]
        self.assertEqual(audit["approved_places"], 1200)
        self.assertEqual(supply["pipeline_projects"][0]["proposed_places"], 1200)
        self.assertIsNone(supply["pipeline_projects"][1]["proposed_places"])

    def test_workflow_includes_pipeline_projects_and_audit(self):
        extracted = {
            "meta": {"data_quality": "MEDIUM", "missing_fields": [], "missing_fields_count": 0},
            "centre": {"name": "Pipeline ELC"},
            "financials": {"fy25": {"revenue": 900000, "ebitda": 180000, "total_labour_cost": 510000}},
            "key_ratios": {},
            "occupancy": {"avg_4wk_pct": 72, "avg_13wk_pct": 74},
            "_pipeline_projects": [{"id": "pipe_test", "name": "Approved ELC", "status": "approved", "proposed_places": 90}],
            "_pipeline_audit": {"source_type": "manual_structured", "approved_places": 90, "warnings": []},
        }

        result = build_structured_deal_intelligence(
            extracted=extracted,
            scored={"centre_name": "Pipeline ELC"},
            combined_text="",
            source_files=[],
            file_classes={},
        )

        self.assertEqual(result["pipeline_projects"][0]["id"], "pipe_test")
        self.assertEqual(result["pipeline_audit"]["approved_places"], 90)


if __name__ == "__main__":
    unittest.main()
