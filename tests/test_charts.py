"""Figure generation must survive degenerate states.

Most US states have thin coverage, and some have none of a particular kind:
no OSM footprint polygon (so no Tier C), no EPA NEI permit (so no Tier B), no
PeeringDB entry (so no interconnection count). A figure that divides by a
facility count, or scales a marker by a maximum, must skip itself rather
than take the whole per-state build down with it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from data_center_dataset import charts


def _facilities(n: int = 5, *, with_power: bool = False) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "facility_id": f"f{i}",
                "name": f"Site {i}",
                "operator": "Example",
                "operator_confidence": None,
                "facility_class": "colocation",
                "lat": 41.0 + i * 0.01,
                "lon": -72.0,
                "address": None,
                "city": None,
                "postcode": "06001",
                "utility": None,
                "utility_candidates": None,
                "year_built": None,
                "building_levels_reported": None,
                "building_levels_used": None,
                "partial_occupancy": False,
                "footprint_sqft": None if not with_power else 100_000.0,
                "est_gross_sqft": None if not with_power else 100_000.0,
                "est_white_space_sqft": None if not with_power else 60_000.0,
                "best_power_mw": None if not with_power else 2.0,
                "power_ci_low_mw": None if not with_power else 1.0,
                "power_ci_high_mw": None if not with_power else 3.0,
                "power_tier": None if not with_power else "C_area",
                "n_power_methods": 0 if not with_power else 1,
                "est_annual_gwh": None if not with_power else 17.5,
                "pdb_net_count": None,
                "pdb_ix_count": None,
                "pdb_carrier_count": None,
                "clli": None,
                "website": None,
                "source_list": "osm",
                "n_sources": 1,
            }
        )
    return pd.DataFrame(rows)


def test_no_power_estimate_means_no_operator_load_figure(tmp_path):
    """A state with sites but no estimates has no load to rank."""
    written = charts.top_operators_by_load(_facilities(), tmp_path, "Testland")
    assert written == []


def test_operator_load_figure_is_written_when_there_is_power(tmp_path):
    written = charts.top_operators_by_load(_facilities(with_power=True), tmp_path, "Testland")
    assert written
    assert all(Path(p).exists() for p in written)


def test_operator_load_figure_survives_an_unresolved_operator(tmp_path):
    """North Dakota's shape: every site has an estimate but no operator.

    A null operator is a null *index* value, and ``pivot_table`` drops those.
    Left alone, the pivot comes back empty and the figure raises on an empty
    array of interval bounds.
    """
    fac = _facilities(with_power=True)
    fac["operator"] = None
    written = charts.top_operators_by_load(fac, tmp_path, "Testland")
    assert written
    assert all(Path(p).exists() for p in written)


def test_charts_build_all_skips_a_state_with_no_facilities(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    empty = pd.DataFrame(columns=_facilities(1).columns)
    empty.to_csv(processed / "facilities.csv", index=False)
    pd.DataFrame(columns=["facility_id", "method", "it_load_mw"]).to_csv(
        processed / "power_estimates.csv", index=False
    )
    assert charts.build_all(tmp_path / "figures", state="RI", processed_dir=processed) == []


def test_charts_build_all_survives_a_state_with_no_estimates(tmp_path):
    """Rhode Island's shape: sites, but no Tier B and no Tier C."""
    processed = tmp_path / "processed"
    processed.mkdir()
    _facilities().to_csv(processed / "facilities.csv", index=False)
    pd.DataFrame(columns=["facility_id", "method", "it_load_mw"]).to_csv(
        processed / "power_estimates.csv", index=False
    )
    written = charts.build_all(tmp_path / "figures", state="RI", processed_dir=processed)
    assert written, "the completeness and evidence-composition figures should still be written"
    assert all(Path(p).exists() for p in written)


def test_rank_size_curve_needs_a_estimate(tmp_path):
    assert charts.rank_size_curve(_facilities(), tmp_path, "Testland") == []
    assert charts.rank_size_curve(_facilities(with_power=True), tmp_path, "Testland")
