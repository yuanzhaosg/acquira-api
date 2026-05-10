import unittest

from local_demand_supply import compute_local_demand_supply


FORBIDDEN_PHRASES = [
    "demand per centre",
    "proof of demand",
    "proof of occupancy",
    "definitive unmet demand",
]


def forest_hill_input() -> dict:
    return {
        "target_address": "303 Springvale Rd, Forest Hill VIC 3131",
        "target_sa3_code": "21104",
        "target_sa3_name": "Whitehorse-East",
        "as_of_quarter": "Dec 2025",
        "sa3_ccs_children_0_5": 2320,
        "sa3_children_0_5_per_cbdc_service": 82.86,
        "state_median_children_0_5_per_cbdc_service": 101.6,
        "national_median_children_0_5_per_cbdc_service": 95.4,
        "sa3_abs_population_0_5": 5200,
        "sa3_population_age_band_used": "0_5",
        "catchment_type": "drive_time",
        "catchment_minutes": 10,
        "catchment_density_class": "middle_ring",
        "catchment_abs_population_0_5": 4200,
        "catchment_population_age_band_used": "0_5",
        "current_cbdc_approved_places": 1250,
        "current_cbdc_service_count": 15,
        "approved_places_source": "fixture_placeholder_state_register_or_estimate",
        "approved_places_confidence": "medium",
        "proposed_new_places": 100,
        "approved_pipeline_places": 80,
        "under_construction_places": 0,
        "lodged_places": 120,
        "lodged_places_probability_weight": 0.4,
    }


class LocalDemandSupplyTests(unittest.TestCase):
    def test_forest_hill_fixture_formulas(self):
        output = compute_local_demand_supply(forest_hill_input())
        self.assertAlmostEqual(output["sa3_ccs_participation_rate_0_5"], 2320 / 5200, places=4)
        self.assertAlmostEqual(output["estimated_realised_ccs_demand_0_5"], 1873.8462, places=3)
        self.assertEqual(output["current_cbdc_approved_places"], 1250.0)
        self.assertEqual(output["current_cbdc_service_count"], 15.0)
        self.assertEqual(output["approved_places_confidence"], "medium")
        self.assertAlmostEqual(output["current_child_per_place"], 1.4991, places=3)
        self.assertEqual(output["proposed_new_places"], 100.0)
        self.assertAlmostEqual(output["post_entry_child_per_place"], 1.388, places=3)
        self.assertAlmostEqual(output["supply_dilution_pct"], 8.0, places=3)
        self.assertAlmostEqual(output["future_supply_places"], 1378, places=3)
        self.assertAlmostEqual(output["future_child_per_place"], 1.36, places=2)
        self.assertIn(output["market_capacity_signal"], {"balanced", "stretched"})
        self.assertNotEqual(output["market_capacity_signal"], "supportive")
        self.assertEqual(output["market_capacity_confidence"], "medium")

    def test_forest_hill_reasons_are_caveated(self):
        output = compute_local_demand_supply(forest_hill_input())
        reasons = " ".join(output["signal_reasons"])
        self.assertIn("below state median", reasons)
        self.assertIn("8.0% supply dilution", reasons)
        self.assertIn("Future pipeline further lowers child-per-place ratio", reasons)
        self.assertIn("capacity screen", reasons)
        self.assertNotIn("proof", reasons.lower())

    def test_missing_critical_inputs_returns_inconclusive(self):
        output = compute_local_demand_supply({"target_sa3_name": "Incomplete"})
        self.assertEqual(output["market_capacity_signal"], "inconclusive")
        self.assertEqual(output["market_capacity_confidence"], "unknown")
        self.assertTrue(any("Critical input missing" in reason for reason in output["signal_reasons"]))

    def test_0_4_approximation_adds_caveats_and_low_confidence(self):
        data = forest_hill_input()
        data.pop("sa3_abs_population_0_5")
        data["sa3_abs_population_0_4"] = 5000
        data.pop("catchment_abs_population_0_5")
        data["catchment_abs_population_0_4"] = 4000
        output = compute_local_demand_supply(data)
        caveats = " ".join(output["caveats"])
        self.assertIn("Participation rate uses CCS 0-5 against ABS 0-4 approximation", caveats)
        self.assertIn("Catchment demand estimate uses ABS 0-4 approximation", caveats)
        self.assertEqual(output["market_capacity_confidence"], "low")
        self.assertEqual(output["market_capacity_signal"], "inconclusive")

    def test_low_approved_places_confidence_keeps_model_inconclusive(self):
        data = forest_hill_input()
        data["approved_places_confidence"] = "low"
        output = compute_local_demand_supply(data)
        self.assertEqual(output["market_capacity_confidence"], "low")
        self.assertEqual(output["market_capacity_signal"], "inconclusive")

    def test_radius_fallback_lowers_confidence(self):
        data = forest_hill_input()
        data["catchment_type"] = "radius"
        output = compute_local_demand_supply(data)
        self.assertEqual(output["market_capacity_confidence"], "low")

    def test_evidence_ledger_semantics(self):
        output = compute_local_demand_supply(forest_hill_input())
        ledger = output["evidence_ledger_entry"]
        self.assertEqual(ledger["provenance"], "derived")
        self.assertEqual(ledger["source_quality"], "mixed_public_sources")
        self.assertEqual(ledger["trust"], "medium")
        self.assertIn("local_demand_supply_screen", ledger["underwriting_use"])
        self.assertIn("new_entrant_plausibility", ledger["underwriting_use"])
        self.assertIn("target_occupancy", ledger["not_underwriting_use"])
        self.assertIn("target_waitlist", ledger["not_underwriting_use"])
        self.assertIn("target_revenue", ledger["not_underwriting_use"])
        self.assertIn("target_ebitda", ledger["not_underwriting_use"])
        self.assertIn("definitive_unmet_demand", ledger["not_underwriting_use"])

    def test_forbidden_phrases_do_not_appear(self):
        output = compute_local_demand_supply(forest_hill_input())
        text = str(output).lower()
        for phrase in FORBIDDEN_PHRASES:
            self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
