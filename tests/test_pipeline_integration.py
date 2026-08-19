"""Offline integration test for the build stage.

Exercises the whole transform chain -- geometry, classification, resolution, the
generator-fleet gate, all three power tiers and reconciliation -- without any
network access, by handing ``pipeline.build`` a synthetic ingest payload.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from shapely.geometry import Polygon

from data_center_dataset import pipeline
from data_center_dataset.config import TIER_ATTESTED, TIER_GENERATOR
from data_center_dataset.normalize.schema import (
    FacilitySchema,
    PowerEstimateSchema,
    conform_source_frame,
)


def _square(lon: float, lat: float, side: float = 0.0015) -> str:
    return Polygon(
        [(lon, lat), (lon + side, lat), (lon + side, lat + side), (lon, lat + side)]
    ).wkt


@pytest.fixture
def ingested() -> dict:
    records = conform_source_frame(
        pd.DataFrame(
            [
                # Colocation with polygon geometry -> Tier C eligible.
                {
                    "source": "osm",
                    "source_id": "way/100",
                    "source_url": "https://www.openstreetmap.org/way/100",
                    "name": "Equinix SV1",
                    "operator_raw": "Equinix",
                    "lat": 37.4000,
                    "lon": -121.9500,
                    "address": "11 Great Oaks Boulevard",
                    "city": "San Jose",
                    "year_built": 2016,
                    "building_levels": 2,
                    "geometry_wkt": _square(-121.9500, 37.4000),
                },
                {
                    "source": "peeringdb",
                    "source_id": "500",
                    "source_url": "https://www.peeringdb.com/fac/500",
                    "name": "Equinix San Jose (SV1)",
                    "operator_raw": "Equinix",
                    "lat": 37.4001,
                    "lon": -121.9501,
                    "address": "11 Great Oaks Blvd",
                    "city": "San Jose",
                    "pdb_net_count": 44,
                    "pdb_ix_count": 2,
                },
                # NEI-only hyperscale site with a large fleet -> survives the gate.
                {
                    "source": "epa_nei",
                    "source_id": "9001",
                    "source_url": "https://enviro.epa.gov/9001",
                    "name": "AMAZON DATA SERVICES, INC.",
                    "operator_raw": "AMAZON DATA SERVICES, INC.",
                    "lat": 37.3700,
                    "lon": -121.9900,
                    "address": "2200 Lafayette Street",
                    "city": "SANTA CLARA",
                },
                # NEI-only office with one generator -> dropped by the gate.
                {
                    "source": "epa_nei",
                    "source_id": "9002",
                    "source_url": "https://enviro.epa.gov/9002",
                    "name": "GOOGLE LLC",
                    "operator_raw": "GOOGLE LLC",
                    "lat": 37.6200,
                    "lon": -122.4000,
                    "address": "1 Office Way",
                    "city": "SAN BRUNO",
                },
            ]
        )
    )

    generator_inventory = pd.DataFrame(
        [
            {
                "source_id": "9001",
                "n_generator_units": 22,
                "n_units_rated": 2,
                "rated_kw_sum": 4200.0,
            },
            {
                "source_id": "9002",
                "n_generator_units": 1,
                "n_units_rated": 0,
                "rated_kw_sum": 0.0,
            },
        ]
    )

    return {
        "records": records,
        "generator_inventory": generator_inventory,
        "ceqa_evidence": pd.DataFrame(),
        "utilities": None,
        "snapshot": "2026-08-19",
    }


def test_build_produces_valid_published_tables(ingested):
    built = pipeline.build(ingested)
    facilities = built["facilities"]
    estimates = built["power_estimates"]

    # The two Equinix records must have collapsed into one facility.
    assert len(facilities) == 2, facilities[["name", "source_list"]].to_dict("records")
    equinix = facilities[facilities.operator == "Equinix"].iloc[0]
    assert equinix.n_sources == 2

    # The single-generator Google office must have been gated out.
    assert "GOOGLE LLC" not in set(facilities.name)
    assert "nei_only_insufficient_generator_fleet" in set(
        built["excluded"].exclusion_rule
    )

    # Amazon gets a Tier B estimate from its 22-unit fleet.
    amazon = facilities[facilities.operator == "Amazon Web Services"].iloc[0]
    assert amazon.power_tier == TIER_GENERATOR
    assert amazon.best_power_mw > 0

    # Equinix has a footprint, so Tier C applies.
    assert equinix.footprint_sqft > 0
    assert equinix.best_power_mw > 0

    FacilitySchema.validate(
        facilities[[c for c in FacilitySchema.to_schema().columns if c in facilities]],
        lazy=True,
    )
    PowerEstimateSchema.validate(estimates, lazy=True)


def test_build_records_reconciliation_and_never_calibrates_by_default(ingested):
    built = pipeline.build(ingested)
    report = built["reconciliation"]
    assert report["calibration_applied"] is False
    assert report["bottom_up_annual_twh"] >= 0
    assert "top_down_range_twh" in report


def test_every_estimate_carries_recoverable_assumptions(ingested):
    built = pipeline.build(ingested)
    for _, row in built["power_estimates"].iterrows():
        assumptions = json.loads(row.assumptions_json)
        assert assumptions, "each estimate must publish its assumptions"
        if row.method == TIER_ATTESTED:
            assert row.source_url
