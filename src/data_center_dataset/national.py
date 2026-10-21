"""National roll-up of the per-state published tables.

Building 51 states produces 51 directories of tables. That is the right
granularity for inspection, but the common questions -- how much data-centre
capacity does the US have, which states dominate, which tier carries the
evidence -- need one table.

This module reads the *published* per-state tables (not the in-memory build
results) and assembles:

``facilities.{csv,parquet,geojson}``  every state, with a ``state`` column
``power_estimates.{csv,parquet}``     every estimate from every state
``summary.csv``                       one row per state
``reconciliation.json``               national bottom-up total vs the anchor band

Nothing is recomputed. The national tables are a concatenation of what each
state build published, so a disagreement with a per-state table is a bug in
this module rather than a modelling difference.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from .config import PROCESSED_DIR, WGS84
from .states import STATES
from .states import get as get_state_profile

log = logging.getLogger(__name__)

FACILITY_COLUMNS = [
    "state",
    "facility_id",
    "name",
    "operator",
    "operator_confidence",
    "facility_class",
    "lat",
    "lon",
    "address",
    "city",
    "postcode",
    "utility",
    "utility_candidates",
    "year_built",
    "building_levels_reported",
    "building_levels_used",
    "partial_occupancy",
    "footprint_sqft",
    "est_gross_sqft",
    "est_white_space_sqft",
    "best_power_mw",
    "power_ci_low_mw",
    "power_ci_high_mw",
    "power_tier",
    "n_power_methods",
    "est_annual_gwh",
    "pdb_net_count",
    "pdb_ix_count",
    "pdb_carrier_count",
    "clli",
    "website",
    "source_list",
    "n_sources",
]

POWER_COLUMNS = [
    "state",
    "facility_id",
    "method",
    "basis",
    "it_load_mw",
    "ci_low_mw",
    "ci_high_mw",
    "annual_gwh",
    "pue_used",
    "n_generator_units",
    "generator_nameplate_mw",
    "partial_occupancy",
    "stated_value_mw",
    "source_url",
    "retrieved_at",
    "quote",
    "assumptions_json",
]


def _read_state(state: str) -> tuple[pd.DataFrame, pd.DataFrame, dict] | None:
    outdir = PROCESSED_DIR / state
    fac_path = outdir / "facilities.csv"
    if not fac_path.exists():
        return None

    facilities = pd.read_csv(fac_path, low_memory=False)
    facilities.insert(0, "state", state)

    est_path = outdir / "power_estimates.csv"
    if est_path.exists():
        estimates = pd.read_csv(est_path, low_memory=False)
        estimates.insert(0, "state", state)
    else:
        estimates = pd.DataFrame(columns=POWER_COLUMNS[1:])

    recon_path = outdir / "reconciliation.json"
    recon = json.loads(recon_path.read_text()) if recon_path.exists() else {}
    return facilities, estimates, recon


def _summary_row(state: str, facilities: pd.DataFrame, recon: dict) -> dict:
    profile = get_state_profile(state)
    mw = pd.to_numeric(facilities.get("best_power_mw"), errors="coerce")
    gwh = pd.to_numeric(facilities.get("est_annual_gwh"), errors="coerce")
    footprint = pd.to_numeric(facilities.get("footprint_sqft"), errors="coerce")
    return {
        "state": state,
        "state_name": profile.name,
        "facilities": len(facilities),
        "with_footprint": int(footprint.notna().sum()),
        "with_power_estimate": int(mw.notna().sum()),
        "multi_source": int(
            (pd.to_numeric(facilities.get("n_sources"), errors="coerce") > 1).sum()
        ),
        "sum_it_load_mw": round(float(mw.sum(skipna=True)), 1),
        "sum_annual_twh": round(float(gwh.sum(skipna=True)) / 1000.0, 2),
        "tier_a_facilities": int((facilities.get("power_tier") == "A_attested").sum()),
        "tier_b_facilities": int((facilities.get("power_tier") == "B_generator").sum()),
        "tier_c_facilities": int((facilities.get("power_tier") == "C_area").sum()),
        "anchor_twh": profile.annual_twh_anchor,
        "anchor_low_twh": profile.annual_twh_anchor_range[0],
        "anchor_high_twh": profile.annual_twh_anchor_range[1],
        "within_anchor_range": bool(recon.get("within_anchor_range", False)),
    }


def _write_datapackage(
    outdir: Path, fac_out: pd.DataFrame, est_out: pd.DataFrame, report: dict
) -> None:
    """Write a frictionless descriptor for the national tables."""
    from .export import FIELD_DOCS

    def resource(stem: str, df: pd.DataFrame, description: str) -> dict:
        return {
            "name": stem,
            "path": f"{stem}.csv",
            "format": "csv",
            "mediatype": "text/csv",
            "description": description,
            "schema": {
                "fields": [
                    {
                        "name": col,
                        "type": "number"
                        if pd.api.types.is_numeric_dtype(df[col])
                        else "string",
                        "description": FIELD_DOCS.get(col, ""),
                    }
                    for col in df.columns
                ]
            },
        }

    descriptor = {
        "profile": "tabular-data-package",
        "name": "us-data-center-dataset",
        "title": "US Data Centers with Tiered Power Estimates",
        "description": (
            "Registry of commercial (colocation, wholesale, hyperscale) data "
            "centers in the United States, assembled per state from "
            "OpenStreetMap, PeeringDB and the EPA National Emissions Inventory. "
            "Power figures are ESTIMATES carrying an explicit evidence tier and "
            "confidence interval; none is a metered consumption reading. See "
            "LIMITATIONS.md before use."
        ),
        "licenses": [
            {
                "name": "ODbL-1.0",
                "title": "Open Database License 1.0",
                "path": "https://opendatacommons.org/licenses/odbl/1-0/",
            }
        ],
        "sources": [
            {
                "title": "OpenStreetMap (Overpass API)",
                "path": "https://www.openstreetmap.org/copyright",
                "license": "ODbL-1.0",
                "role": "Location, building footprint, operator, construction year",
            },
            {
                "title": "PeeringDB",
                "path": "https://www.peeringdb.com/",
                "license": "CC-BY-4.0",
                "role": "Colocation facility registry, interconnection counts",
            },
            {
                "title": "EPA National Emissions Inventory 2020",
                "path": "https://www.epa.gov/air-emissions-inventories",
                "license": "US-PD",
                "role": "Facility recall and backup-generator inventory (Tier B)",
            },
            {
                "title": "California Energy Commission GIS",
                "path": "https://cecgis-caenergy.opendata.arcgis.com/",
                "license": "US-PD",
                "role": "Electric utility service territories (California only)",
            },
        ],
        "resources": [
            resource(
                "facilities",
                fac_out,
                "One row per resolved commercial data center site, all states.",
            ),
            resource(
                "power_estimates",
                est_out,
                "Every power estimate from every method, all states. Not collapsed.",
            ),
            resource(
                "summary",
                pd.DataFrame(report.get("summary_rows", [])),
                "One row per state: facility counts, tier mix, IT MW, TWh/yr, anchor band.",
            ),
        ],
        "reconciliation": report,
    }
    (outdir / "datapackage.json").write_text(
        json.dumps(descriptor, indent=2, sort_keys=True, default=str)
    )


def _stringify_mixed(df: pd.DataFrame) -> pd.DataFrame:
    """Cast object columns to pandas' nullable string type.

    Concatenating 51 per-state tables can produce a column that is an integer
    in some states and a string in others -- a postcode written ``85206`` in
    one state and ``85206`` in another. Parquet cannot store that; CSV can,
    but then the two files disagree about the same table. Casting to
    nullable string fixes both and keeps missing values as missing.
    """
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            try:
                out[col] = out[col].astype("string")
            except (TypeError, ValueError):  # pragma: no cover - exotic dtype
                pass
    return out


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write parquet, degrading to CSV-only rather than failing the roll-up."""
    try:
        df.to_parquet(path, index=False)
    except Exception as exc:  # pragma: no cover - mixed dtypes
        log.warning("national: parquet write failed for %s (%s)", path.name, exc)


