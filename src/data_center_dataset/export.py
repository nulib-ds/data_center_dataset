"""Publication of the processed tables.

Outputs, all under ``data/processed``:

``facilities.{parquet,csv}``      one row per resolved site
``facilities.geojson``           the same, as points, for mapping
``power_estimates.{parquet,csv}`` every estimate from every tier, uncollapsed
``facility_sources.csv``         facility_id -> source record crosswalk
``exclusions.csv``               records filtered out, with the rule that fired
``dedupe_review.csv``            uncertain match pairs awaiting human judgement
``reconciliation.json``          bottom-up vs top-down comparison
``datapackage.json``             Frictionless descriptor with field docs
"""

from __future__ import annotations

import json
import logging

import geopandas as gpd
import pandas as pd
from shapely import wkt as shapely_wkt
from shapely.geometry import Point

from .config import PROCESSED_DIR, WGS84, ensure_dirs

log = logging.getLogger(__name__)

#: Published column order for the facilities table.
FACILITY_COLUMNS = [
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

FIELD_DOCS = {
    "facility_id": "Stable slug + hash identifier for the resolved site.",
    "operator_confidence": "How the operator was resolved: alias (curated table) | alias_token (partial token match) | unattributed (known property SPV, operator not public) | unresolved (raw string kept).",
    "facility_class": "colocation | wholesale | hyperscale | unknown.",
    "utility": "Most specific retail electric utility whose CEC territory contains the site.",
    "utility_candidates": "All overlapping CEC territories, including non-retail overlays. Shows attribution ambiguity.",
    "footprint_sqft": "Building footprint from OSM polygon, EPSG:3310. Null if only a point is known.",
    "est_gross_sqft": "footprint_sqft x credited storeys (capped at 3). Modelled, not surveyed.",
    "building_levels_reported": "Storeys recorded in OSM, uncapped.",
    "building_levels_used": "Storeys credited by the Tier C model, capped at 3.",
    "partial_occupancy": "True where the building exceeds the storey cap, so the facility is one tenant of a larger tower and the footprint overstates its share.",
    "est_white_space_sqft": "Modelled raised-floor area. Tier C input, not a measurement.",
    "best_power_mw": "Preferred IT-load estimate in MW, selected by tier precedence. NOT metered consumption.",
    "power_ci_low_mw": "Lower bound of the estimate interval.",
    "power_ci_high_mw": "Upper bound of the estimate interval.",
    "power_tier": "Evidence quality: A_attested (cited) > B_generator (permit proxy) > C_area (model).",
    "n_power_methods": "How many independent methods produced an estimate for this site.",
    "est_annual_gwh": "Modelled annual electricity use, = IT load x PUE x utilization x 8760.",
    "pdb_net_count": "Networks present, from PeeringDB. Interconnection density.",
    "pdb_ix_count": "Internet exchanges present, from PeeringDB.",
    "n_sources": "Number of independent sources contributing to this record.",
    "source_list": "Comma-separated source keys contributing to this record.",
}


def _write_table(df: pd.DataFrame, stem: str, columns: list[str] | None = None) -> None:
    out = df.copy()
    if columns:
        for col in columns:
            if col not in out.columns:
                out[col] = pd.NA
        out = out[columns]
    out.to_csv(PROCESSED_DIR / f"{stem}.csv", index=False)
    try:
        out.to_parquet(PROCESSED_DIR / f"{stem}.parquet", index=False)
    except Exception as exc:  # pragma: no cover - mixed dtypes
        log.warning("parquet write failed for %s (%s); CSV still written", stem, exc)
    log.info("wrote %s (%d rows)", stem, len(out))


def write_all(built: dict) -> None:
    ensure_dirs()

    facilities = built["facilities"]
    _write_table(facilities, "facilities", FACILITY_COLUMNS)
    _write_table(built["power_estimates"], "power_estimates", POWER_COLUMNS)
    _write_table(built["crosswalk"], "facility_sources")
    _write_table(built["excluded"], "exclusions")
    _write_table(built["review_queue"], "dedupe_review")
    if built.get("tier_agreement") is not None and not built["tier_agreement"].empty:
        _write_table(built["tier_agreement"], "tier_agreement")

    # -- GeoJSON ---------------------------------------------------------
    geo_cols = [c for c in FACILITY_COLUMNS if c in facilities.columns]
    gdf = gpd.GeoDataFrame(
        facilities[geo_cols].copy(),
        geometry=[Point(xy) for xy in zip(facilities.lon, facilities.lat)],
        crs=WGS84,
    )
    geo_path = PROCESSED_DIR / "facilities.geojson"
    gdf.to_file(geo_path, driver="GeoJSON")
    log.info("wrote facilities.geojson (%d features)", len(gdf))

    _write_footprints(facilities, geo_cols)

    (PROCESSED_DIR / "reconciliation.json").write_text(
        json.dumps(built["reconciliation"], indent=2, sort_keys=True)
    )

    _write_datapackage(built)


def _write_footprints(facilities: pd.DataFrame, geo_cols: list[str]) -> None:
    """Publish building footprint polygons as their own layer.

    Kept separate from ``facilities.geojson`` (which is points) because the two
    serve different purposes: points map every facility, polygons map only the
    subset whose building outline is known. Mixing geometry types in one file
    makes both harder to style.
    """
    if "geometry_wkt" not in facilities.columns:
        return

    subset = facilities[facilities.geometry_wkt.notna()].copy()
    path = PROCESSED_DIR / "facility_footprints.geojson"
    if subset.empty:
        log.info("no footprint polygons to write")
        return

    geoms = []
    keep = []
    for idx, wkt_value in zip(subset.index, subset.geometry_wkt):
        try:
            geoms.append(shapely_wkt.loads(wkt_value))
            keep.append(idx)
        except Exception:
            log.debug("unparseable footprint WKT on row %s", idx)

    subset = subset.loc[keep]
    cols = [c for c in geo_cols if c in subset.columns]
    gdf = gpd.GeoDataFrame(subset[cols].copy(), geometry=geoms, crs=WGS84)
    gdf.to_file(path, driver="GeoJSON")
    log.info("wrote facility_footprints.geojson (%d polygons)", len(gdf))


def _resource(stem: str, df: pd.DataFrame, description: str) -> dict:
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


def _datapackage_sources() -> list[dict]:
    return [
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
            "role": "Electric utility service territories",
        },
        {
            "title": "CEQAnet",
            "path": "https://ceqanet.opr.ca.gov/",
            "license": "US-PD",
            "role": "Attested project megawatt figures (opt-in, low recall)",
        },
    ]


