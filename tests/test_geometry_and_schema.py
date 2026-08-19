"""Geometry handling and published-table contracts."""

from __future__ import annotations

import pandas as pd
import pytest
from shapely.geometry import Polygon

from data_center_dataset.normalize import geometry
from data_center_dataset.normalize.schema import (
    FacilitySchema,
    PowerEstimateSchema,
    conform_source_frame,
)


def _square_wkt(lon: float, lat: float, side_deg: float) -> str:
    return Polygon(
        [
            (lon, lat),
            (lon + side_deg, lat),
            (lon + side_deg, lat + side_deg),
            (lon, lat + side_deg),
        ]
    ).wkt


def test_footprint_area_uses_an_equal_area_projection():
    """A ~100 m square in Santa Clara should measure ~10,000 sqm, not degrees."""
    # 0.001 degrees of longitude at 37 N is roughly 89 m; latitude roughly 111 m.
    df = conform_source_frame(
        pd.DataFrame(
            [
                {
                    "source": "osm",
                    "source_id": "way/1",
                    "name": "Test DC",
                    "lat": 37.38,
                    "lon": -121.98,
                    "geometry_wkt": _square_wkt(-121.98, 37.38, 0.001),
                }
            ]
        )
    )
    out = geometry.add_footprint_area(df)
    area = float(out.footprint_sqm.iloc[0])
    assert 8_000 < area < 12_000, f"implausible area {area}"
    assert out.footprint_sqft.iloc[0] == pytest.approx(area / 0.09290304)


def test_point_only_records_get_null_area_not_zero():
    """Absence of a footprint is not a footprint of zero."""
    df = conform_source_frame(
        pd.DataFrame(
            [
                {
                    "source": "peeringdb",
                    "source_id": "1",
                    "name": "Point only",
                    "lat": 37.38,
                    "lon": -121.98,
                    "geometry_wkt": None,
                },
                {
                    "source": "osm",
                    "source_id": "way/2",
                    "name": "Has polygon",
                    "lat": 37.40,
                    "lon": -122.00,
                    "geometry_wkt": _square_wkt(-122.00, 37.40, 0.001),
                },
            ]
        )
    )
    out = geometry.add_footprint_area(df)
    assert pd.isna(out.loc[out.name == "Point only", "footprint_sqm"].iloc[0])
    assert out.loc[out.name == "Has polygon", "footprint_sqm"].iloc[0] > 0


def test_gross_area_multiplies_by_storeys():
    df = pd.DataFrame(
        [
            {"footprint_sqft": 10_000.0, "building_levels": 3},
            {"footprint_sqft": 10_000.0, "building_levels": None},
            # Absurd storey counts are ignored rather than trusted.
            {"footprint_sqft": 10_000.0, "building_levels": 900},
        ]
    )
    out = geometry.estimate_gross_area(df)
    assert out.est_gross_sqft.tolist() == [30_000.0, 10_000.0, 10_000.0]


def test_facility_schema_rejects_coordinates_outside_california():
    df = pd.DataFrame(
        [
            {
                "facility_id": "bad",
                "name": "Somewhere else",
                "operator": "X",
                "lat": 35.03,
                "lon": -81.10,  # North Carolina
                "footprint_sqft": None,
                "est_white_space_sqft": None,
                "best_power_mw": None,
                "power_ci_low_mw": None,
                "power_ci_high_mw": None,
                "power_tier": None,
                "est_annual_gwh": None,
                "n_sources": 1,
            }
        ]
    )
    with pytest.raises(Exception):
        FacilitySchema.validate(df)


def test_facility_schema_requires_estimate_inside_its_interval():
    df = pd.DataFrame(
        [
            {
                "facility_id": "f",
                "name": "N",
                "operator": "O",
                "lat": 37.4,
                "lon": -121.9,
                "footprint_sqft": 1.0,
                "est_white_space_sqft": 1.0,
                "best_power_mw": 50.0,
                "power_ci_low_mw": 60.0,  # inverted
                "power_ci_high_mw": 70.0,
                "power_tier": "C_area",
                "est_annual_gwh": 1.0,
                "n_sources": 1,
            }
        ]
    )
    with pytest.raises(Exception):
        FacilitySchema.validate(df)


def test_power_schema_rejects_uncited_attested_rows():
    """An attested figure without a citation is not attested."""
    df = pd.DataFrame(
        [
            {
                "facility_id": "f",
                "method": "A_attested",
                "basis": "it_load",
                "it_load_mw": 10.0,
                "ci_low_mw": 9.0,
                "ci_high_mw": 11.0,
                "annual_gwh": 60.0,
                "source_url": None,
                "assumptions_json": "{}",
            }
        ]
    )
    with pytest.raises(Exception):
        PowerEstimateSchema.validate(df)


def test_power_schema_accepts_a_cited_attested_row():
    df = pd.DataFrame(
        [
            {
                "facility_id": "f",
                "method": "A_attested",
                "basis": "it_load",
                "it_load_mw": 10.0,
                "ci_low_mw": 9.0,
                "ci_high_mw": 11.0,
                "annual_gwh": 60.0,
                "source_url": "https://example.gov/eir/123",
                "assumptions_json": "{}",
            }
        ]
    )
    PowerEstimateSchema.validate(df)


def test_high_rise_storeys_are_capped_and_flagged():
    """One Wilshire is 30 storeys; crediting all of them gave its tenant 150 MW.

    A facility in a tower occupies a few floors, not the whole building.
    """
    df = pd.DataFrame(
        [
            {"footprint_sqft": 43_850.0, "building_levels": 30},   # carrier hotel
            {"footprint_sqft": 100_000.0, "building_levels": 2},    # purpose-built
        ]
    )
    out = geometry.estimate_gross_area(df)

    tower, purpose_built = out.iloc[0], out.iloc[1]
    assert tower.partial_occupancy is True or bool(tower.partial_occupancy)
    assert tower.building_levels_reported == 30
    assert tower.building_levels_used == 3
    assert tower.est_gross_sqft == 43_850.0 * 3
    assert tower.est_gross_sqft_min == 43_850.0

    assert not bool(purpose_built.partial_occupancy)
    assert purpose_built.est_gross_sqft == 200_000.0


def test_partial_occupancy_widens_the_tier_c_interval():
    from data_center_dataset.power import model

    facilities = pd.DataFrame(
        [
            {
                "facility_id": "tower",
                "facility_class": "colocation",
                "year_built": 2000,
                "est_gross_sqft": 90_000.0,
                "est_gross_sqft_min": 30_000.0,
                "partial_occupancy": True,
            },
            {
                "facility_id": "shed",
                "facility_class": "colocation",
                "year_built": 2000,
                "est_gross_sqft": 90_000.0,
                "est_gross_sqft_min": 90_000.0,
                "partial_occupancy": False,
            },
        ]
    )
    out = model.estimate(facilities).set_index("facility_id")

    # Same mid-point, but the tower's lower bound must reach further down.
    assert out.loc["tower", "it_load_mw"] == pytest.approx(out.loc["shed", "it_load_mw"])
    assert out.loc["tower", "ci_low_mw"] < out.loc["shed", "ci_low_mw"]
    assert out.loc["tower", "ci_low_mw"] <= out.loc["tower", "it_load_mw"]
