"""Table schemas and validation.

Two layers are defined here:

``SOURCE_RECORD_COLUMNS``
    The common shape every ingester normalizes into, so that downstream code
    never needs to know which source a row came from.

``pandera`` schemas
    Contract checks applied to the published tables. These encode the
    invariants that make the dataset trustworthy -- notably that no modelled
    power figure may exist without the inputs that justify it.
"""

from __future__ import annotations

import pandera.pandas as pa
import pandas as pd
from pandera.typing import Series

from ..config import CONUS_BBOX, TIER_PRECEDENCE

#: Loose geographic safety net, kept for reference. The published-table
#: contract no longer uses a box for this: a box wide enough to hold Alaska
#: also holds London. ``FacilitySchema.within_a_known_state`` checks against
#: the union of the per-state boxes instead.
MINX, MINY, MAXX, MAXY = CONUS_BBOX

# --------------------------------------------------------------------------
# Common intermediate representation
# --------------------------------------------------------------------------

SOURCE_RECORD_COLUMNS: list[str] = [
    "source",  # short source key, e.g. "osm"
    "source_id",  # stable identifier within that source
    "source_url",  # canonical link back to the record
    "name",
    "operator_raw",
    "lat",
    "lon",
    "address",
    "city",
    "state",
    "postcode",
    "status_raw",
    "year_built",
    "building_levels",
    "footprint_sqm",  # only OSM supplies geometry-derived area
    "geometry_wkt",
    # Whether the record's geometry is a *building* rather than the site that
    # contains it. Only OSM carries polygons, so this is meaningful there
    # alone; every other source leaves it null.
    "is_building",
    "clli",
    "website",
    "pdb_net_count",
    "pdb_ix_count",
    "pdb_carrier_count",
    "campus_raw",
    "raw_json",
]


def empty_source_frame() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in SOURCE_RECORD_COLUMNS})


def conform_source_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Add any missing common columns and order them canonically."""
    out = df.copy()
    for col in SOURCE_RECORD_COLUMNS:
        if col not in out.columns:
            out[col] = None
    numeric = [
        "lat",
        "lon",
        "footprint_sqm",
        "year_built",
        "building_levels",
        "pdb_net_count",
        "pdb_ix_count",
        "pdb_carrier_count",
    ]
    for col in numeric:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["is_building"] = out["is_building"].fillna(False).astype(bool)
    return out[SOURCE_RECORD_COLUMNS]


# --------------------------------------------------------------------------
# Published table contracts
# --------------------------------------------------------------------------

#: Loose geographic safety net, kept for reference. The published-table
#: contract no longer uses a box for this: a box wide enough to hold Alaska
#: also holds London. ``FacilitySchema.within_a_known_state`` checks against
#: the union of the per-state boxes instead.
MINX, MINY, MAXX, MAXY = CONUS_BBOX


class FacilitySchema(pa.DataFrameModel):
    """One row per resolved physical data center site."""

    facility_id: Series[str] = pa.Field(unique=True, nullable=False)
    name: Series[str] = pa.Field(nullable=False)
    operator: Series[str] = pa.Field(nullable=True)
    lat: Series[float] = pa.Field(ge=-90.0, le=90.0, nullable=False)
    lon: Series[float] = pa.Field(ge=-180.0, le=180.0, nullable=False)
    footprint_sqft: Series[float] = pa.Field(ge=0, nullable=True)
    est_white_space_sqft: Series[float] = pa.Field(ge=0, nullable=True)
    best_power_mw: Series[float] = pa.Field(ge=0, le=2000, nullable=True)
    power_ci_low_mw: Series[float] = pa.Field(ge=0, nullable=True)
    power_ci_high_mw: Series[float] = pa.Field(ge=0, nullable=True)
    power_tier: Series[str] = pa.Field(
        nullable=True, isin=list(TIER_PRECEDENCE)
    )
    est_annual_gwh: Series[float] = pa.Field(ge=0, nullable=True)
    n_sources: Series[int] = pa.Field(ge=1)

    class Config:  # noqa: D106
        strict = False
        coerce = True

    @pa.dataframe_check
    def within_a_known_state(cls, df: pd.DataFrame) -> bool:
        """Coordinates must fall inside *some* US state.

        A plain bounding box cannot do this job: Alaska and Hawaii sit
        outside the continental bounds, and a box wide enough to contain
        Alaska also contains London and Tokyo. The check is therefore against
        the union of the per-state boxes, which is also where the pipeline
        itself decides what is in scope.
        """
        from ..states import STATES, state_bboxes

        lat = pd.to_numeric(df["lat"], errors="coerce")
        lon = pd.to_numeric(df["lon"], errors="coerce")
        inside = pd.Series(False, index=df.index)
        for profile in STATES.values():
            for box in state_bboxes(profile):
                inside |= (lat.between(box[1], box[3]) & lon.between(box[0], box[2])).fillna(
                    False
                )
        return bool(inside.all())

    @pa.dataframe_check
    def ci_brackets_estimate(cls, df: pd.DataFrame) -> bool:
        """The point estimate must lie inside its own confidence interval."""
        m = df[["best_power_mw", "power_ci_low_mw", "power_ci_high_mw"]].notna().all(axis=1)
        if not m.any():
            return True
        sub = df[m]
        return bool(
            (sub.power_ci_low_mw <= sub.best_power_mw + 1e-9).all()
            and (sub.best_power_mw <= sub.power_ci_high_mw + 1e-9).all()
        )


class PowerEstimateSchema(pa.DataFrameModel):
    """One row per (facility, estimation method). Never collapsed."""

    facility_id: Series[str] = pa.Field(nullable=False)
    method: Series[str] = pa.Field(isin=list(TIER_PRECEDENCE), nullable=False)
    basis: Series[str] = pa.Field(nullable=False)
    it_load_mw: Series[float] = pa.Field(ge=0, le=2000, nullable=True)
    ci_low_mw: Series[float] = pa.Field(ge=0, nullable=True)
    ci_high_mw: Series[float] = pa.Field(ge=0, nullable=True)
    annual_gwh: Series[float] = pa.Field(ge=0, nullable=True)
    source_url: Series[str] = pa.Field(nullable=True)
    assumptions_json: Series[str] = pa.Field(nullable=False)

    class Config:  # noqa: D106
        strict = False
        coerce = True

    @pa.dataframe_check
    def interval_ordered(cls, df: pd.DataFrame) -> bool:
        m = df[["ci_low_mw", "ci_high_mw"]].notna().all(axis=1)
        return bool((df.loc[m, "ci_low_mw"] <= df.loc[m, "ci_high_mw"] + 1e-9).all())

    @pa.dataframe_check
    def attested_rows_are_cited(cls, df: pd.DataFrame) -> bool:
        """A Tier A value without a citation is not attested; reject it."""
        att = df[df.method == TIER_PRECEDENCE[0]]
        if att.empty:
            return True
        return bool(att.source_url.notna().all() and (att.source_url.str.len() > 0).all())
