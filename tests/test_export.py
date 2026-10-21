"""Export-layer contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from data_center_dataset import export


_SQUARE = "POLYGON ((0 0, 0 0.001, 0.001 0.001, 0.001 0, 0 0))"


def _minimal_built(state: str = "CA") -> dict:
    """The smallest payload ``export.write_all`` accepts."""
    facilities = pd.DataFrame(
        [
            {
                "facility_id": "f-1",
                "name": "One Facility",
                "operator": "Equinix",
                "lat": 37.4,
                "lon": -121.95,
                "geometry_wkt": _SQUARE,
                "footprint_sqft": 10_000.0,
                "source_list": "osm",
                "n_sources": 1,
            }
        ]
    )
    empty = pd.DataFrame()
    return {
        "state": state,
        "snapshot": "2026-10-21",
        "facilities": facilities,
        "power_estimates": pd.DataFrame(
            [
                {
                    "facility_id": "f-1",
                    "method": "C_area",
                    "basis": "footprint",
                    "it_load_mw": 1.0,
                    "annual_gwh": 6.0,
                    "source_url": "https://example.invalid",
                    "retrieved_at": "2026-10-21",
                    "assumptions_json": "{}",
                }
            ]
        ),
        "crosswalk": empty,
        "excluded": empty,
        "review_queue": empty,
        "tier_agreement": None,
        "reconciliation": {
            "calibration_applied": False,
            "bottom_up_annual_twh": 0.0,
            "top_down_range_twh": [0.0, 0.0],
        },
    }


def test_footprint_layer_omits_polygons_without_a_measured_area(tmp_path: Path):
    """A site outline is not a building footprint and must not be published."""
    facilities = pd.DataFrame(
        [
            {
                "facility_id": "with-area",
                "name": "Measured building",
                "lat": 37.4,
                "lon": -121.95,
                "geometry_wkt": _SQUARE,
                "footprint_sqft": 10_000.0,
            },
            {
                "facility_id": "no-area",
                "name": "Site outline",
                "lat": 37.5,
                "lon": -121.85,
                "geometry_wkt": _SQUARE,
                "footprint_sqft": None,
            },
        ]
    )
    export._write_footprints(facilities, ["facility_id", "name", "lat", "lon"], tmp_path)

    path = tmp_path / "facility_footprints.geojson"
    assert path.exists()
    payload = json.loads(path.read_text())
    names = {f["properties"]["name"] for f in payload["features"]}
    assert names == {"Measured building"}


def test_footprint_layer_is_omitted_when_nothing_was_measured(tmp_path: Path):
    facilities = pd.DataFrame(
        [
            {
                "facility_id": "no-area",
                "name": "Site outline",
                "lat": 37.5,
                "lon": -121.85,
                "geometry_wkt": _SQUARE,
                "footprint_sqft": None,
            }
        ]
    )
    export._write_footprints(facilities, ["facility_id", "name", "lat", "lon"], tmp_path)
    assert not (tmp_path / "facility_footprints.geojson").exists()


def test_write_all_only_touches_its_own_state_directory(tmp_path: Path):
    """A state build must write inside its own directory and nothing above it.

    ``data/processed/`` also holds the legacy California-only snapshot, written
    before the pipeline became per-state. A build that wrote to, or removed
    anything from, the parent directory would destroy that snapshot. This test
    pins that guarantee: a sentinel file beside the state directory survives
    byte-for-byte, and no new sibling appears.
    """
    legacy = tmp_path / "facilities.csv"
    legacy.write_text("facility_id,name\nlegacy,Legacy California Row\n")
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    siblings = {p.name for p in tmp_path.iterdir()}

    export.write_all(_minimal_built("CA"), outdir=tmp_path / "CA")

    assert legacy.exists(), "a state build must not delete files in the parent directory"
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before
    assert {p.name for p in tmp_path.iterdir()} == siblings | {"CA"}
    assert (tmp_path / "CA" / "facilities.csv").exists()
    assert (tmp_path / "CA" / "datapackage.json").exists()
