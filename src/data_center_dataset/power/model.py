"""Tier C -- power modelled from building floor area.

This is the weakest tier and the widest interval. It exists because footprint
geometry is available for far more facilities than either attested figures or
generator permits, and a bounded estimate with an honest interval is more useful
than a null -- provided it is never confused with a measurement.

    white_space_sqft = footprint_sqft x storeys x white_space_fraction
    it_load_mw       = white_space_sqft x W_per_sqft / 1e6
    annual_gwh       = it_load_mw x PUE x utilization x 8760 / 1000

Power density and PUE priors are read from ``data/reference/
power_density_priors.csv``, keyed on facility class and construction vintage, so
they can be revised without touching this code.

A facility with no footprint measurement receives no Tier C estimate. Treating a
missing polygon as zero area would fabricate a zero-power data center.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import pandas as pd

from ..config import (
    HOURS_PER_YEAR,
    MAX_MODELLED_STOREYS,
    REFERENCE_DIR,
    TIER_AREA,
    UTILIZATION,
    UTILIZATION_CI,
    WHITE_SPACE_FRACTION,
    WHITE_SPACE_FRACTION_CI,
)

log = logging.getLogger(__name__)

DEFAULT_VINTAGE = 2012


@lru_cache(maxsize=1)
def _priors() -> pd.DataFrame:
    path = REFERENCE_DIR / "power_density_priors.csv"
    table = pd.read_csv(path, comment="#")
    numeric = [
        "vintage_min",
        "vintage_max",
        "w_per_sqft_low",
        "w_per_sqft_mid",
        "w_per_sqft_high",
        "pue_low",
        "pue_mid",
        "pue_high",
    ]
    for col in numeric:
        table[col] = pd.to_numeric(table[col], errors="coerce")
    return table


def lookup_prior(facility_class: object, year_built: object) -> dict:
    """Select the density/PUE prior row for a facility class and vintage."""
    table = _priors()
    cls = str(facility_class) if facility_class and not pd.isna(facility_class) else "unknown"
    year = pd.to_numeric(year_built, errors="coerce")
    if pd.isna(year):
        year = DEFAULT_VINTAGE

    subset = table[table.facility_class == cls]
    if subset.empty:
        subset = table[table.facility_class == "unknown"]

    match = subset[(subset.vintage_min <= year) & (subset.vintage_max >= year)]
    row = (match if not match.empty else subset).iloc[0]
    return {
        "facility_class": cls,
        "vintage_used": int(year),
        "w_per_sqft": {
            "low": float(row.w_per_sqft_low),
            "mid": float(row.w_per_sqft_mid),
            "high": float(row.w_per_sqft_high),
        },
        "pue": {
            "low": float(row.pue_low),
            "mid": float(row.pue_mid),
            "high": float(row.pue_high),
        },
    }


def make_pue_lookup():
    """Return a callable giving the PUE prior for a facility row.

    Shared with the Tier A and Tier B estimators so that every tier converts
    between IT load and site energy using the same assumption for a given
    facility.
    """

    def lookup(facility: pd.Series) -> dict:
        return lookup_prior(facility.get("facility_class"), facility.get("year_built"))["pue"]

    return lookup


def estimate(facilities: pd.DataFrame) -> pd.DataFrame:
    """Produce Tier C estimates for every facility with a measured footprint."""
    if facilities.empty:
        return pd.DataFrame()

    rows = []
    for _, fac in facilities.iterrows():
        gross = pd.to_numeric(fac.get("est_gross_sqft"), errors="coerce")
        if pd.isna(gross) or gross <= 0:
            continue

        prior = lookup_prior(fac.get("facility_class"), fac.get("year_built"))
        wsf, pue = prior["w_per_sqft"], prior["pue"]

        partial = bool(fac.get("partial_occupancy"))
        # In a tower the occupied share is unknown, so the lower bound assumes a
        # single storey rather than the capped multiple.
        gross_low = pd.to_numeric(fac.get("est_gross_sqft_min"), errors="coerce")
        if pd.isna(gross_low) or gross_low <= 0:
            gross_low = gross

        white_mid = gross * WHITE_SPACE_FRACTION
        white_low = (gross_low if partial else gross) * WHITE_SPACE_FRACTION_CI[0]
        white_high = gross * WHITE_SPACE_FRACTION_CI[1]

        it_mid = white_mid * wsf["mid"] / 1e6
        it_low = white_low * wsf["low"] / 1e6
        it_high = white_high * wsf["high"] / 1e6

        rows.append(
            {
                "facility_id": fac["facility_id"],
                "method": TIER_AREA,
                "basis": "floor_area_density",
                "it_load_mw": it_mid,
                "ci_low_mw": it_low,
                "ci_high_mw": it_high,
                "annual_gwh": it_mid * pue["mid"] * UTILIZATION * HOURS_PER_YEAR / 1000.0,
                "annual_gwh_low": it_low * pue["low"] * UTILIZATION_CI[0] * HOURS_PER_YEAR / 1000.0,
                "annual_gwh_high": it_high * pue["high"] * UTILIZATION_CI[1] * HOURS_PER_YEAR / 1000.0,
                "pue_used": pue["mid"],
                "est_white_space_sqft": white_mid,
                "partial_occupancy": partial,
                "source_url": None,
                "assumptions_json": json.dumps(
                    {
                        "est_gross_sqft": float(gross),
                        "est_gross_sqft_min": float(gross_low),
                        "white_space_fraction": WHITE_SPACE_FRACTION,
                        "white_space_fraction_ci": list(WHITE_SPACE_FRACTION_CI),
                        "w_per_sqft": wsf,
                        "pue": pue,
                        "utilization": UTILIZATION,
                        "utilization_ci": list(UTILIZATION_CI),
                        "facility_class": prior["facility_class"],
                        "vintage_used": prior["vintage_used"],
                        "partial_occupancy": partial,
                        "storeys_credited_max": MAX_MODELLED_STOREYS,
                        "note": (
                            "Modelled from OSM building footprint. Not a "
                            "measurement of consumption."
                            + (
                                " Building exceeds the credited storey cap, so "
                                "the facility is one tenant of a larger tower "
                                "and its occupied share is unknown; the lower "
                                "bound assumes a single storey."
                                if partial
                                else ""
                            )
                        ),
                    },
                    sort_keys=True,
                ),
            }
        )

    out = pd.DataFrame(rows)
    log.info("tier C: %d estimates from floor area", len(out))
    return out
