"""Entity resolution, including the cannot-link constraints."""

from __future__ import annotations

import pandas as pd

from data_center_dataset.normalize import classify, dedupe


def _resolve(records: pd.DataFrame):
    in_scope, _ = classify.apply(records)
    return dedupe.resolve(in_scope)


def test_same_facility_across_sources_is_merged(source_records):
    facilities, crosswalk, _ = _resolve(source_records)
    equinix = crosswalk[crosswalk.source_name.str.contains("Equinix")]
    assert equinix.facility_id.nunique() == 1, "OSM and PeeringDB records must merge"
    assert set(equinix.source) == {"osm", "peeringdb"}


def test_distinct_buildings_on_one_campus_stay_separate(source_records):
    """CoreSite SV3 and SV7 share an operator and sit ~30 m apart here."""
    facilities, crosswalk, _ = _resolve(source_records)
    coresite = crosswalk[crosswalk.source_name.str.contains("CoreSite")]
    assert coresite.facility_id.nunique() == 2


def test_distinct_site_codes_are_a_hard_blocker():
    left = pd.Series({"name": "CoreSite - Santa Clara (SV3)", "address": "2901 Coronado Dr"})
    right = pd.Series({"name": "CoreSite - Santa Clara (SV7)", "address": "2901 Coronado Dr"})
    assert dedupe.forbidden_pair(left, right) == "distinct_site_codes"


def test_distinct_street_numbers_are_a_hard_blocker():
    left = pd.Series({"name": "Digital Realty SJC", "address": "1525 Comstock Street"})
    right = pd.Series({"name": "Digital Realty SJC", "address": "1725 Comstock Street"})
    assert dedupe.forbidden_pair(left, right) == "distinct_street_numbers"


def test_matching_site_code_is_not_blocked():
    left = pd.Series({"name": "Csquare SFO1", "address": "2820 Northwestern Pkwy"})
    right = pd.Series({"name": "Centersquare Silicon Valley (SFO1)", "address": "2820 Northwestern Parkway"})
    assert dedupe.forbidden_pair(left, right) is None
    score, _ = dedupe.pair_score(left, right, 116.7)
    assert score >= 0.72, "shared site code should carry this pair over the threshold"


def test_constraints_survive_transitive_merging():
    """SV3-SV4 and SV4-SV7 may each merge, but SV3 and SV7 must never co-cluster.

    Plain Union-Find fails this: it merged all three through the intermediate.
    """
    records = pd.DataFrame(
        [
            {
                "source": "peeringdb",
                "source_id": str(i),
                "source_url": None,
                "name": f"CoreSite - Santa Clara (SV{code})",
                "operator_raw": "CoreSite",
                "operator": "CoreSite",
                "facility_class": "colocation",
                "lat": 37.38 + i * 0.0002,
                "lon": -121.98,
                "address": f"{2901 + i} Coronado Drive",
                "city": "Santa Clara",
                "postcode": None,
                "year_built": None,
                "building_levels": None,
                "footprint_sqm": None,
                "footprint_sqft": None,
                "geometry_wkt": None,
                "clli": None,
                "website": None,
                "pdb_net_count": None,
                "pdb_ix_count": None,
                "pdb_carrier_count": None,
            }
            for i, code in enumerate((3, 4, 7))
        ]
    )
    facilities, crosswalk, _ = dedupe.resolve(records)
    assert len(facilities) == 3

    groups = crosswalk.groupby("facility_id").source_name.apply(list)
    for names in groups:
        codes = {n.split("SV")[-1].rstrip(")") for n in names}
        assert not {"3", "7"} <= codes


def test_normalize_address_expands_abbreviations():
    assert dedupe.normalize_address("11 Great Oaks Boulevard") == "11 great oaks blvd"
    assert dedupe.normalize_address("200 Paul Ave") == "200 paul ave"
    assert dedupe.normalize_address(None) == ""


def test_facility_ids_are_unique(source_records):
    facilities, _, _ = _resolve(source_records)
    assert facilities.facility_id.is_unique
