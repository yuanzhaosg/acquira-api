import json
import unittest

from demand_service import attach_optional_public_market_context, build_market_audit


def _base_demand_context():
    return {
        "radius_km": 3.0,
        "is_regional": False,
        "estimated_kids_0_to_4": 1200,
        "total_licensed_places": 900,
        "ldc_util_rate": {"mid": 0.475},
        "adj_kids_per_place": {"mid": 0.63},
        "zone": "balanced",
        "confidence": "medium",
        "abs_hit": True,
        "detail": {"coverage_pct": 82, "year_estimate": 2026, "postcode_area_km2": 28},
    }


def _base_market_context():
    return {
        "edr_mid": 0.63,
        "competitor_count": 5,
        "approved_pipeline_places": 40,
        "confidence": "medium",
    }


def _sample_public_market_benchmark():
    return {
        "source": "Department of Education CCS quarterly data",
        "source_quality": "authoritative_public_aggregate",
        "as_of_quarter": "Dec 2025",
        "sa3_code": "21104",
        "sa3_name": "Whitehorse-East",
        "children_0_5_using_care": 2320,
        "children_6_plus_using_care": 1390,
        "total_children_using_care": 3710,
        "families_using_care": 2840,
        "all_approved_services": 38,
        "cbdc_services": 28,
        "children_0_5_per_cbdc_service": 82.86,
        "total_children_per_all_service": 97.63,
        "cbdc_density_per_1000_children_0_5": 12.07,
        "cbdc_mean_fee_per_hour": 14.52,
        "cbdc_fee_growth_yoy_pct": 4.69,
        "cbdc_services_above_cap_pct": 46.4,
        "caveats": [
            "CCS data is public aggregate market evidence, not target-level evidence.",
            "Children using care measures realised CCS usage, not total latent demand.",
            "Approved services are operating services, not licensed places or actual vacancies.",
        ],
        "underwriting_use": [
            "market_depth_benchmark",
            "cbdc_pricing_benchmark",
            "fee_cap_pressure_screen",
            "new_entrant_plausibility_context",
        ],
        "not_underwriting_use": [
            "target_occupancy",
            "target_waitlist",
            "target_revenue",
            "target_ebitda",
            "definitive_unmet_demand",
            "licensed_place_capacity",
            "actual_vacancies",
        ],
        "ignored_extra_field": "should not be copied",
    }


def _sample_local_demand_supply():
    return {
        "sa3_ccs_participation_rate_0_5": 0.4462,
        "estimated_realised_ccs_demand_0_5": 1873.85,
        "current_cbdc_approved_places": 1250,
        "current_child_per_place": 1.5,
        "proposed_new_places": 100,
        "post_entry_child_per_place": 1.39,
        "supply_dilution_pct": 8.0,
        "future_supply_places": 1378,
        "future_child_per_place": 1.36,
        "market_capacity_signal": "stretched",
        "market_capacity_confidence": "medium",
        "signal_reasons": [
            "Whitehorse-East SA3 benchmark is below the state median for 0-5/CBDC.",
            "Proposed new places create 8.0% supply dilution.",
            "Future pipeline further lowers child-per-place ratio.",
            "This is a capacity screen, not centre-level performance evidence.",
        ],
        "caveats": [
            "CCS demand is allocated from SA3 to catchment using population assumptions.",
            "Approved places measure licensed capacity, not actual vacancies.",
        ],
        "evidence_ledger_entry": {
            "provenance": "derived",
            "source_quality": "mixed_public_sources",
            "trust": "medium",
            "underwriting_use": [
                "local_demand_supply_screen",
                "new_entrant_plausibility",
                "occupancy_upside_plausibility",
                "supply_pressure_benchmark",
            ],
            "not_underwriting_use": [
                "target_occupancy",
                "target_waitlist",
                "target_revenue",
                "target_ebitda",
                "definitive_unmet_demand",
                "actual_vacancies",
            ],
        },
        "ignored_extra_field": "should not be copied",
    }