def _write_datapackage(built: dict) -> None:
    facilities = built["facilities"]
    resources = [
        _resource(
            "facilities",
            facilities[[c for c in FACILITY_COLUMNS if c in facilities.columns]],
            "One row per resolved commercial data center site in California.",
        ),
        _resource(
            "power_estimates",
            built["power_estimates"],
            "Every power estimate from every method. Not collapsed; join on facility_id.",
        ),
        _resource(
            "facility_sources",
            built["crosswalk"],
            "Crosswalk from facility_id to contributing source records.",
        ),
        _resource(
            "exclusions",
            built["excluded"],
            "Records filtered out of scope, annotated with the rule responsible.",
        ),
    ]

    descriptor = {
        "profile": "tabular-data-package",
        "name": "california-data-center-dataset",
        "title": "California Data Centers with Tiered Power Estimates",
        "description": (
            "Registry of commercial (colocation, wholesale, hyperscale) data "
            "centers in California, assembled from OpenStreetMap, PeeringDB, the "
            "EPA National Emissions Inventory and California Energy Commission "
            "GIS. Power figures are ESTIMATES carrying an explicit evidence tier "
            "and confidence interval; none is a metered consumption reading. "
            "See LIMITATIONS.md before use."
        ),
        "created": built.get("snapshot"),
        "licenses": [
            {
                "name": "ODbL-1.0",
                "title": "Open Database License 1.0",
                "path": "https://opendatacommons.org/licenses/odbl/1-0/",
            }
        ],
        "sources": _datapackage_sources(),
        "resources": resources,
        "reconciliation": built["reconciliation"],
    }

    (PROCESSED_DIR / "datapackage.json").write_text(
        json.dumps(descriptor, indent=2, sort_keys=True, default=str)
    )
    log.info("wrote datapackage.json")
