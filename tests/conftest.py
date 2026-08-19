"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

import pandas as pd
import pytest

from data_center_dataset.normalize.schema import conform_source_frame


@pytest.fixture
def source_records() -> pd.DataFrame:
    """A small frame exercising the real edge cases found in live data."""
    rows = [
        # Genuine colocation, present in two sources at the same address.
        {
            "source": "osm",
            "source_id": "way/1",
            "name": "Equinix SV1",
            "operator_raw": "Equinix",
            "lat": 37.4000,
            "lon": -121.9500,
            "address": "11 Great Oaks Boulevard",
            "geometry_wkt": None,
        },
        {
            "source": "peeringdb",
            "source_id": "101",
            "name": "Equinix San Jose (SV1)",
            "operator_raw": "Equinix",
            "lat": 37.4001,
            "lon": -121.9501,
            "address": "11 Great Oaks Blvd",
        },
        # Same campus, different building. Must NOT merge.
        {
            "source": "peeringdb",
            "source_id": "102",
            "name": "CoreSite - Santa Clara (SV3)",
            "operator_raw": "CoreSite",
            "lat": 37.3800,
            "lon": -121.9800,
            "address": "2901 Coronado Drive",
        },
        {
            "source": "peeringdb",
            "source_id": "103",
            "name": "CoreSite - Santa Clara (SV7)",
            "operator_raw": "CoreSite",
            "lat": 37.3802,
            "lon": -121.9803,
            "address": "3032 Coronado Drive",
        },
        # Out of scope: campus computer room.
        {
            "source": "osm",
            "source_id": "node/9",
            "name": "SDSU Computer Room",
            "operator_raw": None,
            "lat": 32.7750,
            "lon": -117.0709,
            "address": None,
        },
        # Upstream error: recorded as CA but located in North Carolina.
        {
            "source": "peeringdb",
            "source_id": "104",
            "name": "QTS Charlotte (CTL1)",
            "operator_raw": "QTS",
            "lat": 35.0354,
            "lon": -81.1045,
            "address": "215 Westinghouse Boulevard",
        },
    ]
    return conform_source_frame(pd.DataFrame(rows))
