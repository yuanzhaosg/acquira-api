"""
test_valuation_multiple.py — Crown Kiddo is the first worked case.

Run: python -m unittest test_valuation_multiple -v
"""

import datetime
import unittest

from valuation_multiple import (
    COMP_SET,
    apply_multiple,
    compute_multiple,
    is_stale,
)


class TestDealTypeGuard(unittest.TestCase):
    def test_freehold_is_not_an_ebitda_multiple(self):
        m = compute_multiple(deal_type="freehold")
        self.assertFalse(m["applicable"])
        self.assertIn("CAP RATE", m["guard"])
        # The exact error this module prevents:
        self.assertIn("Do not borrow a property yield", m["guard"])

    def test_freehold_going_concern_also_guarded(self):
        m = compute_multiple(deal_type="freehold going concern")
        self.assertFalse(m["applicable"])

    def test_unknown_deal_type_guarded(self):
        m = compute_multiple(deal_type="")
        self.assertFalse(m["applicable"])


class TestCompStaleness(unittest.TestCase):
    def test_fresh_within_window(self):
        reviewed = datetime.date.fromisoformat(COMP_SET["last_reviewed"])
        s = is_stale(today=reviewed + datetime.timedelta(days=30))
        self.assertFalse(s["is_stale"])

    def test_stale_past_six_months(self):
        reviewed = datetime.date.fromisoformat(COMP_SET["last_reviewed"])
        s = is_stale(today=reviewed + datetime.timedelta(days=200))
        self.assertTrue(s["is_stale"])
        self.assertIn("re-validate", s["message"])

    def test_multiple_carries_staleness(self):
        m = compute_multiple(deal_type="leasehold", licensed_places=55)
        self.assertIn("comp_staleness", m)


class TestCrownKiddo(unittest.TestCase):
    """71-73 John Paul Drive, Hillside VIC — 55-place leasehold going concern."""

    def setUp(self):
        self.m = compute_multiple(
            deal_type="leasehold",
            licensed_places=55,
            occupancy_pct=71,
            occupancy_declining=True,        # FY26 77->58->60
            nqs_rating="working_towards",
            lease_years_remaining=1,         # expires 2027
            lease_options_years=20,          # 2x10yr options
            owner_operated=True,             # founder pedagogy
            rent_to_revenue_pct=7.0,
            growth_corridor=True,            # City of Melton
        )

    def test_lands_at_bottom_of_band(self):
        rec = self.m["recommended_multiple"]
        # Bottom of the 3–5x band, NOT the IM's 5.0–7.2x.
        self.assertLess(rec["mid"], 4.0)
        self.assertGreaterEqual(rec["low"], 3.0)
        self.assertLessEqual(rec["high"], 5.0)
        # Explicitly below the IM's floor of 5.0x.
        self.assertLess(rec["high"], 5.0)

    def test_net_delta_is_negative(self):
        # Six factors push down, one or two up -> net down.
        self.assertLess(self.m["net_factor_delta"], 0)

    def test_factor_trail_is_auditable(self):
        names = {f["name"] for f in self.m["factors"]}
        self.assertEqual(
            names,
            {"size", "occupancy", "nqs", "lease", "rent", "management", "location"},
        )
        # The big down-drivers are present with rationale.
        downs = {f["name"]: f for f in self.m["factor_summary"]["pushed_down"]}
        self.assertIn("occupancy", downs)
        self.assertIn("nqs", downs)
        self.assertIn("management", downs)
        self.assertIn("DECLINING", downs["occupancy"]["rationale"])

    def test_up_factors_present(self):
        ups = {f["name"] for f in self.m["factor_summary"]["pushed_up"]}
        # Low rent and growth corridor support value.
        self.assertIn("rent", ups)
        self.assertIn("location", ups)

    def test_valuation_on_buyer_normalised_ebitda(self):
        # Buyer-normalised EBITDA ~233,403 (after stripping non-recurring wage
        # reductions and normalising food).
        v = apply_multiple(self.m, normalised_ebitda=233403,
                           licensed_places=55, location_tier="outer_metro_or_compromised")
        val = v["valuation"]
        # Defensible value lands well under the IM's implied $2.4–3.4M ask.
        self.assertLess(val["high"], 1_000_000)
        self.assertGreater(val["low"], 600_000)

    def test_per_place_cross_check_runs(self):
        v = apply_multiple(self.m, normalised_ebitda=233403, licensed_places=55)
        cross = v["per_place_cross_check"]
        self.assertIsNotNone(cross)
        self.assertEqual(cross["per_place_band_aud"], [20000, 30000])


class TestStrongMetroContrast(unittest.TestCase):
    """A strong metro centre should land high in the band — proves the factors move."""

    def test_strong_metro_lands_upper_band(self):
        m = compute_multiple(
            deal_type="leasehold",
            licensed_places=110,
            occupancy_pct=92,
            occupancy_declining=False,
            nqs_rating="exceeding",
            lease_years_remaining=15,
            lease_options_years=20,
            owner_operated=False,
            rent_to_revenue_pct=8.0,
            growth_corridor=True,
        )
        rec = m["recommended_multiple"]
        self.assertGreater(rec["mid"], 4.0)
        self.assertGreater(m["net_factor_delta"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
