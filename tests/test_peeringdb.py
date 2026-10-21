"""PeeringDB ingest: state assignment and the country-wide fetch.

PeeringDB's ``state`` query parameter is a full-text search, not an exact
match, so the pipeline fetches the whole country in one request and splits it
locally. These tests pin the split: a facility must land in the state it
actually belongs to, and the known data defects in the live feed must not
silently move a facility into a neighbour.
"""

from __future__ import annotations

import json

import pytest

from data_center_dataset.sources import peeringdb
from data_center_dataset.states import STATES


def test_postal_code_state_is_used_as_is():
    assert peeringdb.assign_state({"state": "CA"}) == "CA"
    assert peeringdb.assign_state({"state": "ca"}) == "CA"
    assert peeringdb.assign_state({"state": " CA "}) == "CA"


def test_full_state_name_is_translated_to_a_postal_code():
    """PeeringDB has seven records that spell the state out."""
    assert peeringdb.assign_state({"state": "South Carolina"}) == "SC"
    assert peeringdb.assign_state({"state": "North Carolina"}) == "NC"
    assert peeringdb.assign_state({"state": "New York"}) == "NY"
    assert peeringdb.assign_state({"state": "Wisconsin"}) == "WI"
    assert peeringdb.assign_state({"state": "Tennessee"}) == "TN"
    assert peeringdb.assign_state({"state": "Colorado"}) == "CO"


def test_missing_state_falls_back_to_the_zip_code():
    assert peeringdb.assign_state({"state": "", "zipcode": "82009"}) == "WY"
    assert peeringdb.assign_state({"state": None, "zipcode": "97013"}) == "OR"
    assert peeringdb.assign_state({"state": "", "zipcode": "FL 33487"}) == "FL"


def test_missing_state_falls_back_to_geography():
    assert (
        peeringdb.assign_state(
            {"state": "", "latitude": 26.405641, "longitude": -80.114041}
        )
        == "FL"
    )


def test_a_record_with_no_resolvable_state_is_left_unassigned():
    """Guessing is worse than leaving a record out."""
    assert peeringdb.assign_state({"state": "", "zipcode": "135-0061"}) is None
    assert peeringdb.assign_state({"state": "", "latitude": None, "longitude": None}) is None
    assert peeringdb.assign_state({}) is None


def test_territory_codes_are_identified_but_are_not_states():
    """Puerto Rico is a territory: it must not be filed under a state."""
    assert peeringdb.assign_state({"state": "PR", "zipcode": "00901"}) == "PR"
    assert peeringdb.assign_state({"state": "PR"}) == "PR"
    assert "PR" not in STATES


def test_parse_uses_the_assigned_state_not_the_raw_field():
    records = [
        {
            "id": 1,
            "status": "ok",
            "name": "Site",
            "org_name": "Equinix",
            "latitude": 35.0,
            "longitude": -80.0,
            "state": "South Carolina",
        }
    ]
    df = peeringdb.parse(records)
    assert df.loc[0, "state"] == "SC"


def test_parse_drops_records_without_coordinates():
    records = [
        {
            "id": 1,
            "status": "ok",
            "name": "Site",
            "org_name": "Equinix",
            "latitude": None,
            "longitude": None,
            "state": "SC",
        }
    ]
    assert peeringdb.parse(records).empty


def test_fetch_national_uses_one_request_and_splits_locally(tmp_path, monkeypatch):
    """The country-wide payload is fetched once; the split makes no requests."""

    def fake_raw_dir(source, snapshot, state=None):
        path = tmp_path / (state or "_none") / snapshot
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(peeringdb, "raw_dir", fake_raw_dir)

    calls: list[dict] = []

    class FakeClient:
        def fetch_json(self, url, params=None, refresh=False):
            calls.append(params)
            return {
                "data": [
                    {"id": 1, "status": "ok", "state": "CA", "latitude": 35.0, "longitude": -80.0},
                    {"id": 2, "status": "ok", "state": "SC", "latitude": 33.0, "longitude": -80.0},
                ]
            }

    records = peeringdb.fetch_national(FakeClient(), snapshot="2026-10-21")
    assert len(records) == 2
    assert len(calls) == 1
    assert calls[0]["limit"] == peeringdb.BULK_LIMIT
    assert calls[0]["country"] == "US"

    ca = peeringdb.fetch(FakeClient(), state="CA", snapshot="2026-10-21")
    assert [r["id"] for r in ca] == [1]
    # The split itself must not hit the network again.
    assert len(calls) == 1

    sc = peeringdb.fetch(FakeClient(), state="SC", snapshot="2026-10-21")
    assert [r["id"] for r in sc] == [2]


def test_fetch_reuses_the_snapshot_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        peeringdb, "raw_dir", lambda source, snapshot, state=None: tmp_path / state
    )
    (tmp_path / "CA").mkdir(parents=True)
    (tmp_path / "CA" / "peeringdb_fac.json").write_text(
        json.dumps([{"id": 99, "state": "CA"}])
    )

    class ExplodingClient:
        def fetch_json(self, *a, **k):  # pragma: no cover - must not be called
            raise AssertionError("network call made for a cached snapshot")

    out = peeringdb.fetch(ExplodingClient(), state="CA", snapshot="2026-10-21")
    assert out == [{"id": 99, "state": "CA"}]
