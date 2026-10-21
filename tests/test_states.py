"""State coverage and per-state configuration.

The pipeline is only as trustworthy as its per-state configuration: a wrong
bounding box silently drops facilities, a wrong EPA region file silently
loses the backup-generator evidence that Tier B depends on, and a missing
state is silently absent from the national roll-up. These tests pin all three.
"""

from __future__ import annotations

import pandas as pd
import pytest

from data_center_dataset import national
from data_center_dataset.states import (
    EPA_REGIONS,
    STATES,
    bbox_contains,
    get,
    region_states,
    state_bboxes,
    state_name_to_code,
    zip3_state,
)

#: The 50 states plus the District of Columbia.
EXPECTED_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI",
    "WY",
}


def test_every_state_and_district_is_configured():
    assert set(STATES) == EXPECTED_CODES
    assert len(STATES) == 51


def test_state_lookup_is_case_insensitive_and_rejects_unknown():
    assert get("ca").code == "CA"
    assert get(" ny ").code == "NY"
    with pytest.raises(ValueError):
        get("XX")


@pytest.mark.parametrize("code", sorted(EXPECTED_CODES))
def test_profile_fields_are_sane(code):
    profile = STATES[code]

    minx, miny, maxx, maxy = profile.bbox
    assert minx < maxx
    assert miny < maxy
    assert -180.0 <= minx <= 180.0
    assert -180.0 <= maxx <= 180.0
    assert 0.0 <= miny <= 90.0
    assert 0.0 <= maxy <= 90.0

    # The map centre must sit inside the state's own bounding box.
    lat, lon = profile.map_center
    assert miny <= lat <= maxy, f"{code} map centre outside its bbox"
    assert minx <= lon <= maxx, f"{code} map centre outside its bbox"

    assert profile.annual_twh_anchor > 0
    low, high = profile.annual_twh_anchor_range
    assert 0 < low <= profile.annual_twh_anchor <= high
    assert profile.anchor_note, f"{code} has no documented anchor rationale"
    assert profile.map_zoom > 0
    assert profile.region_views, f"{code} has no map presets"


@pytest.mark.parametrize("code", sorted(EXPECTED_CODES))
def test_region_views_are_inside_the_state(code):
    profile = STATES[code]
    minx, miny, maxx, maxy = profile.bbox
    for view in profile.region_views:
        assert miny <= view.lat <= maxy, f"{code}/{view.label} latitude outside bbox"
        assert minx <= view.lon <= maxx, f"{code}/{view.label} longitude outside bbox"


def test_nei_region_mapping_is_complete_and_consistent():
    """Every state must map to a region file, and vice versa."""
    from_regions = set()
    for member, codes in EPA_REGIONS.values():
        from_regions.update(codes)
    assert from_regions == set(STATES)

    for code, profile in STATES.items():
        assert profile.nei_region_file in {
            member for member, _ in EPA_REGIONS.values()
        }, f"{code} points at an unknown region member"
        assert code in region_states(profile.nei_region_file)


def test_alaska_uses_two_boxes_because_it_crosses_the_antimeridian():
    """A single (minx, maxx) box for Alaska would cover the whole world.

    Alaska's Aleutian tail sits past +172 E. Folding it into the main box
    makes the box span every longitude -- which would admit London, Tokyo
    and the mid-Atlantic into "Alaska".
    """
    ak = STATES["AK"]
    assert ak.extra_bboxes, "Alaska needs a second box for the far-west Aleutians"

    boxes = state_bboxes(ak)
    assert len(boxes) == 2

    # Anchorage and Attu are both in Alaska.
    assert any(bbox_contains(b, 61.22, -149.90) for b in boxes)
    assert any(bbox_contains(b, 52.89, 172.97) for b in boxes)

    # London, Tokyo and the mid-Atlantic are not.
    for lat, lon in ((51.50, -0.12), (35.68, 139.69), (30.0, -40.0)):
        assert not any(bbox_contains(b, lat, lon) for b in boxes), (
            f"({lat}, {lon}) must not be inside an Alaska box"
        )


