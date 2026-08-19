"""Geometry handling: footprint area and spatial enrichment.

All area and distance computation happens in EPSG:3310 (California Albers), an
equal-area projection with metre units. Computing areas in WGS84 degrees is a
common and badly wrong shortcut; it is avoided here.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import geopandas as gpd
import pandas as pd
from shapely import wkt as shapely_wkt
from shapely.geometry import Point

from ..config import (
    CA_EQUAL_AREA_CRS,
    MAX_MODELLED_STOREYS,
    REFERENCE_DIR,
    WGS84,
)

log = logging.getLogger(__name__)

SQM_PER_SQFT = 0.09290304


def to_geoframe(df: pd.DataFrame, *, prefer_polygons: bool = True) -> gpd.GeoDataFrame:
    """Build a GeoDataFrame, using polygon geometry where available.

    Points are used as a fallback so that every record remains mappable even
    when OSM only recorded a node.
    """
    geoms = []
    for _, row in df.iterrows():
        wkt_value = row.get("geometry_wkt")
        geom = None
        if prefer_polygons and isinstance(wkt_value, str) and wkt_value.strip():
            try:
                geom = shapely_wkt.loads(wkt_value)
            except Exception:
                geom = None
        if geom is None or geom.is_empty:
            lon, lat = row.get("lon"), row.get("lat")
            geom = Point(lon, lat) if pd.notna(lon) and pd.notna(lat) else None
        geoms.append(geom)

    return gpd.GeoDataFrame(df.copy(), geometry=geoms, crs=WGS84)


def add_footprint_area(df: pd.DataFrame) -> pd.DataFrame:
    """Compute ``footprint_sqm`` / ``footprint_sqft`` from polygon geometry.

    Records whose geometry is a point get NaN rather than zero: absence of a
    footprint measurement is not a footprint of zero, and conflating the two
    would silently corrupt the Tier C power model.
    """
    out = df.copy()
    has_wkt = out.get("geometry_wkt")
    if has_wkt is None or has_wkt.notna().sum() == 0:
        out["footprint_sqm"] = pd.NA
        out["footprint_sqft"] = pd.NA
        return out

    gdf = to_geoframe(out)
    projected = gdf.to_crs(CA_EQUAL_AREA_CRS)
    is_poly = projected.geometry.geom_type.isin(["Polygon", "MultiPolygon"])

    areas = pd.Series(pd.NA, index=out.index, dtype="Float64")
    areas.loc[is_poly.values] = projected.loc[is_poly, "geometry"].area.values

    out["footprint_sqm"] = areas
    out["footprint_sqft"] = areas / SQM_PER_SQFT
    log.info(
        "geometry: footprint area computed for %d/%d records",
        int(areas.notna().sum()),
        len(out),
    )
    return out


def estimate_gross_area(df: pd.DataFrame) -> pd.DataFrame:
    """Approximate gross floor area, capping credited storeys.

    Sets ``partial_occupancy`` where the building is taller than
    ``MAX_MODELLED_STOREYS``. For those records the facility is a tenant in a
    larger tower, the true occupied share is unknown, and the Tier C estimator
    widens its interval accordingly.
    """
    out = df.copy()
    levels = pd.to_numeric(out.get("building_levels"), errors="coerce")
    # Single-storey is the overwhelming norm for purpose-built data centers.
    levels = levels.where(levels.between(1, 200), 1).fillna(1)

    capped = levels.clip(upper=MAX_MODELLED_STOREYS)
    out["building_levels_reported"] = levels
    out["building_levels_used"] = capped
    out["partial_occupancy"] = levels > MAX_MODELLED_STOREYS

    footprint = pd.to_numeric(out["footprint_sqft"], errors="coerce")
    out["est_gross_sqft"] = footprint * capped
    # Floor of the plausible range: the facility occupies a single storey.
    out["est_gross_sqft_min"] = footprint

    n_partial = int(out["partial_occupancy"].sum())
    if n_partial:
        log.info(
            "geometry: %d facilities flagged partial occupancy (building taller "
            "than %d storeys)",
            n_partial,
            MAX_MODELLED_STOREYS,
        )
    return out


def assign_utility(
    facilities: pd.DataFrame, territories: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Attribute each facility to the retail electric utility serving it.

    The CEC territory layer contains **overlapping** polygons, so a naive
    spatial join returns an arbitrary match. Observed failure: 73 facilities,
    including the entire Santa Clara cluster, were attributed to the Power and
    Water Resource Pooling Authority instead of Silicon Valley Power.

    Resolution, in order:

    1. Drop non-retail overlays listed in
       ``data/reference/utility_overlay_exclusions.csv`` (pooling authorities,
       water wholesalers, municipal-only and port utilities).
    2. Among the remainder, take the **smallest-area** territory containing the
       point. Municipal utilities nest inside IOU footprints, so the smallest
       containing polygon is the most specific genuine retail provider.

    Also emits ``utility_candidates``: every overlapping territory, so that
    ambiguous attributions are visible rather than hidden behind a single value.
    """
    out = facilities.copy()
    if territories is None or territories.empty:
        out["utility"] = None
        out["utility_candidates"] = None
        return out

    excluded = _overlay_exclusions()
    poly = territories.to_crs(CA_EQUAL_AREA_CRS).copy()
    poly["_area"] = poly.geometry.area

    retail = poly[
        ~poly.utility_name.fillna("").str.strip().isin(excluded)
    ].sort_values("_area")

    pts = gpd.GeoDataFrame(
        out[["facility_id"]].copy(),
        geometry=[Point(xy) for xy in zip(out.lon, out.lat)],
        crs=WGS84,
    ).to_crs(CA_EQUAL_AREA_CRS)

    # All overlaps, for transparency.
    all_hits = gpd.sjoin(pts, poly[["utility", "geometry"]], how="left", predicate="within")
    candidates = (
        all_hits.dropna(subset=["utility"])
        .groupby("facility_id")
        .utility.apply(lambda s: ",".join(sorted(set(s.astype(str)))))
    )

    # Most specific retail provider.
    hits = gpd.sjoin(
        pts, retail[["utility", "_area", "geometry"]], how="left", predicate="within"
    ).sort_values("_area")
    chosen = hits.drop_duplicates(subset="facility_id", keep="first")

    out["utility"] = out.facility_id.map(dict(zip(chosen.facility_id, chosen.utility)))
    out["utility_candidates"] = out.facility_id.map(candidates)

    log.info(
        "utility: attributed %d/%d facilities (%s)",
        int(out.utility.notna().sum()),
        len(out),
        out.utility.value_counts().head(6).to_dict(),
    )
    return out


