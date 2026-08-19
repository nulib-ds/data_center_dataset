"""Utility attribution against overlapping CEC territory polygons."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from data_center_dataset.normalize import geometry


def _box(x0, y0, x1, y1) -> Polygon:
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def _territories() -> gpd.GeoDataFrame:
    """Mimics the real overlap: a big IOU, a mid-size pooling authority, a city.

    Areas are ordered PG&E > PWRPA > SVP, exactly as in the CEC layer, so that
    a naive smallest-area rule would wrongly select PWRPA.
    """
    return gpd.GeoDataFrame(
        {
            "utility": ["PG&E", "PWRPA", "SVP", "CCSF"],
            "utility_name": [
                "Pacific Gas & Electric Company",
                "Power and Water Resource Pooling Authority",
                "Silicon Valley Power",
                "City and County of San Francisco - Hetch Hetchy Water and Power",
            ],
            "utility_type": ["IOU", "POU", "POU", "POU"],
            "geometry": [
                _box(-123.0, 36.5, -121.0, 38.5),   # largest
                _box(-122.5, 37.0, -121.5, 38.0),   # mid, non-retail overlay
                _box(-121.99, 37.34, -121.93, 37.40),  # small, genuine retail
                _box(-122.52, 37.70, -122.35, 37.84),  # SF, municipal-only
            ],
        },
        crs="EPSG:4326",
    )


def _facilities() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # Santa Clara: inside SVP, PWRPA and PG&E.
            {"facility_id": "santa-clara", "lat": 37.37, "lon": -121.96},
            # San Francisco: inside CCSF and PG&E.
            {"facility_id": "san-francisco", "lat": 37.78, "lon": -122.41},
            # Rural: inside PWRPA and PG&E only.
            {"facility_id": "rural", "lat": 37.50, "lon": -122.00},
        ]
    )


def test_municipal_retail_utility_wins_over_larger_overlays():
    out = geometry.assign_utility(_facilities(), _territories()).set_index("facility_id")
    assert out.loc["santa-clara", "utility"] == "SVP"


def test_non_retail_pooling_authority_is_never_selected():
    """PWRPA is smaller than PG&E, so smallest-area alone would pick it."""
    out = geometry.assign_utility(_facilities(), _territories()).set_index("facility_id")
    assert out.loc["rural", "utility"] == "PG&E"
    assert out.utility.tolist().count("PWRPA") == 0


def test_municipal_only_utility_does_not_capture_commercial_load():
    """San Francisco commercial load is served by PG&E, not Hetch Hetchy."""
    out = geometry.assign_utility(_facilities(), _territories()).set_index("facility_id")
    assert out.loc["san-francisco", "utility"] == "PG&E"


def test_overlapping_territories_are_published_for_transparency():
    out = geometry.assign_utility(_facilities(), _territories()).set_index("facility_id")
    assert out.loc["santa-clara", "utility_candidates"] == "PG&E,PWRPA,SVP"


def test_missing_territory_layer_yields_nulls_not_an_error():
    out = geometry.assign_utility(_facilities(), None)
    assert out.utility.isna().all()
    assert "utility_candidates" in out.columns
