"""
valuation_multiple.py — Acquira deterministic valuation multiple engine.

Codifies the childcare *business-sale* (going-concern, leasehold) multiple the
way a disciplined buy-side analyst would defend it:

  1. Deal-type guard. A leasehold business sale is valued on a multiple of
     normalised EBITDA. A freehold/property sale is valued on a cap rate / yield.
     These are DIFFERENT MARKETS. We never borrow a property yield to justify a
     business multiple (the exact error this module exists to prevent).

  2. A versioned comp set (COMP_SET below) of current business-sale evidence,
     with a `last_reviewed` date and a 6-month staleness flag — multiples move
     (the market repriced ~90–130bps in a single year), so a stale comp set must
     announce itself and prompt a re-search rather than silently anchor on old
     numbers.

  3. Up/down factor adjustments off a base band, each one explicit, signed, and
     carrying its rationale — so the output is an audit trail, not a black box.
     Python is authoritative; any LLM layer explains these factors, never invents
     the multiple.

Usage:
    from valuation_multiple import compute_multiple, apply_multiple
    m = compute_multiple(deal_type="leasehold", licensed_places=55,
                         occupancy_pct=71, nqs_rating="working_towards",
                         lease_years_remaining=1, lease_options_years=20,
                         owner_operated=True, rent_to_revenue_pct=7.0,
                         growth_corridor=True)
    val = apply_multiple(m, normalised_ebitda=233403)
"""

from __future__ import annotations

import datetime
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Comp set — CURRENT business-sale multiple evidence (leasehold, adj. EBITDA).
# Refresh cadence: every 6 months. Update `last_reviewed` and the sources when
# re-validated. is_stale() flags when this is older than STALE_AFTER_DAYS.
# NOTE: property/freehold YIELD comps are deliberately NOT stored here — they do
# not belong to the business-multiple market and must never feed this engine.
# ─────────────────────────────────────────────────────────────────────────────
COMP_SET: dict[str, Any] = {
    "last_reviewed": "2026-06-02",
    "review_cadence_days": 182,
    "currency": "AUD",
    "asset_class": "childcare_business_sale_leasehold_going_concern",
    "basis": "adjusted_EBITDA",
    # Defensible band for a SINGLE leasehold centre, from the sources below.
    "band": {"floor": 3.0, "ceiling": 5.0},
    # Premium tier (does NOT apply to single centres) — kept only so the engine
    # can explain why it is excluded if someone cites a 10–12x PE print.
    "premium_tier_note": (
        "10–12x prints (e.g. Young Academics / Seidler) are 40+ centre platforms "
        "with development pipelines — not applicable to a single-centre business sale."
    ),
    "sources": [
        {"name": "Benchmark Business Sales & Valuations", "as_of": "2026-05",
         "range": [3.0, 5.0],
         "note": "owner-operated sells on PEBITDA (lower); with a management layer the "
                 "EBITDA multiple is ~0.5–0.7x higher."},
        {"name": "Miro Capital", "as_of": "2026-04",
         "range": [3.0, 5.0],
         "note": "metro 80+ places & 85%+ occupancy = 4.5–5x; regional ~3x; 3x is the floor."},
        {"name": "ChildcareLink", "as_of": "2026-04",
         "range": [3.0, 5.0],
         "note": "3–5x adjusted EBITDA; 'Exceeding' NQS commands more than 'Working Towards'."},
        {"name": "Business-Sales.info", "as_of": "2025-05",
         "range": [4.0, 4.0],
         "note": "single quality centre ~4x; scale/PE 10–12x (excluded for single centre)."},
    ],
    # Per-place sanity-check band (Miro Capital, Apr 2026) for cross-validation.
    "per_place_aud": {"metro": [30000, 45000], "regional": [15000, 25000],
                      "outer_metro_or_compromised": [20000, 30000]},
}

STALE_AFTER_DAYS = COMP_SET["review_cadence_days"]

VALID_DEAL_TYPES = {"leasehold", "freehold", "freehold_going_concern"}
VALID_NQS = {"excellent", "exceeding", "meeting", "working_towards", "significant_improvement"}


