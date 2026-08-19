"""Tier resolution and top-down reconciliation.

Two jobs:

``resolve``
    Collapse the multi-row ``power_estimates`` table into one preferred figure
    per facility, by tier precedence (attested > generator > area). The chosen
    tier is recorded alongside the value so consumers can filter on evidence
    quality.

``reconcile``
    Compare the bottom-up statewide total against an independent top-down
    anchor. This is the check that stops a plausible-looking model from being
    quietly wrong by an order of magnitude. The result is *reported*, and a
    calibration factor is only applied when explicitly requested -- and even
    then it never touches Tier A rows, because rescaling an attested,
    cited figure would be falsification.
"""

from __future__ import annotations

import logging

import pandas as pd

from ..config import (
    CA_ANNUAL_TWH_ANCHOR,
    CA_ANNUAL_TWH_ANCHOR_RANGE,
    TIER_ATTESTED,
    TIER_PRECEDENCE,
)

log = logging.getLogger(__name__)

_RANK = {tier: i for i, tier in enumerate(TIER_PRECEDENCE)}


def resolve(estimates: pd.DataFrame) -> pd.DataFrame:
    """Pick the highest-precedence estimate per facility."""
    if estimates.empty:
        return pd.DataFrame(
            columns=[
                "facility_id",
                "best_power_mw",
                "power_ci_low_mw",
                "power_ci_high_mw",
                "power_tier",
                "est_annual_gwh",
                "n_power_methods",
            ]
        )

    work = estimates.copy()
    work["_rank"] = work.method.map(_RANK).fillna(99)
    work = work.sort_values(["facility_id", "_rank"])

    best = work.groupby("facility_id", as_index=False).first()
    counts = work.groupby("facility_id", as_index=False).method.nunique()
    counts = counts.rename(columns={"method": "n_power_methods"})

    out = best.merge(counts, on="facility_id", how="left")[
        [
            "facility_id",
            "it_load_mw",
            "ci_low_mw",
            "ci_high_mw",
            "method",
            "annual_gwh",
            "n_power_methods",
        ]
    ].rename(
        columns={
            "it_load_mw": "best_power_mw",
            "ci_low_mw": "power_ci_low_mw",
            "ci_high_mw": "power_ci_high_mw",
            "method": "power_tier",
            "annual_gwh": "est_annual_gwh",
        }
    )

    log.info(
        "resolve: %d facilities with a power figure (%s)",
        len(out),
        out.power_tier.value_counts().to_dict(),
    )
    return out


def agreement_report(estimates: pd.DataFrame) -> pd.DataFrame:
    """Where two tiers cover the same facility, quantify how far apart they are.

    This is the dataset's internal validity check: if the generator proxy and
    the floor-area model systematically disagree, the priors need revisiting.
    """
    if estimates.empty:
        return pd.DataFrame()

    pivot = estimates.pivot_table(
        index="facility_id", columns="method", values="it_load_mw", aggfunc="first"
    )
    rows = []
    for left, right in (
        (TIER_PRECEDENCE[0], TIER_PRECEDENCE[1]),
        (TIER_PRECEDENCE[0], TIER_PRECEDENCE[2]),
        (TIER_PRECEDENCE[1], TIER_PRECEDENCE[2]),
    ):
        if left not in pivot.columns or right not in pivot.columns:
            continue
        both = pivot[[left, right]].dropna()
        if both.empty:
            continue
        ratio = both[right] / both[left]
        rows.append(
            {
                "pair": f"{right} / {left}",
                "n_facilities": int(len(both)),
                "median_ratio": float(ratio.median()),
                "p25_ratio": float(ratio.quantile(0.25)),
                "p75_ratio": float(ratio.quantile(0.75)),
            }
        )
    return pd.DataFrame(rows)


def reconcile(
    facilities: pd.DataFrame, *, apply_calibration: bool = False
) -> tuple[pd.DataFrame, dict]:
    """Compare the bottom-up statewide total against the top-down anchor."""
    total_gwh = pd.to_numeric(facilities.get("est_annual_gwh"), errors="coerce").sum()
    total_twh = float(total_gwh) / 1000.0
    total_mw = float(pd.to_numeric(facilities.get("best_power_mw"), errors="coerce").sum())

    low, high = CA_ANNUAL_TWH_ANCHOR_RANGE
    within = low <= total_twh <= high
    factor = (CA_ANNUAL_TWH_ANCHOR / total_twh) if total_twh > 0 else None

    report = {
        "bottom_up_it_load_mw": round(total_mw, 1),
        "bottom_up_annual_twh": round(total_twh, 2),
        "top_down_anchor_twh": CA_ANNUAL_TWH_ANCHOR,
        "top_down_range_twh": list(CA_ANNUAL_TWH_ANCHOR_RANGE),
        "within_anchor_range": bool(within),
        "implied_calibration_factor": round(factor, 3) if factor else None,
        "calibration_applied": False,
        "n_facilities_with_power": int(
            pd.to_numeric(facilities.get("best_power_mw"), errors="coerce").notna().sum()
        ),
    }

    out = facilities.copy()

    if apply_calibration and factor and not within:
        # Never rescale attested figures.
        adjustable = out.power_tier != TIER_ATTESTED
        for col in ("best_power_mw", "power_ci_low_mw", "power_ci_high_mw", "est_annual_gwh"):
            if col in out.columns:
                out.loc[adjustable, col] = (
                    pd.to_numeric(out.loc[adjustable, col], errors="coerce") * factor
                )
        report["calibration_applied"] = True
        report["calibration_excluded_tier"] = TIER_ATTESTED
        log.warning(
            "reconcile: applied calibration factor %.3f to non-attested tiers", factor
        )

    log.info(
        "reconcile: bottom-up %.1f MW IT / %.2f TWh-yr vs anchor %.1f TWh (%s)",
        total_mw,
        total_twh,
        CA_ANNUAL_TWH_ANCHOR,
        "within range" if within else "OUTSIDE range",
    )
    return out, report
