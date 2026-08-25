"""Scope filtering and operator canonicalisation."""

from __future__ import annotations

import pandas as pd

from data_center_dataset.normalize import classify


def test_operator_aliases_resolve_to_canonical_names():
    assert classify.canonical_operator("Equinix, Inc.") == "Equinix"
    assert classify.canonical_operator("Digital Realty Trust") == "Digital Realty"
    assert classify.canonical_operator("Stack Infrastructure, Incorporated") == (
        "STACK Infrastructure"
    )
    # Telx and DuPont Fabros were both absorbed by Digital Realty.
    assert classify.canonical_operator("Telx") == "Digital Realty"
    assert classify.canonical_operator(None) is None
    assert classify.canonical_operator("   ") is None


def test_campus_computer_rooms_are_out_of_scope(source_records):
    in_scope, excluded = classify.apply(source_records)
    assert "SDSU Computer Room" in set(excluded.name)
    assert (
        excluded.loc[excluded.name == "SDSU Computer Room", "exclusion_rule"].iloc[0]
        == "education_facility"
    )


def test_street_names_do_not_trigger_institution_rules():
    """"Mission College Boulevard" is an address in Santa Clara, not a school.

    Several genuine data centers sit on it, and an earlier version of the filter
    excluded every one of them.
    """
    row = pd.Series(
        {
            "source": "osm",
            "name": "QTS Santa Clara 1",
            "operator_raw": "Quality Technology Services",
            "address": "2805 Mission College Boulevard",
            "raw_json": None,
        }
    )
    facility_class, rule, operator, _ = classify.classify_row(row)
    assert rule is None
    assert operator == "QTS"


def test_out_of_state_coordinates_are_rejected(source_records):
    """PeeringDB genuinely mislabels some non-California facilities as CA."""
    in_scope, excluded = classify.apply(source_records)
    assert "QTS Charlotte (CTL1)" not in set(in_scope.name)
    assert (
        excluded.loc[excluded.name == "QTS Charlotte (CTL1)", "exclusion_rule"].iloc[0]
        == "outside_california"
    )


def test_carrier_sites_in_peeringdb_are_kept_but_osm_central_offices_are_not():
    """Presence in PeeringDB means the carrier sells colocation there."""
    pdb = pd.Series(
        {
            "source": "peeringdb",
            "name": "Level(3) Los Angeles",
            "operator_raw": "Level 3",
            "address": "1200 West 7th Street",
            "raw_json": None,
        }
    )
    facility_class, rule, _, _ = classify.classify_row(pdb)
    assert rule is None
    assert facility_class == classify.CLASS_COLOCATION

    osm = pd.Series(
        {
            "source": "osm",
            "name": "AT&T Switching Center",
            "operator_raw": "AT&T",
            "address": None,
            "raw_json": None,
        }
    )
    _, rule, _, _ = classify.classify_row(osm)
    assert rule == "telecom_central_office"


def test_hyperscaler_and_wholesale_classes():
    for operator, expected in (
        ("Amazon Web Services", classify.CLASS_HYPERSCALE),
        ("Vantage Data Centers", classify.CLASS_WHOLESALE),
        ("Equinix", classify.CLASS_COLOCATION),
    ):
        row = pd.Series(
            {"source": "osm", "name": f"{operator} Site", "operator_raw": operator,
             "address": None, "raw_json": None}
        )
        facility_class, rule, _, _ = classify.classify_row(row)
        assert rule is None
        assert facility_class == expected


def test_generator_fleet_gate_drops_nei_only_offices():
    """A single permitted generator marks an office, not a data center."""
    facilities = pd.DataFrame(
        [
            {"facility_id": "google-office", "source_list": "epa_nei"},
            {"facility_id": "google-dc", "source_list": "epa_nei"},
            {"facility_id": "small-colo", "source_list": "epa_nei,peeringdb"},
        ]
    )
    crosswalk = pd.DataFrame(
        [
            {"facility_id": "google-office", "source": "epa_nei", "source_id": "1"},
            {"facility_id": "google-dc", "source": "epa_nei", "source_id": "2"},
            {"facility_id": "small-colo", "source": "epa_nei", "source_id": "3"},
        ]
    )
    inventory = pd.DataFrame(
        [
            {"source_id": "1", "n_generator_units": 1},
            {"source_id": "2", "n_generator_units": 49},
            {"source_id": "3", "n_generator_units": 1},
        ]
    )

    kept, dropped = classify.post_resolution_gate(facilities, crosswalk, inventory)

    assert set(kept.facility_id) == {"google-dc", "small-colo"}
    assert set(dropped.facility_id) == {"google-office"}
    assert dropped.exclusion_rule.iloc[0] == "nei_only_insufficient_generator_fleet"
