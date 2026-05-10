import unittest

from scripts.qa_public_market_context import (
    build_forest_hill_market_audit_sample,
    build_forest_hill_report_payload_sample,
    validate_report_payload,
    validate_sample_market_audit,
)


class PublicMarketContextQaTests(unittest.TestCase):
    def test_forest_hill_sample_contains_optional_context_and_base_audit(self):
        sample = build_forest_hill_market_audit_sample()

        self.assertIn("public_market_benchmark", sample)
        self.assertIn("local_demand_supply", sample)
        self.assertIn("edr", sample)
        self.assertIn("warnings", sample)
        self.assertIn("competitor_count", sample)
        self.assertNotIn("target_facts", sample)
        self.assertNotIn("deal_facts", sample)

    def test_forest_hill_sample_values_match_verified_fixture(self):
        sample = build_forest_hill_market_audit_sample()
        benchmark = sample["public_market_benchmark"]
        model = sample["local_demand_supply"]

        self.assertEqual(benchmark["sa3_code"], "21104")
        self.assertEqual(benchmark["sa3_name"], "Whitehorse-East")
        self.assertEqual(benchmark["children_0_5_using_care"], 2320)
        self.assertEqual(benchmark["cbdc_services"], 28)
        self.assertAlmostEqual(benchmark["children_0_5_per_cbdc_service"], 82.86, places=2)
        self.assertAlmostEqual(benchmark["cbdc_mean_fee_per_hour"], 14.52, places=2)
        self.assertAlmostEqual(benchmark["cbdc_services_above_cap_pct"], 46.43, places=2)
        self.assertAlmostEqual(model["sa3_ccs_participation_rate_0_5"], 0.4462, places=4)
        self.assertAlmostEqual(model["estimated_realised_ccs_demand_0_5"], 1873.8462, places=3)
        self.assertAlmostEqual(model["current_child_per_place"], 1.4991, places=3)
        self.assertAlmostEqual(model["post_entry_child_per_place"], 1.388, places=3)
        self.assertAlmostEqual(model["supply_dilution_pct"], 8.0, places=3)
        self.assertAlmostEqual(model["future_supply_places"], 1378.0, places=3)
        self.assertAlmostEqual(model["future_child_per_place"], 1.3598, places=3)
        self.assertEqual(model["market_capacity_signal"], "stretched")
        self.assertEqual(model["market_capacity_confidence"], "medium")

    def test_forest_hill_sample_preserves_caveats_and_semantics(self):
        sample = build_forest_hill_market_audit_sample()
        benchmark = sample["public_market_benchmark"]
        model = sample["local_demand_supply"]

        self.assertTrue(benchmark["caveats"])
        self.assertTrue(model["caveats"])
        self.assertIn("target_occupancy", benchmark["not_underwriting_use"])
        self.assertIn("target_waitlist", benchmark["not_underwriting_use"])
        self.assertIn("target_revenue", benchmark["not_underwriting_use"])
        self.assertIn("target_ebitda", benchmark["not_underwriting_use"])
        self.assertIn("licensed_place_capacity", benchmark["not_underwriting_use"])
        ledger_not_use = model["evidence_ledger_entry"]["not_underwriting_use"]
        self.assertIn("target_occupancy", ledger_not_use)
        self.assertIn("target_revenue", ledger_not_use)
        self.assertIn("actual_vacancies", ledger_not_use)

    def test_forest_hill_sample_validation_passes(self):
        sample = build_forest_hill_market_audit_sample()

        self.assertEqual(validate_sample_market_audit(sample), [])

    def test_report_payload_places_public_context_under_workflow_market_audit(self):
        payload = build_forest_hill_report_payload_sample()
        workflow = payload["workflow"]
        market_audit = workflow["market_audit"]

        self.assertIn("public_market_benchmark", market_audit)
        self.assertIn("local_demand_supply", market_audit)
        self.assertNotIn("public_market_benchmark", workflow)
        self.assertNotIn("local_demand_supply", workflow)
        self.assertEqual(market_audit["public_market_benchmark"]["sa3_code"], "21104")
        self.assertEqual(market_audit["local_demand_supply"]["market_capacity_signal"], "stretched")
        self.assertEqual(validate_report_payload(payload), [])


if __name__ == "__main__":
    unittest.main()
