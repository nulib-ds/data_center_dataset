"""Power estimation across all three tiers."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from data_center_dataset.config import (
    CRITICAL_TO_IT,
    GENERATOR_REDUNDANCY_FACTOR,
    TIER_AREA,
    TIER_ATTESTED,
    TIER_GENERATOR,
)
from data_center_dataset.power import generators, model, reconcile
from data_center_dataset.sources.epa_nei import GENERATOR_KW_MID, parse_unit_capacity_kw


# -- Tier B ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("G1 1500 KW EMERGENCY GENERATOR", 1500.0),
        ("947 BHP EMERGENCY IC ENGINE", 947 * 0.746),
        ("2 MW STANDBY GENERATOR", 2000.0),
        ("STANDBY GENERATOR SN-6", None),
        ("", None),
        (None, None),
        # Implausible values are rejected rather than trusted.
        ("PERMIT 99999 KW", None),
        ("10 KW UNIT", None),
    ],
)
def test_generator_capacity_parsing(text, expected):
    result = parse_unit_capacity_kw(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, rel=1e-3)


def test_tier_b_uses_parsed_ratings_and_prior_for_the_rest():
    facilities = pd.DataFrame(
        [{"facility_id": "f1", "facility_class": "colocation", "year_built": 2018}]
    )
    crosswalk = pd.DataFrame(
        [{"facility_id": "f1", "source": "epa_nei", "source_id": "10"}]
    )
    # Ten generators: two rated at 1500 kW each, eight unrated.
    inventory = pd.DataFrame(
        [
            {
                "source_id": "10",
                "n_generator_units": 10,
                "n_units_rated": 2,
                "rated_kw_sum": 3000.0,
            }
        ]
    )

    out = generators.estimate(
        facilities, inventory, crosswalk, model.make_pue_lookup()
    )
    assert len(out) == 1
    row = out.iloc[0]

    expected_nameplate = (3000.0 + 8 * GENERATOR_KW_MID) / 1000.0
    assert row.generator_nameplate_mw == pytest.approx(expected_nameplate)
    assert row.it_load_mw == pytest.approx(
        expected_nameplate / GENERATOR_REDUNDANCY_FACTOR * CRITICAL_TO_IT
    )
    assert row.method == TIER_GENERATOR
    assert row.ci_low_mw <= row.it_load_mw <= row.ci_high_mw

    # The redundancy assumption must be recoverable from the published row.
    assumptions = json.loads(row.assumptions_json)
    assert assumptions["redundancy_factor"] == GENERATOR_REDUNDANCY_FACTOR
    assert assumptions["n_units_with_parsed_rating"] == 2


def test_tier_b_skips_facilities_without_generators():
    facilities = pd.DataFrame([{"facility_id": "f1", "facility_class": "colocation"}])
    crosswalk = pd.DataFrame(
        [{"facility_id": "f1", "source": "epa_nei", "source_id": "10"}]
    )
    inventory = pd.DataFrame(
        [{"source_id": "10", "n_generator_units": 0, "n_units_rated": 0, "rated_kw_sum": 0.0}]
    )
    assert generators.estimate(
        facilities, inventory, crosswalk, model.make_pue_lookup()
    ).empty


# -- Tier C ----------------------------------------------------------------


def test_tier_c_requires_a_measured_footprint():
    """A missing polygon must yield no estimate, not a zero-power data center."""
    facilities = pd.DataFrame(
        [
            {
                "facility_id": "no-geom",
                "facility_class": "colocation",
                "year_built": 2018,
                "est_gross_sqft": None,
            },
            {
                "facility_id": "with-geom",
                "facility_class": "colocation",
                "year_built": 2018,
                "est_gross_sqft": 100_000.0,
            },
        ]
    )
    out = model.estimate(facilities)
    assert set(out.facility_id) == {"with-geom"}


def test_tier_c_scales_with_area_and_brackets_its_estimate():
    facilities = pd.DataFrame(
        [
            {
                "facility_id": "a",
                "facility_class": "colocation",
                "year_built": 2020,
                "est_gross_sqft": 100_000.0,
            },
            {
                "facility_id": "b",
                "facility_class": "colocation",
                "year_built": 2020,
                "est_gross_sqft": 200_000.0,
            },
        ]
    )
    out = model.estimate(facilities).set_index("facility_id")
    assert out.loc["b", "it_load_mw"] == pytest.approx(2 * out.loc["a", "it_load_mw"])
    for fid in ("a", "b"):
        assert out.loc[fid, "ci_low_mw"] <= out.loc[fid, "it_load_mw"]
        assert out.loc[fid, "it_load_mw"] <= out.loc[fid, "ci_high_mw"]
        assert out.loc[fid, "method"] == TIER_AREA


def test_newer_and_denser_classes_get_higher_power_density():
    old = model.lookup_prior("colocation", 1998)["w_per_sqft"]["mid"]
    new = model.lookup_prior("colocation", 2022)["w_per_sqft"]["mid"]
    hyper = model.lookup_prior("hyperscale", 2022)["w_per_sqft"]["mid"]
    assert old < new < hyper

    # Newer builds are more efficient, so PUE should fall.
    assert model.lookup_prior("colocation", 2022)["pue"]["mid"] < (
        model.lookup_prior("colocation", 1998)["pue"]["mid"]
    )


def test_unknown_class_falls_back_without_raising():
    prior = model.lookup_prior("not-a-real-class", None)
    assert prior["w_per_sqft"]["mid"] > 0


# -- Tier resolution and reconciliation ------------------------------------


def test_tier_precedence_prefers_attested_then_generator():
    estimates = pd.DataFrame(
        [
            {
                "facility_id": "f1",
                "method": TIER_AREA,
                "it_load_mw": 5.0,
                "ci_low_mw": 3.0,
                "ci_high_mw": 8.0,
                "annual_gwh": 40.0,
            },
            {
                "facility_id": "f1",
                "method": TIER_ATTESTED,
                "it_load_mw": 12.0,
                "ci_low_mw": 10.0,
                "ci_high_mw": 14.0,
                "annual_gwh": 90.0,
            },
            {
                "facility_id": "f1",
                "method": TIER_GENERATOR,
                "it_load_mw": 9.0,
                "ci_low_mw": 5.0,
                "ci_high_mw": 15.0,
                "annual_gwh": 70.0,
            },
            {
                "facility_id": "f2",
                "method": TIER_GENERATOR,
                "it_load_mw": 3.0,
                "ci_low_mw": 2.0,
                "ci_high_mw": 5.0,
                "annual_gwh": 20.0,
            },
        ]
    )
    out = reconcile.resolve(estimates).set_index("facility_id")
    assert out.loc["f1", "power_tier"] == TIER_ATTESTED
    assert out.loc["f1", "best_power_mw"] == 12.0
    assert out.loc["f1", "n_power_methods"] == 3
    assert out.loc["f2", "power_tier"] == TIER_GENERATOR


def test_calibration_never_rescales_attested_figures():
    facilities = pd.DataFrame(
        [
            {
                "facility_id": "attested",
                "power_tier": TIER_ATTESTED,
                "best_power_mw": 100.0,
                "power_ci_low_mw": 90.0,
                "power_ci_high_mw": 110.0,
                "est_annual_gwh": 10.0,
            },
            {
                "facility_id": "modelled",
                "power_tier": TIER_AREA,
                "best_power_mw": 100.0,
                "power_ci_low_mw": 90.0,
                "power_ci_high_mw": 110.0,
                "est_annual_gwh": 10.0,
            },
        ]
    )
    out, report = reconcile.reconcile(facilities, apply_calibration=True)
    attested = out[out.facility_id == "attested"].iloc[0]
    modelled = out[out.facility_id == "modelled"].iloc[0]

    assert attested.best_power_mw == 100.0, "cited figures must never be rescaled"
    assert modelled.best_power_mw != 100.0
    assert report["calibration_applied"] is True


def test_reconciliation_reports_range_membership():
    facilities = pd.DataFrame(
        [{"facility_id": "f", "power_tier": TIER_AREA, "best_power_mw": 10.0,
          "est_annual_gwh": 15_000.0}]
    )
    _, report = reconcile.reconcile(facilities)
    assert report["bottom_up_annual_twh"] == pytest.approx(15.0)
    assert report["within_anchor_range"] is True


def test_agreement_report_quantifies_cross_method_disagreement():
    estimates = pd.DataFrame(
        [
            {"facility_id": "f1", "method": TIER_GENERATOR, "it_load_mw": 10.0},
            {"facility_id": "f1", "method": TIER_AREA, "it_load_mw": 5.0},
            {"facility_id": "f2", "method": TIER_GENERATOR, "it_load_mw": 20.0},
            {"facility_id": "f2", "method": TIER_AREA, "it_load_mw": 10.0},
        ]
    )
    report = reconcile.agreement_report(estimates)
    row = report[report.pair == f"{TIER_AREA} / {TIER_GENERATOR}"].iloc[0]
    assert row.median_ratio == pytest.approx(0.5)
    assert row.n_facilities == 2