def test_no_state_box_covers_a_point_outside_the_united_states():
    """The union of the 51 state boxes must not swallow the rest of the world."""
    outside = [
        ("london", 51.50, -0.12),
        ("tokyo", 35.68, 139.69),
        ("sydney", -33.87, 151.21),
        ("vancouver_bc", 49.28, -123.12),
        ("mid_atlantic", 30.0, -40.0),
        ("berlin", 52.52, 13.40),
    ]
    for code, profile in STATES.items():
        boxes = state_bboxes(profile)
        for label, lat, lon in outside:
            assert not any(bbox_contains(b, lat, lon) for b in boxes), (
                f"{label} falls inside the {code} box"
            )


def test_california_keeps_its_exclusive_enrichments():
    """The two California-only layers must not leak into other states."""
    assert STATES["CA"].has_utility_layer
    assert STATES["CA"].supports_ceqanet
    for code, profile in STATES.items():
        if code == "CA":
            continue
        assert not profile.has_utility_layer
        assert not profile.supports_ceqanet


def test_national_roll_up_concatenates_published_tables(tmp_path, monkeypatch):
    """The national tables are a concatenation, not a recomputation."""
    processed = tmp_path / "processed"
    for state, mw in (("CA", 100.0), ("TX", 250.0)):
        outdir = processed / state
        outdir.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "facility_id": f"{state}-1",
                    "name": "Site",
                    "operator": "Equinix",
                    "lat": 35.0,
                    "lon": -100.0,
                    "best_power_mw": mw,
                    "power_tier": "B_generator",
                    "est_annual_gwh": mw * 8.76,
                    "footprint_sqft": 1000.0,
                    "n_sources": 2,
                }
            ]
        ).to_csv(outdir / "facilities.csv", index=False)
        pd.DataFrame(
            [
                {
                    "facility_id": f"{state}-1",
                    "method": "B_generator",
                    "basis": "critical_load",
                    "it_load_mw": mw,
                }
            ]
        ).to_csv(outdir / "power_estimates.csv", index=False)

    monkeypatch.setattr(national, "PROCESSED_DIR", processed)
    report = national.build(tmp_path / "national")

    fac = pd.read_csv(tmp_path / "national" / "facilities.csv")
    assert set(fac.state) == {"CA", "TX"}
    assert len(fac) == 2
    assert report["facilities"] == 2
    assert report["bottom_up_it_load_mw"] == 350.0
    # Every other state is simply absent, not zero-filled.
    assert report["states_built"] == 2
    assert len(report["states_missing"]) == 49


# --------------------------------------------------------------------------
# ZIP code -> state
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "zipcode,expected",
    [
        ("82009", "WY"),      # Cheyenne
        ("33487", "FL"),      # Boca Raton
        ("70056", "LA"),      # Gretna
        ("97013", "OR"),      # Portland
        ("50265", "IA"),      # West Des Moines
        ("19019", "PA"),      # Philadelphia
        ("48858", "MI"),      # Mount Pleasant
        ("10001", "NY"),
        ("99701", "AK"),      # Fairbanks
        ("96743", "HI"),      # Hilo
        ("00901", "PR"),      # Puerto Rico -- out of scope, but identified
        ("00601", "PR"),
        ("96813", "HI"),
    ],
)
def test_zip3_resolves_to_the_right_state(zipcode, expected):
    assert zip3_state(zipcode) == expected


@pytest.mark.parametrize(
    "zipcode",
    [
        "",
        None,
        "135-0061",       # a Japanese postal code, not a US ZIP
        "1234",
        "ABCDE",
        "not a zip",
    ],
)
def test_zip3_rejects_anything_that_is_not_a_us_zip(zipcode):
    """A foreign postal code must not be read as a US one.

    PeeringDB holds a Tokyo facility whose "zipcode" is ``135-0061``. Its
    first three digits fall in the New York range, so a permissive parser
    would file a Japanese data center in New York.
    """
    assert zip3_state(zipcode) is None


def test_zip3_accepts_a_state_prefixed_zip():
    assert zip3_state("FL 33487") == "FL"
    assert zip3_state("VA 23454") == "VA"


def test_state_name_lookup():
    assert state_name_to_code("South Carolina") == "SC"
    assert state_name_to_code("new york") == "NY"
    assert state_name_to_code("District of Columbia") == "DC"
    assert state_name_to_code("Puerto Rico") is None
    assert state_name_to_code("") is None
    assert state_name_to_code(None) is None


def test_every_state_name_is_unique():
    names = [p.name.lower() for p in STATES.values()]
    assert len(set(names)) == len(names)
