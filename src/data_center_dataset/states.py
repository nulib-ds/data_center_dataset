"""Per-state configuration.

Everything that varies by state -- bounding box, EPA NEI region file, map
defaults, and the top-down reconciliation anchor -- lives here as a
:class:`StateProfile`, so that adding a state means adding one entry rather
than hunting through the pipeline for hardcoded "California" assumptions.

Coverage
--------
All 50 US states plus the District of Columbia. The nationwide sources
(OpenStreetMap, EPA NEI) are queried per state, so every jurisdiction in
:data:`STATES` is buildable with the same command:

    uv run data-center-dataset build --state OH

PeeringDB is the exception: its ``state`` query parameter is a full-text
search rather than an exact match, and the anonymous API quota does not allow
one query per state, so the whole country is fetched once per snapshot and
split locally (see :mod:`data_center_dataset.sources.peeringdb`). This module
supplies the pieces that split needs -- the state names, the bounding boxes,
and the ZIP-prefix table that places a facility whose ``state`` field is
blank.

Two enrichments remain California-only and are flagged as such rather than
silently producing empty columns for other states:

* **CEC utility territories** (``has_utility_layer``). No equivalent free,
  nationwide, per-state utility-territory API was found. Other states get
  ``utility = None`` rather than a guess.
* **CEQAnet** (``supports_ceqanet``). This is the California Environmental
  Quality Act database; it has no equivalent in other states and is not
  offered as an option outside California.

Bounding boxes
--------------
Each ``bbox`` is the state's own extent padded by ~0.2 degrees, which covers
coastal waters and boundary quirks without admitting a neighbouring state's
cities. It is used as a hard geographic gate against source records that are
mislabelled or that arrived from a mis-scoped query -- a real, observed defect
in PeeringDB, where a ``state=CA`` query returned three North/South Carolina
facilities because the parameter is a full-text search.

Alaska is the one state that needs more than one box. Its Aleutian tail sits
past +172 degrees east, so a single (minx, maxx) pair would have to span from
-168 to +180 -- every longitude on Earth. The tail therefore lives in
``extra_bboxes``, and the geographic gate tests the union of the boxes. A
state's scope is never a single rectangle.

Projections
-----------
Areas and distances are measured in an equal-area projection: the CONUS Albers
(EPSG:5070) for the continental states. Equal-area projections preserve *area*
everywhere, so the footprint-area model is unaffected by the choice; *distance*
is another matter, and distance is what the entity-resolution merge radius
uses. Alaska and Hawaii carry their own projection (see
:attr:`StateProfile.equal_area_crs`).

Reconciliation anchors
----------------------
``annual_twh_anchor`` is a *statewide* electricity anchor used for the
reconciliation sanity check only -- never to overwrite a facility-level
estimate. These are order-of-magnitude heuristics, rounded to one significant
figure, scaled from published state-level data-centre market sizing (the LBNL
*United States Data Center Energy Usage Report*, plus CBRE / JLL / Synergy
Research market commentary). The range is deliberately wide: the honest
uncertainty on a statewide energy figure is a factor of two or more, and a
tight range would manufacture false precision. Every anchor carries an
``anchor_note`` stating its rationale, so a reviewer can argue with the
assumption instead of guessing at it.
"""

from __future__ import annotations

import re
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
    #: are mislabelled or that arrived from a mis-scoped query (a real,
    #: observed defect in PeeringDB, where a ``state=CA`` query returned three
    #: North/South Carolina facilities).
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
    #: Why this anchor is what it is, in one line. Kept with the number so a
    #: reviewer can argue with the assumption instead of guessing at it.
    anchor_note: str = ""
    #: Equal-area projection used for area and distance computation in this
    #: state. ``None`` means the CONUS Albers (EPSG:5070), which is correct
    #: for the continental states. Alaska and Hawaii are far outside that
    #: projection's domain: areas still come out right (an equal-area
    #: projection preserves area everywhere) but *distances* are wrong by up
    #: to 20% in Alaska, which would skew the entity-resolution merge
    #: radius. Those two states therefore carry their own projection.
    equal_area_crs: str | None = None
    #: Additional disjoint boxes that also belong to this state. Needed only
    #: for Alaska, whose Aleutian tail crosses the antimeridian: folding it
    #: into ``bbox`` would make the box span every longitude in the world.
    extra_bboxes: tuple[tuple[float, float, float, float], ...] = ()


def _span(anchor: float) -> tuple[float, float]:
    """Wide plausibility band around an order-of-magnitude anchor.

    0.6x-1.6x the central estimate. Wide on purpose: a statewide data-centre
    energy figure is not known to better than a factor of two, and the anchor
    is a smell test for the bottom-up total, not a measurement.
    """
    return (round(anchor * 0.6, 2), round(anchor * 1.6, 2))