@lru_cache(maxsize=1)
def _overlay_exclusions() -> frozenset[str]:
    path = REFERENCE_DIR / "utility_overlay_exclusions.csv"
    if not path.exists():
        log.warning("utility overlay exclusion list missing: %s", path)
        return frozenset()
    table = pd.read_csv(path, comment="#")
    return frozenset(table["utility"].dropna().str.strip())


def spatial_join_attribute(
    facilities: pd.DataFrame,
    polygons: gpd.GeoDataFrame,
    *,
    value_column: str,
    output_column: str,
) -> pd.DataFrame:
    """Attach a polygon attribute (e.g. utility territory) to each facility."""
    out = facilities.copy()
    if polygons is None or polygons.empty or value_column not in polygons.columns:
        out[output_column] = None
        return out

    pts = gpd.GeoDataFrame(
        out[["facility_id"]].copy(),
        geometry=[Point(xy) for xy in zip(out.lon, out.lat)],
        crs=WGS84,
    ).to_crs(CA_EQUAL_AREA_CRS)

    poly = polygons.to_crs(CA_EQUAL_AREA_CRS)[[value_column, "geometry"]]
    joined = gpd.sjoin(pts, poly, how="left", predicate="within")
    joined = joined.drop_duplicates(subset="facility_id", keep="first")

    mapping = dict(zip(joined.facility_id, joined[value_column]))
    out[output_column] = out.facility_id.map(mapping)
    return out


def nearest_distance_km(
    facilities: pd.DataFrame,
    targets: gpd.GeoDataFrame,
    *,
    output_column: str,
) -> pd.DataFrame:
    """Great-circle-equivalent distance to the nearest feature in ``targets``."""
    out = facilities.copy()
    if targets is None or targets.empty:
        out[output_column] = pd.NA
        return out

    pts = gpd.GeoDataFrame(
        out[["facility_id"]].copy(),
        geometry=[Point(xy) for xy in zip(out.lon, out.lat)],
        crs=WGS84,
    ).to_crs(CA_EQUAL_AREA_CRS)

    tgt = targets.to_crs(CA_EQUAL_AREA_CRS)[["geometry"]].reset_index(drop=True)
    joined = gpd.sjoin_nearest(pts, tgt, how="left", distance_col="_dist_m")
    joined = joined.drop_duplicates(subset="facility_id", keep="first")

    mapping = dict(zip(joined.facility_id, joined["_dist_m"] / 1000.0))
    out[output_column] = out.facility_id.map(mapping)
    return out
