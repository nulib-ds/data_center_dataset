"""Publication-quality figures.

Design rules applied throughout:

* Power charts are labelled as estimates. Every one carries the evidence tier in
  its encoding, because a bar whose height comes from a cited figure and a bar
  whose height comes from a floor-area model do not mean the same thing.
* Uncertainty is drawn where it exists. Aggregated intervals are produced by
  summing per-facility bounds, which assumes errors are correlated. That is the
  conservative choice -- it yields wider intervals than treating them as
  independent -- and it is stated on the figure.
* No time series claims to show construction. See ``provenance_timeline``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from .config import PROCESSED_DIR, TIER_AREA, TIER_ATTESTED, TIER_GENERATOR
from .normalize.classify import UNATTRIBUTED_OPERATOR

log = logging.getLogger(__name__)

FIGURE_DIR = PROCESSED_DIR / "figures"

# --------------------------------------------------------------------------
# Style
# --------------------------------------------------------------------------

TIER_COLOUR = {
    TIER_ATTESTED: "#2E7D32",
    TIER_GENERATOR: "#E0912F",
    TIER_AREA: "#3B8EA5",
}
TIER_LABEL = {
    TIER_ATTESTED: "Tier A – attested, cited",
    TIER_GENERATOR: "Tier B – generator permits",
    TIER_AREA: "Tier C – floor-area model",
}
NO_DATA_COLOUR = "#9AA5B1"
UNATTRIBUTED_COLOUR = "#A85273"
ACCENT = "#1F4E5F"
GRID = "#D8DEE4"
INK = "#22282E"

CAVEAT = (
    "Estimates, not meter readings. Per-facility consumption is not public for "
    "any California data center."
)


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.labelsize": 10.5,
            "axes.edgecolor": "#9AA5B1",
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "text.color": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.7,
            "legend.frameon": False,
            "figure.facecolor": "white",
        }
    )


def _footnote(fig, text: str) -> None:
    fig.text(0.005, -0.015, text, ha="left", va="top", fontsize=7.8, color="#5B6672")


def _save(fig, stem: str, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for ext in ("png", "svg"):
        path = outdir / f"{stem}.{ext}"
        fig.savefig(path)
        written.append(path)
    plt.close(fig)
    log.info("wrote %s.{png,svg}", stem)
    return written


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------


def _load() -> tuple[pd.DataFrame, pd.DataFrame]:
    fac = pd.read_csv(PROCESSED_DIR / "facilities.csv")
    est = pd.read_csv(PROCESSED_DIR / "power_estimates.csv")
    return fac, est


# --------------------------------------------------------------------------
# 1. Operators by estimated load
# --------------------------------------------------------------------------


def top_operators_by_load(fac: pd.DataFrame, outdir: Path, top_n: int = 16):
    """Horizontal bars, segmented by the evidence tier behind each MW."""
    df = fac[fac.best_power_mw.notna()].copy()

    pivot = (
        df.pivot_table(
            index="operator",
            columns="power_tier",
            values="best_power_mw",
            aggfunc="sum",
            fill_value=0.0,
        )
    )
    totals = pivot.sum(axis=1).sort_values(ascending=False).head(top_n)
    pivot = pivot.loc[totals.index]

    bounds = df.groupby("operator")[["power_ci_low_mw", "power_ci_high_mw"]].sum()
    sites_estimated = df.groupby("operator").facility_id.count()
    sites_total = fac.groupby("operator").facility_id.count()

    fig, ax = plt.subplots(figsize=(9.9, 7.6))
    y = np.arange(len(pivot))[::-1]
    left = np.zeros(len(pivot))

    for tier in (TIER_ATTESTED, TIER_GENERATOR, TIER_AREA):
        if tier not in pivot.columns:
            continue
        vals = pivot[tier].to_numpy(dtype=float)
        colours = [
            UNATTRIBUTED_COLOUR if op == UNATTRIBUTED_OPERATOR else TIER_COLOUR[tier]
            for op in pivot.index
        ]
        ax.barh(y, vals, left=left, color=colours, edgecolor="white", linewidth=0.6,
                height=0.74)
        left += vals

    lo = bounds.loc[pivot.index, "power_ci_low_mw"].to_numpy(dtype=float)
    hi = bounds.loc[pivot.index, "power_ci_high_mw"].to_numpy(dtype=float)
    mid = totals.to_numpy(dtype=float)
    ax.errorbar(
        mid, y,
        xerr=[np.clip(mid - lo, 0, None), np.clip(hi - mid, 0, None)],
        fmt="none", ecolor="#5B6672", elinewidth=1.1, capsize=2.6, alpha=0.85,
    )

    # Site counts read "estimated / total". Without the denominator the label
    # understates an operator's footprint: Digital Realty has 19 California
    # sites but only 15 of them can be given a load.
    labels = [
        f"{op}  ({sites_estimated.get(op, 0)}/{sites_total.get(op, 0)})"
        for op in pivot.index
    ]
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    for tick, op in zip(ax.get_yticklabels(), pivot.index):
        if op == UNATTRIBUTED_OPERATOR:
            tick.set_color(UNATTRIBUTED_COLOUR)
            tick.set_fontstyle("italic")

    # Place value labels clear of the whisker so they do not strike through it.
    pad = hi.max() * 0.014
    for yi, val, high in zip(y, mid, hi):
        ax.text(high + pad, yi, f"{val:,.0f}", va="center", fontsize=9,
                color="#40484F")

    ax.set_xlabel("Estimated IT load (MW), summed across sites")
    ax.set_title("Which companies draw the most power\nCalifornia data centers, by estimated IT load")
    ax.set_xlim(0, hi.max() * 1.13)
    ax.grid(axis="y", visible=False)

    handles = [
        Patch(facecolor=TIER_COLOUR[t], label=TIER_LABEL[t])
        for t in (TIER_GENERATOR, TIER_AREA)
        if t in pivot.columns
    ] + [
        Patch(facecolor=UNATTRIBUTED_COLOUR, label="Operator not publicly established"),
        Line2D([0], [0], color="#5B6672", lw=1.1, label="Summed CI bounds"),
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=9)

    _footnote(
        fig,
        f"{CAVEAT}  Parentheses show sites with an estimate / total sites for that "
        "operator. Bar segments show the evidence tier behind each megawatt.\n"
        "Intervals sum per-facility bounds, which assumes correlated errors and so "
        "is the wider, more conservative choice.",
    )
    return _save(fig, "01_top_operators_by_load", outdir)


# --------------------------------------------------------------------------
# 2. Utility
# --------------------------------------------------------------------------


def load_by_utility(fac: pd.DataFrame, outdir: Path):
    g = (
        fac.groupby("utility")
        .agg(sites=("facility_id", "count"), mw=("best_power_mw", "sum"))
        .sort_values("mw", ascending=False)
    )
    g = g[g.sites > 0].head(12)

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 5.4), sharey=True)
    y = np.arange(len(g))[::-1]

    axes[0].barh(y, g.mw, color=ACCENT, height=0.72)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(g.index)
    axes[0].set_xlabel("Estimated IT load (MW)")
    axes[0].set_title("Estimated load by serving utility", loc="left")
    axes[0].grid(axis="y", visible=False)
    for yi, v in zip(y, g.mw):
        axes[0].text(v + g.mw.max() * 0.015, yi, f"{v:,.0f}", va="center", fontsize=9)

    axes[1].barh(y, g.sites, color="#7FA9B8", height=0.72)
    axes[1].set_xlabel("Number of facilities")
    axes[1].set_title("Facility count by serving utility", loc="left")
    axes[1].grid(axis="y", visible=False)
    for yi, v in zip(y, g.sites):
        axes[1].text(v + g.sites.max() * 0.015, yi, f"{v:,.0f}", va="center", fontsize=9)

    fig.suptitle(
        "Silicon Valley Power carries California's largest data center concentration",
        fontsize=13, fontweight="bold", x=0.005, ha="left",
    )
    _footnote(
        fig,
        f"{CAVEAT}  Utility is assigned by point-in-polygon against CEC service "
        "territories, preferring the most specific retail provider.\nAttribution "
        "reflects geography, not supply contracts: direct access and community "
        "choice aggregation are not represented.",
    )
    return _save(fig, "02_load_and_sites_by_utility", outdir)


# --------------------------------------------------------------------------
# 3. Rank-size
# --------------------------------------------------------------------------


def rank_size_curve(fac: pd.DataFrame, outdir: Path):
    s = fac.best_power_mw.dropna().sort_values(ascending=False).reset_index(drop=True)
    if s.empty:
        return []
    ranks = np.arange(1, len(s) + 1)
    share = s.cumsum() / s.sum() * 100
    top10_share = share.iloc[min(9, len(share) - 1)]

    fig, ax = plt.subplots(figsize=(9.2, 5.6))
    ax.plot(ranks, s.to_numpy(), marker="o", ms=3.4, lw=1.4, color=ACCENT)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Facility rank (log)")
    ax.set_ylabel("Estimated IT load, MW (log)")
    # Title states what the data shows. An earlier draft claimed "a few sites
    # dominate", which its own annotations contradicted.
    ax.set_title(
        "Load is spread widely, not concentrated in a handful of sites\n"
        f"The largest 10 facilities hold {top10_share:.0f}% of estimated load"
    )

    ax2 = ax.twinx()
    ax2.plot(ranks, share.to_numpy(), lw=1.6, ls="--", color="#A85273")
    ax2.set_ylabel("Cumulative share of estimated load (%)", color="#A85273")
    ax2.tick_params(axis="y", colors="#A85273")
    ax2.set_ylim(0, 100)
    ax2.grid(False)

    for n in (5, 10, 25):
        if n <= len(s):
            pct = share.iloc[n - 1]
            ax2.annotate(
                f"top {n} = {pct:.0f}%",
                xy=(n, pct), xytext=(n * 1.25, pct - 11),
                fontsize=9, color="#A85273",
                arrowprops=dict(arrowstyle="-", color="#A85273", lw=0.8),
            )

    _footnote(
        fig,
        f"{CAVEAT}  {len(s)} of {len(fac)} facilities have an estimate; the "
        f"remaining {len(fac) - len(s)} are absent from this curve, so the true "
        "tail is longer than shown.",
    )
    return _save(fig, "03_rank_size_curve", outdir)


# --------------------------------------------------------------------------
# 4. Evidence composition
# --------------------------------------------------------------------------


def evidence_composition(fac: pd.DataFrame, outdir: Path):
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 5.0))

    counts = fac.power_tier.value_counts(dropna=False)
    order = [t for t in (TIER_ATTESTED, TIER_GENERATOR, TIER_AREA) if t in counts.index]
    labels = [TIER_LABEL[t] for t in order]
    values = [counts[t] for t in order]
    colours = [TIER_COLOUR[t] for t in order]
    n_missing = int(fac.best_power_mw.isna().sum())
    if n_missing:
        labels.append("No estimate available")
        values.append(n_missing)
        colours.append(NO_DATA_COLOUR)

    axes[0].barh(np.arange(len(values))[::-1], values, color=colours, height=0.7)
    axes[0].set_yticks(np.arange(len(values))[::-1])
    axes[0].set_yticklabels(labels)
    axes[0].set_xlabel("Number of facilities")
    axes[0].set_title("Facilities by strength of evidence", loc="left")
    axes[0].grid(axis="y", visible=False)
    for yi, v in zip(np.arange(len(values))[::-1], values):
        axes[0].text(v + max(values) * 0.015, yi, str(v), va="center", fontsize=9)

    mw = fac.groupby("power_tier").best_power_mw.sum()
    mw_order = [t for t in (TIER_ATTESTED, TIER_GENERATOR, TIER_AREA) if t in mw.index]
    axes[1].barh(
        np.arange(len(mw_order))[::-1],
        [mw[t] for t in mw_order],
        color=[TIER_COLOUR[t] for t in mw_order],
        height=0.7,
    )
    axes[1].set_yticks(np.arange(len(mw_order))[::-1])
    axes[1].set_yticklabels([TIER_LABEL[t] for t in mw_order])
    axes[1].set_xlabel("Estimated IT load (MW)")
    axes[1].set_title("Estimated megawatts by strength of evidence", loc="left")
    axes[1].grid(axis="y", visible=False)
    for yi, t in zip(np.arange(len(mw_order))[::-1], mw_order):
        axes[1].text(mw[t] + mw.max() * 0.015, yi, f"{mw[t]:,.0f}", va="center", fontsize=9)

    fig.suptitle(
        "No figure in this dataset rests on a cited source",
        fontsize=13, fontweight="bold", x=0.005, ha="left",
    )
    _footnote(
        fig,
        "Tier A is empty because no per-facility figure could be independently "
        "verified. The entire statewide total therefore rests on inference:\n"
        "backup-generator permit counts (Tier B) or building footprint "
        "(Tier C). Treat absolute magnitudes accordingly.",
    )
    return _save(fig, "04_evidence_composition", outdir)


# --------------------------------------------------------------------------
# 5. Tier agreement
# --------------------------------------------------------------------------


def tier_agreement(est: pd.DataFrame, outdir: Path):
    pivot = est.pivot_table(
        index="facility_id", columns="method", values="it_load_mw", aggfunc="first"
    )
    if TIER_GENERATOR not in pivot.columns or TIER_AREA not in pivot.columns:
        return []
    both = pivot[[TIER_GENERATOR, TIER_AREA]].dropna()
    if both.empty:
        return []

    x = both[TIER_GENERATOR].to_numpy()
    y = both[TIER_AREA].to_numpy()
    ratio = np.median(y / x)

    fig, ax = plt.subplots(figsize=(7.4, 6.8))
    lim = max(x.max(), y.max()) * 1.18
    ax.plot([0, lim], [0, lim], color="#5B6672", lw=1.2, ls="-", label="1 : 1 agreement")
    ax.plot([0, lim], [0, lim * ratio], color="#A85273", lw=1.4, ls="--",
            label=f"observed median ratio {ratio:.2f}")
    ax.scatter(x, y, s=64, color=ACCENT, alpha=0.78, edgecolor="white", linewidth=0.8, zorder=3)

    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_xlabel("Tier B estimate – backup generator permits (MW)")
    ax.set_ylabel("Tier C estimate – floor-area model (MW)")
    ax.set_title(
        f"Two independent methods disagree by about 2×\n{len(both)} facilities "
        "where both could be computed"
    )
    ax.legend(loc="upper left", fontsize=9)
    ax.set_aspect("equal")

    _footnote(
        fig,
        "Points below the 1:1 line mean the floor-area model returns less than the "
        "generator proxy. Either Tier B overestimates (its 1.30 redundancy divisor\n"
        "assumes N+1 where much California colocation is 2N), or Tier C "
        "underestimates (white-space fraction or W/sqft too low), or footprints "
        "cover only part of a campus.\nThe priors were deliberately not tuned to "
        "force agreement: that would manufacture confidence and destroy the "
        "independence that makes this comparison informative.",
    )
    return _save(fig, "05_tier_agreement", outdir)


# --------------------------------------------------------------------------
# 6. Completeness
# --------------------------------------------------------------------------


def completeness(fac: pd.DataFrame, outdir: Path):
    n = len(fac)
    checks = [
        ("Located (lat/lon)", fac.lat.notna() & fac.lon.notna()),
        ("Operator resolved to a company",
         fac.operator_confidence.isin(["alias", "alias_token"])),
        ("Serving utility assigned", fac.utility.notna()),
        ("Building footprint measured", fac.footprint_sqft.notna()),
        ("Any power estimate", fac.best_power_mw.notna()),
        ("Generator-permit evidence (Tier B)", fac.power_tier == TIER_GENERATOR),
        ("Cited figure (Tier A)", fac.power_tier == TIER_ATTESTED),
        ("Two or more independent sources", fac.n_sources > 1),
        ("Two or more power methods", fac.n_power_methods > 1),
        ("Real construction year known", fac.year_built.notna()),
    ]
    labels = [c[0] for c in checks]
    counts = [int(c[1].sum()) for c in checks]
    pct = [c / n * 100 for c in counts]

    fig, ax = plt.subplots(figsize=(9.6, 5.8))
    y = np.arange(len(labels))[::-1]
    colours = ["#3B8EA5" if p >= 50 else "#D9843B" if p >= 15 else "#B0413E" for p in pct]
    ax.barh(y, pct, color=colours, height=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel(f"Share of the {n} facilities (%)")
    ax.set_xlim(0, 108)
    ax.set_title("What is actually known about each facility")
    ax.grid(axis="y", visible=False)
    for yi, p, c in zip(y, pct, counts):
        ax.text(p + 1.4, yi, f"{p:.0f}%  ({c})", va="center", fontsize=9, color="#40484F")

    _footnote(
        fig,
        "Reading this chart is the honest way to use the dataset. Construction year "
        "is known for almost nothing, and no facility has a cited power figure,\n"
        "so any analysis leaning on those fields is unsupported.",
    )
    return _save(fig, "06_data_completeness", outdir)


# --------------------------------------------------------------------------
# 7. Interconnection vs load
# --------------------------------------------------------------------------


def interconnection_vs_load(fac: pd.DataFrame, outdir: Path):
    df = fac[fac.pdb_net_count.notna() & fac.best_power_mw.notna()].copy()
    if len(df) < 5:
        return []

    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    for tier, group in df.groupby("power_tier"):
        ax.scatter(
            group.best_power_mw, group.pdb_net_count,
            s=54, alpha=0.75, edgecolor="white", linewidth=0.7,
            color=TIER_COLOUR.get(tier, ACCENT), label=TIER_LABEL.get(tier, str(tier)),
        )

    # Spearman is Pearson on ranks; computing it that way avoids pulling in
    # scipy purely for one coefficient.
    rho = df.best_power_mw.rank().corr(df.pdb_net_count.rank())

    # Network counts span 0 to ~300 with two extreme carrier hotels, which on a
    # linear axis flattens every other point onto the baseline. symlog keeps the
    # genuine zeros (facilities with no public peering) that a log axis would
    # silently drop.
    ax.set_xscale("log")
    ax.set_yscale("symlog", linthresh=1)
    ax.set_xticks([1, 2, 5, 10, 20, 50])
    ax.set_yticks([0, 1, 5, 10, 50, 100, 300])
    ax.set_ylim(-0.25, 420)
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
        axis.set_minor_formatter(matplotlib.ticker.NullFormatter())

    ax.set_xlabel("Estimated IT load, MW (log)")
    ax.set_ylabel("Networks present, PeeringDB (symlog)")
    ax.set_title(
        "Interconnection density is not a proxy for size\n"
        f"Spearman ρ = {rho:.2f} across {len(df)} facilities"
    )
    ax.legend(fontsize=9, loc="upper left")

    _footnote(
        fig,
        "Carrier hotels concentrate networks in modest floor space, while "
        "hyperscale campuses draw great power with few public networks.\n"
        "A weak relationship is the expected result, and it argues against using "
        "peering counts to infer capacity.",
    )
    return _save(fig, "07_interconnection_vs_load", outdir)


# --------------------------------------------------------------------------
# 8. Provenance timeline -- the only temporal figure
# --------------------------------------------------------------------------


def provenance_timeline(history: pd.DataFrame, validation: pd.DataFrame, outdir: Path):
    """When these facilities entered OpenStreetMap. NOT when they were built.

    The refutation table is drawn onto the figure so the negative result cannot
    be separated from the chart and misread as construction history.
    """
    if history is None or history.empty:
        return []

    created = pd.to_numeric(history.created_year, errors="coerce").dropna().astype(int)
    tagged = pd.to_numeric(history.datacenter_tagged_year, errors="coerce").dropna().astype(int)
    years = np.arange(min(created.min(), tagged.min()), max(created.max(), tagged.max()) + 1)
    cum_created = [(created <= y).sum() for y in years]
    cum_tagged = [(tagged <= y).sum() for y in years]

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(10.2, 8.5),
        gridspec_kw={"height_ratios": [2.0, 0.92], "hspace": 0.30},
    )

    ax.step(years, cum_created, where="post", lw=2.0, color=ACCENT,
            label="Building footprint first drawn in OSM")
    ax.step(years, cum_tagged, where="post", lw=2.0, color="#D9843B", ls="--",
            label="First tagged as a data center")
    ax.fill_between(years, cum_tagged, cum_created, step="post", color=ACCENT, alpha=0.10)
    ax.set_ylabel("Cumulative OSM elements")
    ax.set_xlabel("Year")
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True, nbins=9))
    ax.set_title(
        "Dataset provenance: when OSM recorded these facilities\n"
        "This is mapping activity, NOT construction history"
    )
    ax.legend(fontsize=9, loc="upper left")
    idx = years.tolist().index(2018) if 2018 in years else len(years) // 2
    ax.annotate(
        "gap = years a building sat in OSM\nbefore anyone identified it as a data center",
        xy=(years[idx], (cum_created[idx] + cum_tagged[idx]) / 2),
        xytext=(years[0] + 0.4, max(cum_created) * 0.60), fontsize=8.6, color="#40484F",
        arrowprops=dict(arrowstyle="->", color="#8A949E", lw=0.9),
    )

    # -- refutation panel -------------------------------------------------
    ax2.axis("off")
    ax2.set_title(
        "Why no capacity-over-time chart is provided", loc="left", fontsize=11.5, pad=10
    )
    if validation is not None and not validation.empty:
        v = validation.copy()
        cells = []
        for _, r in v.iterrows():
            nm = str(r["name"]) if pd.notna(r["name"]) else "(unnamed)"
            cells.append([nm[:30], f"{int(r.real_year)}",
                          f"{int(r.osm_created_year)}", f"+{int(r.error_years)} yr"])
        table = ax2.table(
            cellText=cells,
            colLabels=["Facility", "Real start_date", "OSM first mapped", "Error"],
            cellLoc="left",
            colWidths=[0.42, 0.19, 0.21, 0.13],
            bbox=[0.0, 0.46, 0.92, 0.54],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8.5)
        for (row, _col), cell in table.get_celld().items():
            cell.set_edgecolor("#C8D0D8")
            if row == 0:
                cell.set_text_props(fontweight="bold")
                cell.set_facecolor("#EDF1F4")

        med = v.error_years.median()
        sub = v[v.real_year > 1950]
        corr = sub.real_year.corr(sub.osm_created_year) if len(sub) > 2 else float("nan")
        ax2.text(
            0.0, 0.34,
            f"Median error +{med:.0f} years. Excluding the single 1938 outlier the "
            f"correlation is {corr:.2f} — the OSM year is effectively constant at "
            "2016\nwhile real build years span 1973–1990, because the 2016 cluster is "
            "a Los Angeles mapping campaign rather than a\nbuilding boom. Changeset "
            "timestamps therefore measure OpenStreetMap, not California. A real "
            "construction year is\nknown for only 2 of 216 facilities, so neither "
            "facility counts nor load can be honestly plotted against time.",
            transform=ax2.transAxes, fontsize=8.8, va="top", color="#40484F",
            linespacing=1.5,
        )

    _footnote(
        fig,
        "Source: OpenStreetMap element version history via the OSM API, "
        f"{len(history)} California data-center elements.",
    )
    return _save(fig, "08_provenance_timeline", outdir)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def build_all(outdir: Path | None = None, history: pd.DataFrame | None = None) -> list[Path]:
    """Render every figure. Returns the paths written."""
    _apply_style()
    outdir = outdir or FIGURE_DIR
    fac, est = _load()

    written: list[Path] = []
    written += top_operators_by_load(fac, outdir)
    written += load_by_utility(fac, outdir)
    written += rank_size_curve(fac, outdir)
    written += evidence_composition(fac, outdir)
    written += tier_agreement(est, outdir)
    written += completeness(fac, outdir)
    written += interconnection_vs_load(fac, outdir)

    if history is None:
        history = _load_history()
    if history is not None and not history.empty:
        from .sources.osm_history import start_date_validation

        validation = start_date_validation(history)
        if validation is not None and not validation.empty:
            validation.to_csv(outdir / "osm_date_refutation.csv", index=False)
        written += provenance_timeline(history, validation, outdir)
    else:
        log.warning("no OSM history snapshot; skipping the provenance figure")

    return written


def _load_history() -> pd.DataFrame | None:
    import glob

    from .config import RAW_DIR

    paths = sorted(glob.glob(str(RAW_DIR / "osm_history" / "*" / "osm_element_history.csv")))
    if not paths:
        return None
    return pd.read_csv(paths[-1])
