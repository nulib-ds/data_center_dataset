"""Tier B -- power inferred from permitted backup generators.

Rationale
---------
Backup generation exists to carry a facility's critical load through a utility
outage, so installed generator capacity is a genuine physical constraint on how
much load the site can serve. Unlike a floor-area heuristic, this is grounded in
equipment the operator actually bought and permitted.

Calibration
-----------
The per-unit prior is measured, not guessed. Of the 817 California data-center
generator units in NEI 2020, 60 state a nameplate rating in free text; their
median is 2116 kW with an interquartile range of 885-2190 kW. Those values
populate ``GENERATOR_KW_*`` in ``sources.epa_nei``.

Chain
-----
    nameplate_mw  = parsed ratings + (unrated units x per-unit prior)
    critical_mw   = nameplate_mw / redundancy_factor
    it_load_mw    = critical_mw x CRITICAL_TO_IT

The redundancy divisor is the dominant source of uncertainty: an N+1 site and a
2N site with identical load carry very different generator counts, and NEI does
not record the configuration. The confidence interval is therefore wide by
construction, spanning 1.10 (barely redundant) to 2.00 (fully mirrored).
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from ..config import (
    CRITICAL_TO_IT,
    GENERATOR_REDUNDANCY_CI,
    GENERATOR_REDUNDANCY_FACTOR,
    HOURS_PER_YEAR,
    TIER_GENERATOR,
    UTILIZATION,
)
from ..sources.epa_nei import (
    GENERATOR_KW_HIGH,
    GENERATOR_KW_LOW,
    GENERATOR_KW_MID,
)

log = logging.getLogger(__name__)


def _nameplate_mw(row: pd.Series, per_unit_kw: float) -> float:
    """Total generator nameplate, mixing parsed ratings with the prior."""
    rated_kw = float(row.get("rated_kw_sum") or 0.0)
    n_units = int(row.get("n_generator_units") or 0)
    n_rated = int(row.get("n_units_rated") or 0)
    unrated = max(n_units - n_rated, 0)
    return (rated_kw + unrated * per_unit_kw) / 1000.0


def estimate(
    facilities: pd.DataFrame,
    generator_inventory: pd.DataFrame,
    crosswalk: pd.DataFrame,
    pue_lookup,
) -> pd.DataFrame:
    """Produce Tier B estimates for facilities with a permitted generator fleet.

    ``crosswalk`` maps ``facility_id`` to NEI ``source_id``, so generator counts
    follow the facility through entity resolution rather than being re-matched
    spatially.
    """
    if generator_inventory is None or generator_inventory.empty:
        return pd.DataFrame()

    nei_links = crosswalk[crosswalk.source == "epa_nei"][["facility_id", "source_id"]]
    if nei_links.empty:
        log.info("tier B: no facilities linked to NEI records")
        return pd.DataFrame()

    inv = generator_inventory.copy()
    inv["source_id"] = inv["source_id"].astype(str)
    nei_links = nei_links.assign(source_id=nei_links.source_id.astype(str))

    joined = nei_links.merge(inv, on="source_id", how="inner")
    # A resolved facility may absorb several NEI permit records (multi-building
    # campuses); sum their fleets.
    agg = (
        joined.groupby("facility_id", as_index=False)
        .agg(
            n_generator_units=("n_generator_units", "sum"),
            n_units_rated=("n_units_rated", "sum"),
            rated_kw_sum=("rated_kw_sum", "sum"),
        )
    )
    agg = agg[agg.n_generator_units > 0]
    if agg.empty:
        return pd.DataFrame()

    fac_index = facilities.set_index("facility_id")
    rows = []

    for _, row in agg.iterrows():
        fid = row.facility_id
        if fid not in fac_index.index:
            continue
        facility = fac_index.loc[fid]
        pue = pue_lookup(facility)

        mid_nameplate = _nameplate_mw(row, GENERATOR_KW_MID)
        low_nameplate = _nameplate_mw(row, GENERATOR_KW_LOW)
        high_nameplate = _nameplate_mw(row, GENERATOR_KW_HIGH)

        it_mid = mid_nameplate / GENERATOR_REDUNDANCY_FACTOR * CRITICAL_TO_IT
        # Low estimate pairs the smallest units with the most redundancy.
        it_low = low_nameplate / GENERATOR_REDUNDANCY_CI[1] * CRITICAL_TO_IT
        it_high = high_nameplate / GENERATOR_REDUNDANCY_CI[0] * CRITICAL_TO_IT

        rows.append(
            {
                "facility_id": fid,
                "method": TIER_GENERATOR,
                "basis": "backup_generator_fleet",
                "it_load_mw": it_mid,
                "ci_low_mw": min(it_low, it_mid),
                "ci_high_mw": max(it_high, it_mid),
                "annual_gwh": it_mid * pue["mid"] * UTILIZATION * HOURS_PER_YEAR / 1000.0,
                "pue_used": pue["mid"],
                "n_generator_units": int(row.n_generator_units),
                "generator_nameplate_mw": mid_nameplate,
                "source_url": "https://www.epa.gov/air-emissions-inventories",
                "assumptions_json": json.dumps(
                    {
                        "n_generator_units": int(row.n_generator_units),
                        "n_units_with_parsed_rating": int(row.n_units_rated),
                        "parsed_rated_kw_sum": float(row.rated_kw_sum),
                        "per_unit_kw_prior": {
                            "low": GENERATOR_KW_LOW,
                            "mid": GENERATOR_KW_MID,
                            "high": GENERATOR_KW_HIGH,
                            "provenance": (
                                "median/IQR of 60 NEI 2020 CA data-center units "
                                "whose nameplate parsed from free text"
                            ),
                        },
                        "nameplate_mw_mid": mid_nameplate,
                        "redundancy_factor": GENERATOR_REDUNDANCY_FACTOR,
                        "redundancy_ci": list(GENERATOR_REDUNDANCY_CI),
                        "critical_to_it": CRITICAL_TO_IT,
                        "pue_mid": pue["mid"],
                        "utilization": UTILIZATION,
                    },
                    sort_keys=True,
                ),
            }
        )

    out = pd.DataFrame(rows)
    log.info("tier B: %d estimates from generator inventories", len(out))
    return out
