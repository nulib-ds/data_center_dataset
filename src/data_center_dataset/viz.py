"""Interactive Plotly map of California data centers on satellite imagery.

Design notes
------------
**Basemap.** Uses Plotly's MapLibre ``map`` traces with ``style="satellite"``,
which resolves to ESRI World Imagery raster tiles and needs no access token.
The older ``Scattermapbox`` traces would require a Mapbox key for satellite.

**What the visual channels encode.** Marker area scales with estimated IT load,
and colour encodes the *evidence tier* behind that estimate. Those are the two
things a reader needs simultaneously: how big the site is, and how much the
number can be trusted.

**Facilities with no estimate are drawn, not dropped.** 103 of 221 sites have no
power figure at all. Omitting them would imply the map shows all California data
center load, and silently overstate coverage. They appear as small hollow
markers.

**Building footprints.** The 88 known outlines are added as a fill layer. At
statewide zoom they are invisible; zoom into Santa Clara or downtown Los Angeles
and the actual buildings resolve under the markers.

**The caption is part of the figure.** A map of "power draw" invites the reading
that these are metered values. They are not, and the annotation says so.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from .config import PROCESSED_DIR, TIER_AREA, TIER_ATTESTED, TIER_GENERATOR

log = logging.getLogger(__name__)

OUTPUT_PATH = PROCESSED_DIR / "california_data_centers_map.html"

#: Bright, high-chroma colours chosen to survive a dark, textured satellite
#: background. Ordered so that stronger evidence reads as warmer.
TIER_STYLE = {
    TIER_ATTESTED: ("#39FF7A", "Tier A - attested, cited"),
    TIER_GENERATOR: ("#FFB000", "Tier B - backup generator permits"),
    TIER_AREA: ("#00D4FF", "Tier C - floor-area model"),
}
#: Mid-tone cool grey. Pure white was invisible against the pale rooftops of
#: downtown Los Angeles, where a real cluster of unestimated sites sits.
NO_ESTIMATE_COLOUR = "#B9C4D4"

#: Utilities given their own colour in the alternative view. Anything else is
#: pooled into "Other", so the legend stays readable.
UTILITY_STYLE = {
    "SVP": "#FF3B6B",
    "PG&E": "#FFB000",
    "LADWP": "#00D4FF",
    "SCE": "#B26BFF",
    "SDG&E": "#39FF7A",
    "SMUD": "#FF8A3D",
}
OTHER_UTILITY_COLOUR = "#C9C9C9"

#: Framing. Data spans roughly 32.5-38.8 N and -122.7 to -117.0 E, i.e. the
#: populated corridor from Sacramento to San Diego rather than the whole state.
#: On a wide desktop viewport, any zoom that fits the latitude range leaves
#: horizontal slack, so the default is a compromise found by rendering and
#: inspecting. The region presets below exist because the data is genuinely
#: bimodal -- statewide view cannot resolve either cluster.
CALIFORNIA_CENTER = {"lat": 35.75, "lon": -119.40}
DEFAULT_ZOOM = 6.05

#: Preset views. Silicon Valley and Los Angeles hold the overwhelming majority
#: of facilities and are unreadable at statewide zoom.
REGION_VIEWS = [
    ("All California", 35.75, -119.40, 6.05),
    ("Silicon Valley", 37.38, -121.96, 11.2),
    ("San Francisco", 37.77, -122.40, 12.4),
    ("Los Angeles", 34.05, -118.25, 11.6),
    ("Sacramento", 38.60, -121.45, 10.8),
    ("San Diego", 32.85, -117.13, 11.2),
]

#: Largest marker diameter in pixels, for the largest facility.
MAX_MARKER_PX = 42
MIN_MARKER_PX = 6.5


def _load(path=None) -> pd.DataFrame:
    path = path or PROCESSED_DIR / "facilities.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `data-center-dataset build` first."
        )
    return pd.read_csv(path)


def _load_footprints() -> dict | None:
    path = PROCESSED_DIR / "facility_footprints.geojson"
    if not path.exists():
        log.info("no footprint layer found; map will show markers only")
        return None
    return json.loads(path.read_text())


def _fmt(value: object, unit: str = "", digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "not estimated"
    if isinstance(value, str):
        return value
    return f"{value:,.{digits}f}{unit}"


def _hover_text(row: pd.Series) -> str:
    """Rich hover card. Power is always shown with its interval and tier."""
    tier = row.get("power_tier")
    if pd.isna(tier):
        power_block = (
            "<b>Power:</b> no estimate<br>"
            "<span style='font-size:11px'>No footprint geometry, generator "
            "permit, or cited figure available</span>"
        )
    else:
        label = TIER_STYLE.get(tier, ("", str(tier)))[1]
        power_block = (
            f"<b>Est. IT load:</b> {_fmt(row.best_power_mw, ' MW')}<br>"
            f"<b>Range:</b> {_fmt(row.power_ci_low_mw)} – "
            f"{_fmt(row.power_ci_high_mw)} MW<br>"
            f"<b>Evidence:</b> {label}<br>"
            f"<b>Est. annual:</b> {_fmt(row.est_annual_gwh, ' GWh/yr', 0)}"
        )

    footprint = row.get("footprint_sqft")
    footprint_line = (
        f"<b>Footprint:</b> {_fmt(footprint, ' sq ft', 0)}<br>"
        if pd.notna(footprint)
        else ""
    )
    partial = (
        "<br><i>Tenant in a taller building; occupied share unknown</i>"
        if bool(row.get("partial_occupancy"))
        else ""
    )
    nets = row.get("pdb_net_count")
    nets_line = (
        f"<b>Networks present:</b> {int(nets)}<br>" if pd.notna(nets) and nets else ""
    )

    return (
        f"<b style='font-size:14px'>{row['name']}</b><br>"
        f"{'<i>' + str(row.operator) + '</i><br>' if pd.notna(row.operator) else ''}"
        f"{str(row.city) + '<br>' if pd.notna(row.city) else ''}"
        "<br>"
        f"{power_block}<br><br>"
        f"<b>Utility:</b> {_fmt(row.utility)}<br>"
        f"<b>Class:</b> {_fmt(row.facility_class)}<br>"
        f"{footprint_line}"
        f"{nets_line}"
        f"<b>Sources:</b> {_fmt(row.source_list)}"
        f"{partial}"
        "<extra></extra>"
    )


def _marker_sizes(mw: pd.Series, max_mw: float) -> pd.Series:
    """Scale marker *area* with load, so visual weight matches magnitude.

    Encoding load as diameter would exaggerate large sites roughly quadratically,
    a well-known way to mislead with bubble maps.
    """
    if max_mw <= 0:
        return pd.Series(MIN_MARKER_PX, index=mw.index)
    scaled = (mw.clip(lower=0) / max_mw) ** 0.5 * MAX_MARKER_PX
    return scaled.clip(lower=MIN_MARKER_PX)


def build_figure(facilities: pd.DataFrame | None = None):
    """Assemble the Plotly figure. Returns a ``plotly.graph_objects.Figure``."""
    import plotly.graph_objects as go

    df = facilities if facilities is not None else _load()
    df = df.copy()
    df["_hover"] = df.apply(_hover_text, axis=1)

    has_power = df[df.best_power_mw.notna()]
    no_power = df[df.best_power_mw.isna()]
    max_mw = float(has_power.best_power_mw.max()) if not has_power.empty else 0.0

    fig = go.Figure()
    tier_trace_count = 0

    # ---- View 1: coloured by evidence tier -----------------------------
    for tier, (colour, label) in TIER_STYLE.items():
        sub = has_power[has_power.power_tier == tier]
        if sub.empty:
            continue
        total = sub.best_power_mw.sum()
        fig.add_trace(
            go.Scattermap(
                lat=sub.lat,
                lon=sub.lon,
                mode="markers",
                name=f"{label}  ({len(sub)} sites, {total:,.0f} MW)",
                legendgroup="tier",
                marker=dict(
                    size=_marker_sizes(sub.best_power_mw, max_mw),
                    color=colour,
                    opacity=0.82,
                    allowoverlap=True,
                ),
                hovertemplate=sub._hover,
                visible=True,
            )
        )
        tier_trace_count += 1

    if not no_power.empty:
        fig.add_trace(
            go.Scattermap(
                lat=no_power.lat,
                lon=no_power.lon,
                mode="markers",
                name=f"No power estimate  ({len(no_power)} sites)",
                legendgroup="tier",
                marker=dict(
                    size=7.5,
                    color=NO_ESTIMATE_COLOUR,
                    opacity=0.80,
                    allowoverlap=True,
                ),
                hovertemplate=no_power._hover,
                visible=True,
            )
        )
        tier_trace_count += 1

    # ---- View 2: coloured by serving utility ---------------------------
    utility_trace_count = 0
    df["_utility_group"] = df.utility.where(
        df.utility.isin(UTILITY_STYLE), "Other / municipal"
    )
    order = list(UTILITY_STYLE) + ["Other / municipal"]
    for utility in order:
        sub = df[df._utility_group == utility]
        if sub.empty:
            continue
        colour = UTILITY_STYLE.get(utility, OTHER_UTILITY_COLOUR)
        total = sub.best_power_mw.sum(min_count=1)
        n_est_here = int(sub.best_power_mw.notna().sum())
        total_txt = f", {total:,.0f} MW" if pd.notna(total) else ""
        sizes = _marker_sizes(sub.best_power_mw.fillna(0.0), max_mw)
        # Sites with no estimate are drawn at minimum size, which would read as
        # "small facility". Fading them keeps "unknown" visually distinct from
        # "known to be small".
        opacity = [0.86 if pd.notna(v) else 0.34 for v in sub.best_power_mw]
        fig.add_trace(
            go.Scattermap(
                lat=sub.lat,
                lon=sub.lon,
                mode="markers",
                name=(
                    f"{utility}  ({len(sub)} sites{total_txt}"
                    f"; {len(sub) - n_est_here} unestimated)"
                ),
                legendgroup="utility",
                marker=dict(
                    size=sizes, color=colour, opacity=opacity, allowoverlap=True
                ),
                hovertemplate=sub._hover,
                visible=False,
            )
        )
        utility_trace_count += 1

    # ---- Footprint polygons -------------------------------------------
    layers = []
    n_footprints = 0
    footprints = _load_footprints()
    if footprints and footprints.get("features"):
        n_footprints = len(footprints["features"])
        # Neutral white rather than a hue: footprints are geometry, not a
        # category. An earlier yellow was easily confused with the amber Tier B
        # markers sitting on top of them.
        #
        # Drawn as fill + dark casing + bright line. The casing is the standard
        # cartographic trick for keeping a stroke legible over unpredictable
        # imagery: a plain white outline vanishes against the pale rooftops of
        # downtown Los Angeles, and a plain dark one vanishes over water.
        layers.append(
            dict(
                sourcetype="geojson",
                source=footprints,
                type="fill",
                color="rgba(255, 255, 255, 0.16)",
                below="",
            )
        )
        layers.append(
            dict(
                sourcetype="geojson",
                source=footprints,
                type="line",
                color="rgba(0, 0, 0, 0.55)",
                line=dict(width=3.6),
            )
        )
        layers.append(
            dict(
                sourcetype="geojson",
                source=footprints,
                type="line",
                color="rgba(255, 255, 255, 0.95)",
                line=dict(width=1.5),
            )
        )

    # ---- Controls ------------------------------------------------------
    n_tier, n_util = tier_trace_count, utility_trace_count
    colour_buttons = [
        dict(
            label="Colour: evidence tier",
            method="update",
            args=[{"visible": [True] * n_tier + [False] * n_util}],
        ),
        dict(
            label="Colour: serving utility",
            method="update",
            args=[{"visible": [False] * n_tier + [True] * n_util}],
        ),
    ]
    basemap_buttons = [
        dict(label="Satellite", method="relayout", args=["map.style", "satellite"]),
        dict(
            label="Satellite + streets",
            method="relayout",
            args=["map.style", "satellite-streets"],
        ),
        dict(label="Dark", method="relayout", args=["map.style", "dark"]),
        dict(label="Outdoors", method="relayout", args=["map.style", "outdoors"]),
    ]
    region_buttons = [
        dict(
            label=label,
            method="relayout",
            args=[{"map.center": {"lat": lat, "lon": lon}, "map.zoom": zoom}],
        )
        for label, lat, lon, zoom in REGION_VIEWS
    ]

    n_sites = len(df)
    n_est = len(has_power)
    total_mw = has_power.best_power_mw.sum() if not has_power.empty else 0.0
    total_twh = (
        df.est_annual_gwh.sum() / 1000.0 if "est_annual_gwh" in df.columns else 0.0
    )

    fig.update_layout(
        title=dict(
            text=(
                f"<b>California data centers</b>  ·  {n_sites} commercial "
                f"facilities  ·  {n_est} with an estimated load totalling "
                f"~{total_mw:,.0f} MW IT (~{total_twh:,.1f} TWh/yr)"
            ),
            x=0.01,
            xanchor="left",
            font=dict(size=17, color="#F5F5F5"),
        ),
        map=dict(
            style="satellite",
            center=CALIFORNIA_CENTER,
            zoom=DEFAULT_ZOOM,
            layers=layers,
        ),
        margin=dict(l=0, r=0, t=84, b=92),
        paper_bgcolor="#0B0E13",
        font=dict(color="#E8E8E8", family="Inter, Helvetica, Arial, sans-serif"),
        legend=dict(
            title=dict(text="<b>Marker area ∝ estimated IT load</b>"),
            bgcolor="rgba(11,14,19,0.80)",
            bordercolor="rgba(255,255,255,0.22)",
            borderwidth=1,
            x=0.008,
            y=0.015,
            xanchor="left",
            yanchor="bottom",
            font=dict(size=11),
        ),
        updatemenus=[
            dict(
                buttons=colour_buttons,
                direction="down",
                x=0.998,
                xanchor="right",
                y=0.995,
                yanchor="top",
                bgcolor="rgba(11,14,19,0.88)",
                bordercolor="rgba(255,255,255,0.22)",
                font=dict(size=11),
                showactive=True,
            ),
            dict(
                buttons=basemap_buttons,
                direction="down",
                x=0.998,
                xanchor="right",
                y=0.905,
                yanchor="top",
                bgcolor="rgba(11,14,19,0.88)",
                bordercolor="rgba(255,255,255,0.22)",
                font=dict(size=11),
                showactive=True,
            ),
            dict(
                buttons=region_buttons,
                direction="down",
                x=0.998,
                xanchor="right",
                y=0.815,
                yanchor="top",
                bgcolor="rgba(11,14,19,0.88)",
                bordercolor="rgba(255,255,255,0.22)",
                font=dict(size=11),
                showactive=True,
            ),
        ],
        annotations=[
            dict(
                text=(
                    "<b>These are estimates, not meter readings.</b> Per-facility "
                    "consumption is not public for any California data center. Values "
                    "are modelled from air-permit generator<br>counts (Tier B) or "
                    "building footprint (Tier C) — hover any marker for its interval. "
                    "Independent methods disagree by roughly 2×, and "
                    f"{n_sites - n_est} of {n_sites} sites<br>have no estimate at all. "
                    f"White outlines are known building footprints ({n_footprints} of "
                    f"{n_sites}); zoom in to see them.<br>"
                    "Imagery: ESRI World Imagery · Data: OpenStreetMap (ODbL), "
                    "PeeringDB, EPA NEI 2020, California Energy Commission"
                ),
                showarrow=False,
                xref="paper",
                yref="paper",
                x=0.0,
                y=-0.035,
                xanchor="left",
                yanchor="top",
                align="left",
                font=dict(size=10.5, color="#A8B0BD"),
            )
        ],
    )
    return fig


def write_map(output_path=None, facilities: pd.DataFrame | None = None):
    """Build the figure and write a self-contained HTML file."""
    path = output_path or OUTPUT_PATH
    fig = build_figure(facilities)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(
        path,
        include_plotlyjs="cdn",
        full_html=True,
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "toImageButtonOptions": {
                "format": "png",
                "filename": "california_data_centers",
                "scale": 2,
            },
        },
    )
    log.info("wrote %s", path)
    return path
