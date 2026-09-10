"""Per-state configuration.

Everything that varies by state -- bounding box, EPA NEI region file, map
defaults, and the top-down reconciliation anchor -- lives here as a
:class:`StateProfile`, so that adding a state means adding one entry rather
than hunting through the pipeline for hardcoded "California" assumptions.

Two enrichments remain California-only and are flagged as such rather than
silently producing empty columns for other states:

* **CEC utility territories** (``has_utility_layer``). No equivalent free,
  nationwide, per-state utility-territory API was found. Other states get
  ``utility = None`` rather than a guess.
* **CEQAnet** (``supports_ceqanet``). This is the California Environmental
  Quality Act database; it has no equivalent in other states and is not
  offered as an option outside California.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegionView:
    """A named map preset: label, center, zoom."""

    label: str
    lat: float
    lon: float
    zoom: float


@dataclass(frozen=True)
class StateProfile:
    code: str
    name: str
    #: (minx, miny, maxx, maxy) -- generous, not a precise administrative
    #: boundary. Used as a hard geographic gate against source records that
    #: are mislabelled with this state's postal code (a real, observed defect
    #: in PeeringDB: three non-California facilities carried state="CA").
    bbox: tuple[float, float, float, float]
    #: EPA NEI 2020 "facility_process_byregions" archive member covering this
    #: state. One archive is shared across all states; only the member and the
    #: in-file `state` filter change.
    nei_region_file: str
    #: Statewide annual electricity anchor in TWh/yr, for the reconciliation
    #: sanity check ONLY -- never used to overwrite a facility-level estimate.
    #: These are order-of-magnitude heuristics from public data-center market
    #: sizing (Synergy Research / CBRE / JLL market-share reporting on hyperscale
    #: and colocation inventory), NOT a rigorous per-state energy study. The
    #: range is deliberately wide.
    annual_twh_anchor: float
    annual_twh_anchor_range: tuple[float, float]
    #: Map defaults.
    map_center: tuple[float, float]
    map_zoom: float
    region_views: tuple[RegionView, ...] = field(default_factory=tuple)
    #: California-only enrichments (see module docstring).
    has_utility_layer: bool = False
    supports_ceqanet: bool = False


STATES: dict[str, StateProfile] = {
    "CA": StateProfile(
        code="CA",
        name="California",
        bbox=(-124.55, 32.45, -114.05, 42.05),
        nei_region_file="point_9.csv",
        annual_twh_anchor=18.0,
        annual_twh_anchor_range=(12.0, 26.0),
        map_center=(35.75, -119.40),
        map_zoom=6.05,
        region_views=(
            RegionView("All California", 35.75, -119.40, 6.05),
            RegionView("Silicon Valley", 37.38, -121.96, 11.2),
            RegionView("San Francisco", 37.77, -122.40, 12.4),
            RegionView("Los Angeles", 34.05, -118.25, 11.6),
            RegionView("Sacramento", 38.60, -121.45, 10.8),
            RegionView("San Diego", 32.85, -117.13, 11.2),
        ),
        has_utility_layer=True,
        supports_ceqanet=True,
    ),
    "TX": StateProfile(
        code="TX",
        name="Texas",
        bbox=(-106.80, 25.75, -93.50, 36.60),
        nei_region_file="point_6.csv",
        # Dallas-Fort Worth and San Antonio are large, fast-growing colocation
        # and hyperscale markets; Texas ranks among the top few US markets by
        # most industry sizing reports.
        annual_twh_anchor=20.0,
        annual_twh_anchor_range=(12.0, 34.0),
        map_center=(31.4, -99.3),
        map_zoom=5.7,
        region_views=(
            RegionView("All Texas", 31.4, -99.3, 5.7),
            RegionView("Dallas–Fort Worth", 32.80, -96.85, 10.6),
            RegionView("San Antonio", 29.45, -98.50, 11.0),
            RegionView("Austin", 30.30, -97.75, 11.2),
            RegionView("Houston", 29.78, -95.40, 10.8),
        ),
    ),
    "GA": StateProfile(
        code="GA",
        name="Georgia",
        bbox=(-85.70, 30.30, -80.70, 35.10),
        nei_region_file="point_4.csv",
        # Atlanta is a well-established top-tier US colocation/interconnection
        # market (e.g. 56 Marietta St).
        annual_twh_anchor=8.0,
        annual_twh_anchor_range=(4.0, 14.0),
        map_center=(32.75, -83.30),
        map_zoom=6.3,
        region_views=(
            RegionView("All Georgia", 32.75, -83.30, 6.3),
            RegionView("Atlanta", 33.76, -84.40, 10.6),
        ),
    ),
    "AZ": StateProfile(
        code="AZ",
        name="Arizona",
        bbox=(-114.90, 31.30, -109.00, 37.05),
        nei_region_file="point_9.csv",
        # Phoenix is a major and rapidly growing hyperscale market.
        annual_twh_anchor=9.0,
        annual_twh_anchor_range=(4.0, 16.0),
        map_center=(34.30, -111.65),
        map_zoom=6.4,
        region_views=(
            RegionView("All Arizona", 34.30, -111.65, 6.4),
            RegionView("Phoenix", 33.45, -112.05, 10.4),
        ),
    ),
    "IL": StateProfile(
        code="IL",
        name="Illinois",
        bbox=(-91.60, 36.90, -87.00, 42.55),
        nei_region_file="point_5.csv",
        # Chicago (350 E Cermak) is a top-tier US interconnection market.
        annual_twh_anchor=9.0,
        annual_twh_anchor_range=(4.0, 15.0),
        map_center=(40.0, -89.2),
        map_zoom=6.2,
        region_views=(
            RegionView("All Illinois", 40.0, -89.2, 6.2),
            RegionView("Chicago", 41.85, -87.68, 10.6),
        ),
    ),
    "VA": StateProfile(
        code="VA",
        name="Virginia",
        bbox=(-83.80, 36.50, -75.15, 39.55),
        nei_region_file="point_3.csv",
        # Northern Virginia (Loudoun/"Data Center Alley") is widely reported
        # as the single largest concentration of hyperscale/colocation capacity
        # in the world; the anchor is set well above the other states here.
        annual_twh_anchor=30.0,
        annual_twh_anchor_range=(18.0, 48.0),
        map_center=(37.5, -78.6),
        map_zoom=6.4,
        region_views=(
            RegionView("All Virginia", 37.5, -78.6, 6.4),
            RegionView("Northern Virginia", 39.02, -77.45, 10.6),
            RegionView("Richmond", 37.54, -77.44, 10.8),
        ),
    ),
}


def get(state: str) -> StateProfile:
    key = state.strip().upper()
    if key not in STATES:
        raise ValueError(
            f"Unknown state code {state!r}. Available: {', '.join(sorted(STATES))}"
        )
    return STATES[key]