def _s(
    code: str,
    name: str,
    bbox: tuple[float, float, float, float],
    nei_region_file: str,
    anchor: float,
    center: tuple[float, float],
    zoom: float,
    views: tuple[RegionView, ...] = (),
    *,
    span: tuple[float, float] | None = None,
    has_utility_layer: bool = False,
    supports_ceqanet: bool = False,
    anchor_note: str = "",
    equal_area_crs: str | None = None,
    extra_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> StateProfile:
    return StateProfile(
        code=code,
        name=name,
        bbox=bbox,
        extra_bboxes=extra_bboxes,
        nei_region_file=nei_region_file,
        annual_twh_anchor=anchor,
        annual_twh_anchor_range=span or _span(anchor),
        map_center=center,
        map_zoom=zoom,
        region_views=views,
        has_utility_layer=has_utility_layer,
        supports_ceqanet=supports_ceqanet,
        anchor_note=anchor_note,
        equal_area_crs=equal_area_crs,
    )


#: A bounding box: (minx, miny, maxx, maxy) in WGS84 degrees.
Box = tuple[float, float, float, float]


def state_bboxes(profile: StateProfile) -> tuple[Box, ...]:
    """Every box that belongs to a state (the main one plus any satellites)."""
    return (profile.bbox, *profile.extra_bboxes)


def bbox_contains(box: Box, lat: float, lon: float) -> bool:
    """Is (lat, lon) inside ``box``?"""
    minx, miny, maxx, maxy = box
    return miny <= lat <= maxy and minx <= lon <= maxx


#: EPA administrative regions, as the NEI "byregions" archive splits them.
#: The region file is what a state must be read from; the region membership
#: is recorded here so the mapping can be checked against the archive.
EPA_REGIONS = {
    1: ("point_1.csv", ("CT", "MA", "ME", "NH", "RI", "VT")),
    2: ("point_2.csv", ("NJ", "NY")),
    3: ("point_3.csv", ("DC", "DE", "MD", "PA", "VA", "WV")),
    4: ("point_4.csv", ("AL", "FL", "GA", "KY", "MS", "NC", "SC", "TN")),
    5: ("point_5.csv", ("IL", "IN", "MI", "MN", "OH", "WI")),
    6: ("point_6.csv", ("AR", "LA", "NM", "OK", "TX")),
    7: ("point_7.csv", ("IA", "KS", "MO", "NE")),
    8: ("point_8.csv", ("CO", "MT", "ND", "SD", "UT", "WY")),
    9: ("point_9.csv", ("AZ", "CA", "HI", "NV")),
    10: ("point_10.csv", ("AK", "ID", "OR", "WA")),
}

STATES: dict[str, StateProfile] = {
    # -- California --------------------------------------------------------
    "CA": _s(
        "CA",
        "California",
        (-124.55, 32.45, -114.05, 42.05),
        "point_9.csv",
        18.0,
        (35.75, -119.40),
        6.05,
        (
            RegionView("All California", 35.75, -119.40, 6.05),
            RegionView("Silicon Valley", 37.38, -121.96, 11.2),
            RegionView("San Francisco", 37.77, -122.40, 12.4),
            RegionView("Los Angeles", 34.05, -118.25, 11.6),
            RegionView("Sacramento", 38.60, -121.45, 10.8),
            RegionView("San Diego", 32.85, -117.13, 11.2),
        ),
        span=(12.0, 26.0),
        has_utility_layer=True,
        supports_ceqanet=True,
        anchor_note=(
            "Bay Area, Los Angeles and Silicon Valley colocation markets; "
            "among the largest US data-centre states by installed capacity."
        ),
    ),
    # -- Texas -------------------------------------------------------------
    "TX": _s(
        "TX",
        "Texas",
        (-106.80, 25.75, -93.50, 36.60),
        "point_6.csv",
        20.0,
        (31.4, -99.3),
        5.7,
        (
            RegionView("All Texas", 31.4, -99.3, 5.7),
            RegionView("Dallas-Fort Worth", 32.80, -96.85, 10.6),
            RegionView("San Antonio", 29.45, -98.50, 11.0),
            RegionView("Austin", 30.30, -97.75, 11.2),
            RegionView("Houston", 29.78, -95.40, 10.8),
        ),
        span=(12.0, 34.0),
        anchor_note=(
            "Dallas-Fort Worth and San Antonio are large, fast-growing colocation "
            "and hyperscale markets; Texas ranks among the top few US markets by "
            "most industry sizing reports."
        ),
    ),
    # -- Georgia -----------------------------------------------------------
    "GA": _s(
        "GA",
        "Georgia",
        (-85.70, 30.30, -80.70, 35.10),
        "point_4.csv",
        8.0,
        (32.75, -83.30),
        6.3,
        (
            RegionView("All Georgia", 32.75, -83.30, 6.3),
            RegionView("Atlanta", 33.76, -84.40, 10.6),
        ),
        span=(4.0, 14.0),
        anchor_note=(
            "Atlanta is a well-established top-tier US colocation/interconnection "
            "market (e.g. 56 Marietta St)."
        ),
    ),
    # -- Arizona -----------------------------------------------------------
    "AZ": _s(
        "AZ",
        "Arizona",
        (-114.90, 31.30, -109.00, 37.05),
        "point_9.csv",
        9.0,
        (34.30, -111.65),
        6.4,
        (
            RegionView("All Arizona", 34.30, -111.65, 6.4),
            RegionView("Phoenix", 33.45, -112.05, 10.4),
        ),
        span=(4.0, 16.0),
        anchor_note="Phoenix is a major and rapidly growing hyperscale market.",
    ),
    # -- Illinois ----------------------------------------------------------
    "IL": _s(
        "IL",
        "Illinois",
        (-91.60, 36.90, -87.00, 42.55),
        "point_5.csv",
        9.0,
        (40.0, -89.2),
        6.2,
        (
            RegionView("All Illinois", 40.0, -89.2, 6.2),
            RegionView("Chicago", 41.85, -87.68, 10.6),
        ),
        span=(4.0, 15.0),
        anchor_note="Chicago (350 E Cermak) is a top-tier US interconnection market.",
    ),
    # -- Virginia ----------------------------------------------------------
    "VA": _s(
        "VA",
        "Virginia",
        (-83.80, 36.50, -75.15, 39.55),
        "point_3.csv",
        30.0,
        (37.5, -78.6),
        6.4,
        (
            RegionView("All Virginia", 37.5, -78.6, 6.4),
            RegionView("Northern Virginia", 39.02, -77.45, 10.6),
            RegionView("Richmond", 37.54, -77.44, 10.8),
        ),
        span=(18.0, 48.0),
        anchor_note=(
            "Northern Virginia (Loudoun/'Data Center Alley') is widely reported "
            "as the single largest concentration of hyperscale/colocation capacity "
            "in the world; the anchor is set well above the other states here."
        ),
    ),
    # -- Alabama -----------------------------------------------------------
    "AL": _s(
        "AL",
        "Alabama",
        (-88.67, 30.02, -84.69, 35.21),
        "point_4.csv",
        1.2,
        (32.80, -86.80),
        7.9,
        (
            RegionView("All Alabama", 32.80, -86.80, 7.9),
            RegionView("Birmingham", 33.52, -86.80, 10.6),
            RegionView("Huntsville", 34.73, -86.59, 10.6),
        ),
        anchor_note="Birmingham/Huntsville colocation market; no hyperscale megasites.",
    ),
    # -- Alaska ------------------------------------------------------------
    # Alaska straddles the antimeridian: the mainland and the Alaska
    # Peninsula run from Cape Prince of Wales (-168.1) eastwards, while the
    # far-western Aleutians (Attu, Kiska, Semisopochnoi) sit past +172 E.
    # A single (minx, maxx) box spanning that range would cover every
    # longitude on Earth -- and therefore London -- so the Aleutian tail is
    # carried as a second box.
    "AK": _s(
        "AK",
        "Alaska",
        (-167.88, 51.01, -129.79, 71.64),
        "point_10.csv",
        0.6,
        (64.20, -152.40),
        3.6,
        (
            RegionView("All Alaska", 64.20, -152.40, 3.6),
            RegionView("Anchorage", 61.22, -149.90, 10.0),
        ),
        anchor_note="One metro-scale market (Anchorage); capacity is small.",
        equal_area_crs="EPSG:3338",  # NAD83 / Alaska Albers
        extra_bboxes=((172.77, 51.10, 180.00, 53.90),),  # far-west Aleutians
    ),
    # -- Arkansas ----------------------------------------------------------
    "AR": _s(
        "AR",
        "Arkansas",
        (-94.82, 32.80, -89.45, 36.70),
        "point_6.csv",
        0.6,
        (34.90, -92.40),
        8.3,
        (
            RegionView("All Arkansas", 34.90, -92.40, 8.3),
            RegionView("Little Rock", 34.75, -92.29, 10.6),
        ),
        anchor_note="Small colocation market centred on Little Rock.",
    ),
    # -- Colorado ----------------------------------------------------------
    "CO": _s(
        "CO",
        "Colorado",
        (-109.26, 36.79, -101.84, 41.20),
        "point_8.csv",
        3.5,
        (39.00, -105.50),
        7.8,
        (
            RegionView("All Colorado", 39.00, -105.50, 7.8),
            RegionView("Denver", 39.74, -104.99, 10.2),
            RegionView("Colorado Springs", 38.83, -104.82, 10.6),
        ),
        anchor_note=(
            "Denver metro is a growing hyperscale and colocation market; "
            "Colorado ranks well above its population share in capacity."
        ),
    ),
    # -- Connecticut -------------------------------------------------------
    "CT": _s(
        "CT",
        "Connecticut",
        (-73.93, 40.78, -71.59, 42.25),
        "point_1.csv",
        0.9,
        (41.60, -72.70),
        9.1,
        (
            RegionView("All Connecticut", 41.60, -72.70, 9.1),
            RegionView("Hartford", 41.76, -72.68, 10.6),
            RegionView("Stamford", 41.05, -73.54, 11.0),
        ),
        anchor_note="Small but dense Northeast market; mostly financial-sector colo.",
    ),
    # -- Delaware ----------------------------------------------------------
    "DE": _s(
        "DE",
        "Delaware",
        (-75.99, 38.25, -74.85, 40.04),
        "point_3.csv",
        0.8,
        (39.00, -75.50),
        9.1,
        (
            RegionView("All Delaware", 39.00, -75.50, 9.1),
            RegionView("Wilmington", 39.68, -75.75, 10.8),
        ),
        anchor_note="Northern Delaware and the New Castle industrial corridor.",
    ),
    # -- District of Columbia ---------------------------------------------
    "DC": _s(
        "DC",
        "District of Columbia",
        (-77.32, 38.59, -76.71, 39.20),
        "point_3.csv",
        0.9,
        (38.90, -77.04),
        11.0,
        (
            RegionView("All District of Columbia", 38.90, -77.04, 11.0),
            RegionView("Northern Virginia edge", 38.95, -77.30, 11.5),
        ),
        anchor_note=(
            "One metro-scale market; a handful of carrier hotels and enterprise "
            "facilities rather than hyperscale campuses."
        ),
    ),
    # -- Florida -----------------------------------------------------------
    "FL": _s(
        "FL",
        "Florida",
        (-87.83, 24.32, -79.83, 31.21),
        "point_4.csv",
        6.0,
        (28.60, -82.40),
        7.5,
        (
            RegionView("All Florida", 28.60, -82.40, 7.5),
            RegionView("Miami", 25.76, -80.19, 10.2),
            RegionView("Orlando", 28.54, -81.38, 10.4),
            RegionView("Tampa", 27.95, -82.46, 10.4),
            RegionView("Jacksonville", 30.33, -81.66, 10.4),
        ),
        anchor_note=(
            "South Florida, Tampa and Orlando; large enterprise and colocation "
            "footprint spread over a big, fast-growing state."
        ),
    ),
    # -- Hawaii ------------------------------------------------------------
    "HI": _s(
        "HI",
        "Hawaii",
        (-160.44, 18.71, -154.61, 22.43),
        "point_9.csv",
        0.7,
        (20.80, -157.50),
        8.2,
        (
            RegionView("All Hawaii", 20.80, -157.50, 8.2),
            RegionView("Honolulu", 21.31, -157.86, 11.0),
        ),
        anchor_note="Single metro market (Honolulu); modest capacity.",
        equal_area_crs=(
            "+proj=laea +lat_0=20.5 +lon_0=-157.5 +datum=NAD83 +units=m +no_defs"
        ),
    ),
    # -- Idaho -------------------------------------------------------------
    "ID": _s(
        "ID",
        "Idaho",
        (-117.44, 41.79, -110.84, 49.20),
        "point_10.csv",
        0.7,
        (44.20, -114.50),
        7.4,
        (
            RegionView("All Idaho", 44.20, -114.50, 7.4),
            RegionView("Boise", 43.62, -116.20, 10.6),
        ),
        anchor_note="Small market centred on the Boise metro.",
    ),
    # -- Indiana -----------------------------------------------------------
    "IN": _s(
        "IN",
        "Indiana",
        (-88.30, 37.57, -84.58, 41.96),
        "point_5.csv",
        2.0,
        (39.90, -86.30),
        8.1,
        (
            RegionView("All Indiana", 39.90, -86.30, 8.1),
            RegionView("Indianapolis", 39.77, -86.16, 10.6),
        ),
        anchor_note="Indianapolis and northern Indiana colocation markets.",
    ),
    # -- Iowa --------------------------------------------------------------
    "IA": _s(
        "IA",
        "Iowa",
        (-96.84, 40.18, -89.94, 43.70),
        "point_7.csv",
        1.2,
        (42.10, -93.50),
        7.9,
        (
            RegionView("All Iowa", 42.10, -93.50, 7.9),
            RegionView("Des Moines", 41.59, -93.62, 10.6),
        ),
        anchor_note="Des Moines-area colocation; wind-powered capacity projects.",
    ),
    # -- Kansas -----------------------------------------------------------
    "KS": _s(
        "KS",
        "Kansas",
        (-102.25, 36.79, -94.39, 40.20),
        "point_7.csv",
        1.0,
        (38.50, -98.40),
        7.7,
        (
            RegionView("All Kansas", 38.50, -98.40, 7.7),
            RegionView("Wichita", 37.69, -97.34, 10.6),
            RegionView("Kansas City (KS)", 39.11, -94.63, 11.0),
        ),
        anchor_note="Wichita and Kansas City edge; mostly small colocation sites.",
    ),
    # -- Kentucky ----------------------------------------------------------
    "KY": _s(
        "KY",
        "Kentucky",
        (-89.77, 36.30, -81.76, 39.35),
        "point_4.csv",
        0.8,
        (37.50, -85.30),
        7.7,
        (
            RegionView("All Kentucky", 37.50, -85.30, 7.7),
            RegionView("Louisville", 38.25, -85.76, 10.6),
            RegionView("Lexington", 38.04, -84.50, 10.8),
        ),
        anchor_note="Louisville and Lexington; small-scale colocation market.",
    ),
    # -- Louisiana ---------------------------------------------------------
    "LA": _s(
        "LA",
        "Louisiana",
        (-94.24, 28.73, -88.56, 33.22),
        "point_6.csv",
        1.2,
        (31.00, -92.00),
        8.1,
        (
            RegionView("All Louisiana", 31.00, -92.00, 8.1),
            RegionView("New Orleans", 29.95, -90.07, 10.6),
            RegionView("Baton Rouge", 30.45, -91.19, 10.8),
        ),
        anchor_note="New Orleans and Baton Rouge; small regional colocation market.",
    ),
    # -- Maine -------------------------------------------------------------
    "ME": _s(
        "ME",
        "Maine",
        (-71.28, 42.78, -66.75, 47.66),
        "point_1.csv",
        0.4,
        (45.30, -69.20),
        7.9,
        (
            RegionView("All Maine", 45.30, -69.20, 7.9),
            RegionView("Portland", 43.66, -70.26, 10.8),
        ),
        anchor_note="Portland and Bangor; minimal commercial data-centre presence.",
    ),
    # -- Maryland ----------------------------------------------------------
    "MD": _s(
        "MD",
        "Maryland",
        (-79.69, 37.71, -74.85, 39.92),
        "point_3.csv",
        2.5,
        (39.00, -76.70),
        8.4,
        (
            RegionView("All Maryland", 39.00, -76.70, 8.4),
            RegionView("Baltimore", 39.29, -76.61, 10.4),
            RegionView("Hunt Valley", 39.48, -76.63, 11.0),
        ),
        anchor_note=(
            "Baltimore/Hunt Valley plus the Maryland edge of the Northern "
            "Virginia cluster, which is the largest capacity market in the US."
        ),
    ),
    # -- Massachusetts -----------------------------------------------------
    "MA": _s(
        "MA",
        "Massachusetts",
        (-73.71, 41.04, -69.73, 43.09),
        "point_1.csv",
        4.0,
        (42.30, -71.80),
        8.7,
        (
            RegionView("All Massachusetts", 42.30, -71.80, 8.7),
            RegionView("Boston", 42.36, -71.06, 10.6),
            RegionView("Route 128 (Wilmington)", 42.60, -71.38, 10.8),
        ),
        anchor_note=(
            "Boston and the Route 128 corridor: a long-established financial and "
            "enterprise colocation market."
        ),
    ),
    # -- Michigan ----------------------------------------------------------
    "MI": _s(
        "MI",
        "Michigan",
        (-90.62, 41.50, -82.21, 48.51),
        "point_5.csv",
        2.0,
        (44.30, -85.60),
        7.2,
        (
            RegionView("All Michigan", 44.30, -85.60, 7.2),
            RegionView("Detroit", 42.33, -83.05, 10.4),
            RegionView("Grand Rapids", 42.96, -85.67, 10.6),
        ),
        anchor_note="Detroit and west Michigan; mostly enterprise and edge sites.",
    ),
    # -- Minnesota ---------------------------------------------------------
    "MN": _s(
        "MN",
        "Minnesota",
        (-97.44, 43.30, -89.29, 49.58),
        "point_5.csv",
        2.5,
        (46.30, -94.30),
        7.6,
        (
            RegionView("All Minnesota", 46.30, -94.30, 7.6),
            RegionView("Minneapolis", 44.98, -93.27, 10.4),
            RegionView("Mankato", 44.16, -94.00, 10.8),
        ),
        anchor_note=(
            "Twin Cities and the Mankato/St. Cloud corridor, which hosts a "
            "notable concentration of hyperscale campuses."
        ),
    ),
    # -- Mississippi -------------------------------------------------------
    "MS": _s(
        "MS",
        "Mississippi",
        (-91.86, 29.97, -87.90, 35.20),
        "point_4.csv",
        0.5,
        (32.70, -89.70),
        7.8,
        (
            RegionView("All Mississippi", 32.70, -89.70, 7.8),
            RegionView("Jackson", 32.30, -90.18, 10.6),
        ),
        anchor_note="Minimal commercial data-centre presence.",
    ),
    # -- Missouri ----------------------------------------------------------
    "MO": _s(
        "MO",
        "Missouri",
        (-95.97, 35.80, -88.90, 40.81),
        "point_7.csv",
        1.5,
        (38.50, -92.50),
        7.9,
        (
            RegionView("All Missouri", 38.50, -92.50, 7.9),
            RegionView("St. Louis", 38.63, -90.20, 10.4),
            RegionView("Kansas City", 39.10, -94.58, 10.4),
        ),
        anchor_note="St. Louis and Kansas City colocation markets.",
    ),
    # -- Montana -----------------------------------------------------------
    "MT": _s(
        "MT",
        "Montana",
        (-116.25, 44.16, -103.84, 49.20),
        "point_8.csv",
        0.5,
        (47.00, -109.60),
        7.1,
        (
            RegionView("All Montana", 47.00, -109.60, 7.1),
            RegionView("Billings", 45.78, -108.50, 10.6),
        ),
        anchor_note="Minimal commercial data-centre presence outside Billings.",
    ),
    # -- Nebraska ----------------------------------------------------------
    "NE": _s(
        "NE",
        "Nebraska",
        (-104.25, 39.79, -95.11, 43.20),
        "point_7.csv",
        1.0,
        (41.50, -99.80),
        7.5,
        (
            RegionView("All Nebraska", 41.50, -99.80, 7.5),
            RegionView("Omaha", 41.26, -95.94, 10.4),
            RegionView("Lincoln", 40.81, -96.68, 11.0),
        ),
        anchor_note="Omaha and Lincoln; small colocation market.",
    ),
    # -- Nevada ------------------------------------------------------------
    "NV": _s(
        "NV",
        "Nevada",
        (-120.21, 34.80, -113.84, 42.20),
        "point_9.csv",
        1.8,
        (39.30, -116.60),
        7.3,
        (
            RegionView("All Nevada", 39.30, -116.60, 7.3),
            RegionView("Las Vegas", 36.17, -115.14, 10.2),
            RegionView("Reno", 39.53, -119.81, 10.4),
        ),
        anchor_note="Las Vegas and Reno; a smaller market than its profile suggests.",
    ),
    # -- New Hampshire -----------------------------------------------------
    "NH": _s(
        "NH",
        "New Hampshire",
        (-72.76, 42.50, -70.41, 45.51),
        "point_1.csv",
        0.8,
        (43.70, -71.60),
        8.6,
        (
            RegionView("All New Hampshire", 43.70, -71.60, 8.6),
            RegionView("Manchester", 42.99, -71.46, 10.8),
        ),
        anchor_note="Manchester and Nashua; modest enterprise and colo market.",
    ),
    # -- New Jersey --------------------------------------------------------
    "NJ": _s(
        "NJ",
        "New Jersey",
        (-75.76, 38.73, -73.69, 41.56),
        "point_2.csv",
        4.0,
        (40.10, -74.50),
        8.7,
        (
            RegionView("All New Jersey", 40.10, -74.50, 8.7),
            RegionView("Newark", 40.74, -74.17, 10.4),
            RegionView("Secaucus", 40.79, -74.03, 11.0),
        ),
        anchor_note=(
            "Northern New Jersey sits inside the New York metro, one of the "
            "densest interconnection markets in the country."
        ),
    ),
    # -- New Mexico --------------------------------------------------------
    "NM": _s(
        "NM",
        "New Mexico",
        (-109.25, 31.13, -102.80, 37.20),
        "point_6.csv",
        0.8,
        (34.40, -106.10),
        7.6,
        (
            RegionView("All New Mexico", 34.40, -106.10, 7.6),
            RegionView("Albuquerque", 35.08, -106.65, 10.6),
        ),
        anchor_note="Albuquerque is the state's only material market.",
    ),
    # -- New York ----------------------------------------------------------
    "NY": _s(
        "NY",
        "New York",
        (-79.96, 40.30, -71.66, 45.22),
        "point_2.csv",
        7.0,
        (43.00, -75.00),
        7.7,
        (
            RegionView("All New York", 43.00, -75.00, 7.7),
            RegionView("New York City", 40.71, -74.01, 10.2),
            RegionView("Buffalo", 42.89, -78.88, 10.6),
        ),
        anchor_note=(
            "New York City metro is a top-tier financial interconnection market; "
            "upstate adds enterprise and some colocation capacity."
        ),
    ),
    # -- North Carolina ----------------------------------------------------
    "NC": _s(
        "NC",
        "North Carolina",
        (-84.52, 33.64, -75.26, 36.79),
        "point_4.csv",
        4.0,
        (35.50, -79.40),
        7.5,
        (
            RegionView("All North Carolina", 35.50, -79.40, 7.5),
            RegionView("Charlotte", 35.23, -80.84, 10.2),
            RegionView("Raleigh-Durham", 35.78, -78.64, 10.2),
        ),
        anchor_note=(
            "Charlotte is a large banking-colocation market; the Triangle hosts "
            "hyperscale and university research campuses."
        ),
    ),
    # -- North Dakota ------------------------------------------------------
    "ND": _s(
        "ND",
        "North Dakota",
        (-104.25, 45.74, -96.35, 49.20),
        "point_8.csv",
        0.4,
        (47.50, -100.40),
        7.7,
        (
            RegionView("All North Dakota", 47.50, -100.40, 7.7),
            RegionView("Fargo", 46.88, -96.79, 10.8),
        ),
        anchor_note="Minimal commercial data-centre presence outside Fargo.",
    ),
    # -- Ohio --------------------------------------------------------------
    "OH": _s(
        "OH",
        "Ohio",
        (-85.02, 38.20, -80.32, 42.52),
        "point_5.csv",
        7.0,
        (40.30, -82.80),
        8.1,
        (
            RegionView("All Ohio", 40.30, -82.80, 8.1),
            RegionView("Columbus", 39.96, -83.00, 10.2),
            RegionView("Cleveland", 41.50, -81.69, 10.4),
            RegionView("Cincinnati", 39.10, -84.51, 10.4),
        ),
        anchor_note=(
            "Columbus is a large hyperscale and colocation market; Ohio hosts "
            "several of the biggest cloud campuses in the Midwest."
        ),
    ),
    # -- Oklahoma ----------------------------------------------------------
    "OK": _s(
        "OK",
        "Oklahoma",
        (-103.20, 33.42, -94.23, 37.20),
        "point_6.csv",
        1.2,
        (35.60, -97.50),
        7.6,
        (
            RegionView("All Oklahoma", 35.60, -97.50, 7.6),
            RegionView("Oklahoma City", 35.47, -97.52, 10.6),
            RegionView("Tulsa", 36.15, -95.99, 10.6),
        ),
        anchor_note="Oklahoma City and Tulsa; a small but steady colocation market.",
    ),
    # -- Oregon ------------------------------------------------------------
    "OR": _s(
        "OR",
        "Oregon",
        (-124.77, 41.79, -116.26, 46.50),
        "point_10.csv",
        1.2,
        (44.00, -120.60),
        7.6,
        (
            RegionView("All Oregon", 44.00, -120.60, 7.6),
            RegionView("Portland", 45.52, -122.68, 10.2),
            RegionView("The Dalles", 45.60, -121.18, 10.8),
        ),
        anchor_note="Portland and the Columbia River hyperscale campuses.",
    ),
    # -- Pennsylvania ------------------------------------------------------
    "PA": _s(
        "PA",
        "Pennsylvania",
        (-80.72, 39.52, -74.49, 42.47),
        "point_3.csv",
        3.5,
        (40.90, -77.80),
        8.1,
        (
            RegionView("All Pennsylvania", 40.90, -77.80, 8.1),
            RegionView("Philadelphia", 39.95, -75.17, 10.2),
            RegionView("Pittsburgh", 40.44, -80.00, 10.4),
        ),
        anchor_note=(
            "Philadelphia and Pittsburgh; the Lehigh Valley hosts a growing "
            "colocation cluster."
        ),
    ),
    # -- Rhode Island ------------------------------------------------------
    "RI": _s(
        "RI",
        "Rhode Island",
        (-72.11, 40.95, -70.92, 42.22),
        "point_1.csv",
        0.4,
        (41.70, -71.50),
        9.6,
        (
            RegionView("All Rhode Island", 41.70, -71.50, 9.6),
            RegionView("Providence", 41.82, -71.41, 11.0),
        ),
        anchor_note="Small market; part of the Providence/New England cluster.",
    ),
    # -- South Carolina ----------------------------------------------------
    "SC": _s(
        "SC",
        "South Carolina",
        (-83.55, 31.83, -78.34, 35.42),
        "point_4.csv",
        1.8,
        (33.90, -80.90),
        8.3,
        (
            RegionView("All South Carolina", 33.90, -80.90, 8.3),
            RegionView("Charleston", 32.78, -79.93, 10.4),
            RegionView("Columbia", 34.00, -81.03, 10.8),
        ),
        anchor_note="Charleston and Columbia; a mid-sized regional colocation market.",
    ),
    # -- South Dakota ------------------------------------------------------
    "SD": _s(
        "SD",
        "South Dakota",
        (-104.26, 42.28, -96.24, 46.15),
        "point_8.csv",
        0.4,
        (44.40, -100.20),
        7.7,
        (
            RegionView("All South Dakota", 44.40, -100.20, 7.7),
            RegionView("Sioux Falls", 43.55, -96.70, 10.8),
        ),
        anchor_note="Minimal commercial data-centre presence outside Sioux Falls.",
    ),
    # -- Tennessee ---------------------------------------------------------
    "TN": _s(
        "TN",
        "Tennessee",
        (-90.51, 34.78, -81.45, 36.88),
        "point_4.csv",
        2.0,
        (35.80, -86.30),
        7.5,
        (
            RegionView("All Tennessee", 35.80, -86.30, 7.5),
            RegionView("Nashville", 36.16, -86.78, 10.2),
            RegionView("Memphis", 35.15, -90.05, 10.4),
        ),
        anchor_note="Memphis and Nashville; several large cloud campuses in the region.",
    ),
    # -- Utah --------------------------------------------------------------
    "UT": _s(
        "UT",
        "Utah",
        (-114.25, 36.80, -108.84, 42.20),
        "point_8.csv",
        2.5,
        (39.30, -111.70),
        7.8,
        (
            RegionView("All Utah", 39.30, -111.70, 7.8),
            RegionView("Salt Lake City", 40.76, -111.89, 10.4),
        ),
        anchor_note=(
            "Wasatch Front; one of the fastest-growing US data-centre markets "
            "outside Texas and the Mid-Atlantic."
        ),
    ),
    # -- Vermont -----------------------------------------------------------
    "VT": _s(
        "VT",
        "Vermont",
        (-73.64, 42.53, -71.27, 45.22),
        "point_1.csv",
        0.3,
        (44.10, -72.70),
        8.8,
        (
            RegionView("All Vermont", 44.10, -72.70, 8.8),
            RegionView("Burlington", 44.48, -73.21, 11.0),
        ),
        anchor_note="Minimal commercial data-centre presence.",
    ),
    # -- Washington --------------------------------------------------------
    "WA": _s(
        "WA",
        "Washington",
        (-125.05, 45.34, -116.72, 49.20),
        "point_10.csv",
        5.0,
        (47.40, -120.70),
        7.7,
        (
            RegionView("All Washington", 47.40, -120.70, 7.7),
            RegionView("Seattle", 47.61, -122.33, 10.2),
            RegionView("Quincy", 47.23, -119.85, 10.4),
        ),
        anchor_note=(
            "Seattle and the Quincy/central-Washington hyperscale campuses; "
            "Washington has one of the largest data-centre footprints per capita."
        ),
    ),
    # -- West Virginia -----------------------------------------------------
    "WV": _s(
        "WV",
        "West Virginia",
        (-82.84, 37.00, -77.52, 40.84),
        "point_3.csv",
        0.6,
        (38.60, -80.60),
        8.3,
        (
            RegionView("All West Virginia", 38.60, -80.60, 8.3),
            RegionView("Charles Town / Harpers Ferry", 39.29, -77.86, 10.6),
            RegionView("Charleston", 38.35, -81.63, 10.8),
        ),
        anchor_note=(
            "The eastern panhandle sits inside the Northern Virginia cluster and "
            "is home to a rapidly growing hyperscale campus."
        ),
    ),
    # -- Wisconsin ---------------------------------------------------------
    "WI": _s(
        "WI",
        "Wisconsin",
        (-93.09, 42.29, -86.05, 47.51),
        "point_5.csv",
        2.5,
        (44.50, -89.70),
        7.8,
        (
            RegionView("All Wisconsin", 44.50, -89.70, 7.8),
            RegionView("Madison", 43.07, -89.40, 10.6),
            RegionView("Mount Pleasant (Racine County)", 42.58, -87.00, 10.6),
        ),
        anchor_note=(
            "Racine County's 'Data Center Alley' hosts one of the largest "
            "hyperscale campuses in the US."
        ),
    ),
    # -- Wyoming -----------------------------------------------------------
    "WY": _s(
        "WY",
        "Wyoming",
        (-111.26, 40.80, -103.85, 45.21),
        "point_8.csv",
        0.4,
        (43.00, -107.50),
        7.8,
        (
            RegionView("All Wyoming", 43.00, -107.50, 7.8),
            RegionView("Cheyenne", 41.14, -104.82, 10.6),
        ),
        anchor_note="Minimal commercial data-centre presence.",
    ),
}


def region_states(region_file: str) -> tuple[str, ...]:
    """State codes whose NEI data lives in ``region_file``."""
    for _num, (member, codes) in EPA_REGIONS.items():
        if member == region_file:
            return codes
    raise ValueError(f"Unknown NEI region member {region_file!r}")


# --------------------------------------------------------------------------
# ZIP code -> state
# --------------------------------------------------------------------------

#: USPS 3-digit ZIP prefix allocation, as (low, high, state) inclusive ranges.
#:
#: The USPS assigns each 3-digit prefix to exactly one state (the handful of
#: military/territory prefixes aside), so the first three digits of a ZIP code
#: identify its state. This is the standard USPS prefix chart; it is static
#: reference data, not an estimate.
#:
#: It exists because some source records carry no usable state field. PeeringDB
#: has nine such records in the 2026-10-21 snapshot, and the ZIP code resolves
#: all nine exactly -- which the generous per-state bounding boxes cannot
#: always do, since a point on a state border falls inside two of them.
ZIP3_RANGES: tuple[tuple[int, int, str], ...] = (
    (0, 5, "AP"),        # armed forces -- outside the 50 states + DC
    (6, 7, "PR"),        # Puerto Rico
    (8, 8, "VI"),        # US Virgin Islands
    (9, 9, "PR"),
    (10, 27, "MA"),
    (28, 29, "RI"),
    (30, 38, "NH"),
    (39, 49, "ME"),
    (50, 59, "VT"),
    (60, 69, "CT"),
    (70, 89, "NJ"),
    (90, 98, "AE"),      # armed forces
    (99, 99, "AA"),      # armed forces
    (100, 149, "NY"),
    (150, 196, "PA"),
    (197, 199, "DE"),
    (200, 205, "DC"),
    (206, 219, "MD"),
    (220, 246, "VA"),
    (247, 268, "WV"),
    (270, 289, "NC"),
    (290, 299, "SC"),
    (300, 319, "GA"),
    (320, 349, "FL"),
    (350, 369, "AL"),
    (370, 385, "TN"),
    (386, 397, "MS"),
    (398, 399, "GA"),
    (400, 427, "KY"),
    (430, 459, "OH"),
    (460, 479, "IN"),
    (480, 499, "MI"),
    (500, 528, "IA"),
    (530, 549, "WI"),
    (550, 567, "MN"),
    (570, 577, "SD"),
    (580, 588, "ND"),
    (590, 599, "MT"),
    (600, 629, "IL"),
    (630, 658, "MO"),
    (660, 679, "KS"),
    (680, 693, "NE"),
    (700, 714, "LA"),
    (716, 729, "AR"),
    (730, 749, "OK"),
    (750, 799, "TX"),
    (800, 816, "CO"),
    (820, 831, "WY"),
    (832, 838, "ID"),
    (840, 847, "UT"),
    (850, 865, "AZ"),
    (870, 884, "NM"),
    (890, 898, "NV"),
    (900, 961, "CA"),
    (967, 968, "HI"),
    (969, 969, "GU"),    # Guam / Northern Mariana Islands
    (970, 979, "OR"),
    (980, 994, "WA"),
    (995, 999, "AK"),
)


#: A US ZIP code, optionally carrying a state in front ("FL 33487") and
#: optionally a ZIP+4 suffix. Anything else -- a foreign postal code, a
#: partial code -- is not a US ZIP and is not guessed at.
_ZIP_US = re.compile(r"^(?:[A-Z]{2}\s+)?(\d{5})(?:-(\d{4}))?$")


def zip3_state(zipcode: object) -> str | None:
    """State code implied by a ZIP code, or ``None`` if it cannot be resolved.

    Only the first three digits are used, which is the granularity at which the
    USPS assigns prefixes to states. The code must look like a US ZIP: five
    digits, optionally with a state in front and a ZIP+4 suffix. A foreign
    postal code is rejected rather than read as a US one -- PeeringDB holds a
    Tokyo facility whose "zipcode" is the Japanese ``135-0061``, whose first
    three digits happen to fall in a New York range.

    Returns a code outside :data:`STATES` (Puerto Rico, a military ZIP, and so
    on) as well -- the caller decides what is in scope.
    """
    text = str(zipcode or "").strip().upper()
    if not text:
        return None
    match = _ZIP_US.match(text)
    if not match:
        return None
    prefix = int(match.group(1)[:3])
    for low, high, code in ZIP3_RANGES:
        if low <= prefix <= high:
            return code
    return None


def get(state: str) -> StateProfile:
    key = state.strip().upper()
    if key not in STATES:
        raise ValueError(
            f"Unknown state code {state!r}. Available: {', '.join(sorted(STATES))}"
        )
    return STATES[key]


def state_name_to_code(name: object) -> str | None:
    """Postal code for a full state name, case-insensitively.

    Source records sometimes spell the state out instead of using the postal
    code -- PeeringDB has seven such records in the 2026-10-21 snapshot
    ("South Carolina", "New York", "Wisconsin", "Tennessee", "Colorado",
    "North Carolina"). Returns ``None`` when the name is not one of the 51
    jurisdictions.
    """
    text = str(name or "").strip().lower()
    if not text:
        return None
    for code, profile in STATES.items():
        if profile.name.lower() == text:
            return code
    return None