def build(outdir: Path | None = None) -> dict:
    """Assemble the national tables from the published per-state tables."""
    outdir = outdir or (PROCESSED_DIR / "national")
    outdir.mkdir(parents=True, exist_ok=True)

    facilities: list[pd.DataFrame] = []
    estimates: list[pd.DataFrame] = []
    summary: list[dict] = []
    missing: list[str] = []

    for state in sorted(STATES):
        loaded = _read_state(state)
        if loaded is None:
            missing.append(state)
            continue
        fac, est, recon = loaded
        facilities.append(fac)
        estimates.append(est)
        summary.append(_summary_row(state, fac, recon))

    if not facilities:
        raise FileNotFoundError(
            "no per-state outputs found; run `data-center-dataset build-all` first"
        )

    fac_all = pd.concat(facilities, ignore_index=True)
    est_all = pd.concat(estimates, ignore_index=True) if estimates else pd.DataFrame()

    for col in FACILITY_COLUMNS:
        if col not in fac_all.columns:
            fac_all[col] = pd.NA
    fac_out = _stringify_mixed(fac_all[FACILITY_COLUMNS])

    if not est_all.empty:
        for col in POWER_COLUMNS:
            if col not in est_all.columns:
                est_all[col] = pd.NA
        est_out = _stringify_mixed(est_all[POWER_COLUMNS])
    else:
        est_out = pd.DataFrame(columns=POWER_COLUMNS)

    fac_out.to_csv(outdir / "facilities.csv", index=False)
    _write_parquet(fac_out, outdir / "facilities.parquet")
    est_out.to_csv(outdir / "power_estimates.csv", index=False)
    _write_parquet(est_out, outdir / "power_estimates.parquet")

    geo_cols = [c for c in FACILITY_COLUMNS if c in fac_out.columns]
    gdf = gpd.GeoDataFrame(
        fac_out[geo_cols].copy(),
        geometry=[Point(xy) for xy in zip(fac_out.lon, fac_out.lat)],
        crs=WGS84,
    )
    gdf.to_file(outdir / "facilities.geojson", driver="GeoJSON")

    summary_df = pd.DataFrame(summary).sort_values("sum_it_load_mw", ascending=False)
    summary_df.to_csv(outdir / "summary.csv", index=False)

    mw = pd.to_numeric(fac_out.best_power_mw, errors="coerce")
    gwh = pd.to_numeric(fac_out.est_annual_gwh, errors="coerce")
    bottom_up_twh = float(gwh.sum(skipna=True)) / 1000.0
    anchor_low = sum(
        get_state_profile(s).annual_twh_anchor_range[0] for s in summary_df.state
    )
    anchor_high = sum(
        get_state_profile(s).annual_twh_anchor_range[1] for s in summary_df.state
    )
    anchor_point = sum(get_state_profile(s).annual_twh_anchor for s in summary_df.state)

    report = {
        "states_built": len(summary_df),
        "states_missing": missing,
        "facilities": int(len(fac_out)),
        "facilities_with_power_estimate": int(mw.notna().sum()),
        "bottom_up_it_load_mw": round(float(mw.sum(skipna=True)), 1),
        "bottom_up_annual_twh": round(bottom_up_twh, 2),
        "top_down_anchor_twh_sum": round(anchor_point, 2),
        "top_down_range_twh": [round(anchor_low, 2), round(anchor_high, 2)],
        "within_anchor_range": bool(anchor_low <= bottom_up_twh <= anchor_high),
        "note": (
            "The national anchor band is the sum of the per-state bands, each "
            "of which is an order-of-magnitude heuristic, so the union is wide "
            "by construction. It is a smell test, not a measurement."
        ),
    }
    (outdir / "reconciliation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True)
    )

    _write_datapackage(
        outdir,
        fac_out,
        est_out,
        {**report, "summary_rows": summary_df.to_dict(orient="records")},
    )

    log.info(
        "national: %d states, %d facilities, %.0f MW IT, %.1f TWh/yr",
        len(summary_df),
        len(fac_out),
        report["bottom_up_it_load_mw"],
        report["bottom_up_annual_twh"],
    )
    if missing:
        log.warning("national: %d states had no published output", len(missing))
    return report