class PublicMarketContextTests(unittest.TestCase):
    def test_market_audit_unchanged_without_public_market_context(self):
        audit = build_market_audit(
            _base_demand_context(),
            _base_market_context(),
            competitor_source="ACECQA",
            included_centres=6,
            pipeline_source="Manual DA check",
            pipeline_searched=True,
        )

        self.assertNotIn("public_market_benchmark", audit)
        self.assertNotIn("local_demand_supply", audit)

    def test_public_market_benchmark_attaches_without_changing_existing_fields(self):
        base = build_market_audit(
            _base_demand_context(),
            _base_market_context(),
            competitor_source="ACECQA",
            included_centres=6,
            pipeline_source="Manual DA check",
            pipeline_searched=True,
        )
        attached = build_market_audit(
            _base_demand_context(),
            _base_market_context(),
            competitor_source="ACECQA",
            included_centres=6,
            pipeline_source="Manual DA check",
            pipeline_searched=True,
            public_market_benchmark=_sample_public_market_benchmark(),
        )

        benchmark = attached.pop("public_market_benchmark")
        self.assertEqual(base, attached)
        self.assertEqual(benchmark["sa3_code"], "21104")
        self.assertEqual(benchmark["children_0_5_per_cbdc_service"], 82.86)
        self.assertIn("target_occupancy", benchmark["not_underwriting_use"])
        self.assertNotIn("ignored_extra_field", benchmark)

    def test_local_demand_supply_attaches_without_changing_existing_fields(self):
        base = build_market_audit(
            _base_demand_context(),
            _base_market_context(),
            competitor_source="ACECQA",
            included_centres=6,
            pipeline_source="Manual DA check",
            pipeline_searched=True,
        )
        attached = build_market_audit(
            _base_demand_context(),
            _base_market_context(),
            competitor_source="ACECQA",
            included_centres=6,
            pipeline_source="Manual DA check",
            pipeline_searched=True,
            local_demand_supply=_sample_local_demand_supply(),
        )

        model = attached.pop("local_demand_supply")
        self.assertEqual(base, attached)
        self.assertEqual(model["market_capacity_signal"], "stretched")
        self.assertEqual(model["market_capacity_confidence"], "medium")
        self.assertIn("target_waitlist", model["evidence_ledger_entry"]["not_underwriting_use"])
        self.assertNotIn("ignored_extra_field", model)

    def test_missing_local_demand_supply_inputs_do_not_crash(self):
        audit = attach_optional_public_market_context(
            {"warnings": []},
            local_demand_supply={},
        )

        self.assertEqual(audit["local_demand_supply"]["status"], "unavailable")
        self.assertTrue(audit["local_demand_supply"]["caveats"])
        self.assertTrue(audit["local_demand_supply"]["signal_reasons"])

    def test_caveats_and_not_underwriting_use_are_preserved(self):
        audit = attach_optional_public_market_context(
            {},
            public_market_benchmark=_sample_public_market_benchmark(),
            local_demand_supply=_sample_local_demand_supply(),
        )

        self.assertTrue(audit["public_market_benchmark"]["caveats"])
        self.assertIn("target_revenue", audit["public_market_benchmark"]["not_underwriting_use"])
        self.assertTrue(audit["local_demand_supply"]["caveats"])
        self.assertIn(
            "target_ebitda",
            audit["local_demand_supply"]["evidence_ledger_entry"]["not_underwriting_use"],
        )

    def test_new_human_facing_output_avoids_forbidden_language(self):
        audit = attach_optional_public_market_context(
            {},
            public_market_benchmark=_sample_public_market_benchmark(),
            local_demand_supply=_sample_local_demand_supply(),
        )

        text_values = []

        def collect_strings(value):
            if isinstance(value, dict):
                for nested in value.values():
                    collect_strings(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_strings(nested)
            elif isinstance(value, str):
                text_values.append(value.lower())

        collect_strings(audit)
        text = " ".join(text_values)
        for phrase in [
            "demand per centre",
            "proof of demand",
            "proof of occupancy",
            "actual demand",
            "true demand",
        ]:
            self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