def is_stale(today: Optional[datetime.date] = None) -> dict[str, Any]:
    """Return staleness status of the comp set against the 6-month cadence."""
    today = today or datetime.date.today()
    reviewed = datetime.date.fromisoformat(COMP_SET["last_reviewed"])
    age_days = (today - reviewed).days
    stale = age_days > STALE_AFTER_DAYS
    return {
        "last_reviewed": COMP_SET["last_reviewed"],
        "age_days": age_days,
        "stale_after_days": STALE_AFTER_DAYS,
        "is_stale": stale,
        "message": (
            f"Comp set last reviewed {COMP_SET['last_reviewed']} ({age_days}d ago) — "
            + ("STALE: re-validate against current business-sale evidence before relying on the multiple."
               if stale else
               "within the 6-month review window.")
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Factor adjustments. Each returns a signed delta to the multiple plus rationale.
# Anchored to the band midpoint; the net of all factors is clamped to the band.
# These encode the up/down reasoning: size, occupancy, NQS, lease tail,
# rent ratio, management depth, location.
# ─────────────────────────────────────────────────────────────────────────────
def _factor_size(places: Optional[int]) -> tuple[float, str, str]:
    if places is None:
        return 0.0, "neutral", "Licensed places unknown — no size adjustment."
    if places >= 100:
        return +0.50, "up", f"{places} places — large centre; supports upper band."
    if places >= 80:
        return +0.25, "up", f"{places} places — meets the 80+ that earns 4.5–5x at strong occupancy."
    if places >= 60:
        return 0.0, "neutral", f"{places} places — mid-size; neutral."
    return -0.25, "down", f"{places} places — below the 80+ that earns the top of the band."


def _factor_occupancy(occ_pct: Optional[float], declining: Optional[bool]) -> tuple[float, str, str]:
    if occ_pct is None:
        return 0.0, "neutral", "Occupancy unknown — no adjustment; verify before relying on the multiple."
    if occ_pct >= 90:
        d = +0.50
        return d, "up", f"{occ_pct:.0f}% occupancy — at/above the 85%+ that earns the top of the band."
    if occ_pct >= 85:
        return +0.25, "up", f"{occ_pct:.0f}% occupancy — meets the 85%+ threshold for the upper band."
    if occ_pct >= 75:
        base = -0.10
    elif occ_pct >= 65:
        base = -0.35
    else:
        base = -0.60
    label = f"{occ_pct:.0f}% occupancy — below the 85% that earns the top of the band."
    if declining:
        base -= 0.25
        label += " Trend is DECLINING (extra discount; underwrite off the recent run, not the peak)."
    return base, "down", label


def _factor_nqs(nqs: Optional[str]) -> tuple[float, str, str]:
    n = (nqs or "").strip().lower().replace(" ", "_").replace("-", "_")
    if n in ("excellent", "exceeding"):
        return +0.40, "up", f"NQS {nqs} — premium rating; commands more than Meeting/Working Towards."
    if n == "meeting":
        return 0.0, "neutral", "NQS Meeting — neutral (the market baseline)."
    if n in ("working_towards", "significant_improvement"):
        return -0.40, "down", f"NQS {nqs} — discounts vs Meeting/Exceeding; buyers price in remediation."
    return 0.0, "neutral", "NQS rating unknown — no adjustment."


def _factor_lease(years_remaining: Optional[float], options_years: Optional[float]) -> tuple[float, str, str]:
    if years_remaining is None:
        return 0.0, "neutral", "Lease tail unknown — no adjustment; confirm before relying on the multiple."
    effective = years_remaining + (options_years or 0)
    if years_remaining < 3 and (options_years or 0) > 0:
        return -0.35, "down", (
            f"Lease ~{years_remaining:g}yr to expiry before options ({effective:g}yr incl options) — "
            "options MUST be confirmed/renewed pre-sale; a short pre-option tail spooks buyers and lenders.")
    if effective < 10:
        return -0.50, "down", f"~{effective:g}yr total incl options — under 10yr; materially discounts value."
    if effective >= 15:
        return +0.15, "up", f"~{effective:g}yr total incl options — long secure tail; supports value."
    return 0.0, "neutral", f"~{effective:g}yr total incl options — adequate; neutral."


def _factor_rent(rent_to_rev_pct: Optional[float]) -> tuple[float, str, str]:
    if rent_to_rev_pct is None:
        return 0.0, "neutral", "Rent-to-revenue unknown — no adjustment."
    if rent_to_rev_pct <= 9:
        return +0.20, "up", f"Rent ~{rent_to_rev_pct:.0f}% of revenue — low occupancy cost; supports value."
    if rent_to_rev_pct <= 13:
        return 0.0, "neutral", f"Rent ~{rent_to_rev_pct:.0f}% of revenue — within normal range."
    if rent_to_rev_pct <= 15:
        return -0.20, "down", f"Rent ~{rent_to_rev_pct:.0f}% of revenue — elevated; pressures margin."
    return -0.45, "down", f"Rent ~{rent_to_rev_pct:.0f}% of revenue — above the 15% danger line; significant discount."


def _factor_management(owner_operated: Optional[bool]) -> tuple[float, str, str]:
    if owner_operated is None:
        return 0.0, "neutral", "Management depth unknown — no adjustment."
    if owner_operated:
        return -0.30, "down", (
            "Owner-operated / owner-dependent — sells on PEBITDA (lower); the buyer must fund a "
            "replacement manager. Pushes to the lower end of the band.")
    return +0.20, "up", "Standalone management layer in place — supports the EBITDA (not PEBITDA) multiple."


def _factor_location(growth_corridor: Optional[bool]) -> tuple[float, str, str]:
    if growth_corridor is None:
        return 0.0, "neutral", "Location/demand context unknown — no adjustment."
    if growth_corridor:
        return +0.15, "up", "High-growth catchment — sustained demand supports value."
    return 0.0, "neutral", "Location neutral for the multiple."


def compute_multiple(
    *,
    deal_type: str,
    licensed_places: Optional[int] = None,
    occupancy_pct: Optional[float] = None,
    occupancy_declining: Optional[bool] = None,
    nqs_rating: Optional[str] = None,
    lease_years_remaining: Optional[float] = None,
    lease_options_years: Optional[float] = None,
    owner_operated: Optional[bool] = None,
    rent_to_revenue_pct: Optional[float] = None,
    growth_corridor: Optional[bool] = None,
    today: Optional[datetime.date] = None,
) -> dict[str, Any]:
    """
    Deterministic business-sale multiple with an auditable factor trail.

    Returns a dict with the recommended multiple range, midpoint, the signed
    factor adjustments, the deal-type guard result, and the comp staleness flag.
    """
    dt = (deal_type or "").strip().lower().replace(" ", "_")

    # ── Deal-type guard ──────────────────────────────────────────────────────
    if dt in ("freehold", "freehold_going_concern"):
        return {
            "applicable": False,
            "deal_type": dt,
            "guard": (
                "Freehold/property sale: value on a CAP RATE / YIELD (net rent ÷ price), "
                "NOT an EBITDA multiple. This engine only produces business-sale multiples. "
                "Do not borrow a property yield to justify a business multiple, or vice versa."
            ),
            "comp_staleness": is_stale(today),
        }
    if dt != "leasehold":
        return {
            "applicable": False,
            "deal_type": dt or "unknown",
            "guard": (
                "Deal type not recognised as a leasehold business sale. Confirm structure "
                "(leasehold business → EBITDA multiple; freehold → yield) before valuing."
            ),
            "comp_staleness": is_stale(today),
        }

    band = COMP_SET["band"]
    floor, ceiling = band["floor"], band["ceiling"]
    midpoint = (floor + ceiling) / 2  # 4.0

    factors = [
        ("size", *_factor_size(licensed_places)),
        ("occupancy", *_factor_occupancy(occupancy_pct, occupancy_declining)),
        ("nqs", *_factor_nqs(nqs_rating)),
        ("lease", *_factor_lease(lease_years_remaining, lease_options_years)),
        ("rent", *_factor_rent(rent_to_revenue_pct)),
        ("management", *_factor_management(owner_operated)),
        ("location", *_factor_location(growth_corridor)),
    ]

    net_delta = sum(f[1] for f in factors)
    raw_mid = midpoint + net_delta
    rec_mid = max(floor, min(ceiling, round(raw_mid, 2)))
    # Range = midpoint ± 0.375 (a ~0.75x-wide band), clamped to the floor/ceiling.
    rec_lo = max(floor, round(rec_mid - 0.375, 2))
    rec_hi = min(ceiling, round(rec_mid + 0.375, 2))

    ups = [f for f in factors if f[2] == "up"]
    downs = [f for f in factors if f[2] == "down"]

    return {
        "applicable": True,
        "deal_type": "leasehold",
        "basis": "adjusted_EBITDA",
        "comp_band": [floor, ceiling],
        "comp_midpoint": midpoint,
        "net_factor_delta": round(net_delta, 2),
        "recommended_multiple": {"low": rec_lo, "mid": rec_mid, "high": rec_hi},
        "factors": [
            {"name": n, "delta": round(d, 2), "direction": dirn, "rationale": why}
            for (n, d, dirn, why) in factors
        ],
        "factor_summary": {
            "pushed_up": [{"name": f[0], "delta": round(f[1], 2), "rationale": f[3]} for f in ups],
            "pushed_down": [{"name": f[0], "delta": round(f[1], 2), "rationale": f[3]} for f in downs],
        },
        "interpretation": _interpret(rec_mid, midpoint, downs, ups),
        "comp_staleness": is_stale(today),
        "comp_sources": COMP_SET["sources"],
    }


def _interpret(rec_mid: float, midpoint: float, downs: list, ups: list) -> str:
    if rec_mid < midpoint - 0.25:
        pos = "bottom of the 3–5x band"
    elif rec_mid > midpoint + 0.25:
        pos = "upper part of the 3–5x band"
    else:
        pos = "middle of the 3–5x band"
    return (
        f"Recommended ~{rec_mid:g}x adjusted EBITDA — {pos}. "
        f"{len(downs)} factor(s) push down, {len(ups)} push up. "
        "Apply to a buyer-NORMALISED EBITDA, not the vendor's reported or 'normalised' profit."
    )


def apply_multiple(
    multiple: dict[str, Any],
    *,
    normalised_ebitda: Optional[float],
    licensed_places: Optional[int] = None,
    location_tier: str = "outer_metro_or_compromised",
) -> dict[str, Any]:
    """
    Apply the recommended multiple to a normalised EBITDA and cross-check against
    the per-place band. If the two methods diverge widely, flag it (a competent
    buyer asks why — per Miro Capital).
    """
    if not multiple.get("applicable") or normalised_ebitda is None:
        return {
            "valuation": None,
            "reason": (multiple.get("guard") if not multiple.get("applicable")
                       else "Normalised EBITDA required to value."),
        }
    rec = multiple["recommended_multiple"]
    val_lo = normalised_ebitda * rec["low"]
    val_mid = normalised_ebitda * rec["mid"]
    val_hi = normalised_ebitda * rec["high"]

    cross = None
    if licensed_places:
        band = COMP_SET["per_place_aud"].get(location_tier) or COMP_SET["per_place_aud"]["outer_metro_or_compromised"]
        pp_lo, pp_hi = licensed_places * band[0], licensed_places * band[1]
        # Divergence test: do the two ranges overlap?
        overlap = not (val_hi < pp_lo or val_lo > pp_hi)
        cross = {
            "per_place_band_aud": band,
            "per_place_valuation": [round(pp_lo), round(pp_hi)],
            "methods_overlap": overlap,
            "note": ("EBITDA-multiple and per-place ranges overlap — methods reconcile."
                     if overlap else
                     "EBITDA-multiple and per-place ranges DO NOT overlap — investigate the "
                     "earnings base or the per-place tier before relying on either."),
        }

    return {
        "normalised_ebitda": round(normalised_ebitda),
        "multiple_applied": rec,
        "valuation": {"low": round(val_lo), "mid": round(val_mid), "high": round(val_hi)},
        "per_place_cross_check": cross,
        "comp_staleness": multiple.get("comp_staleness"),
    }
