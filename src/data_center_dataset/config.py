"""Central configuration: paths, projections, contact info, and model priors.

Every tunable assumption in the power model lives here or in ``data/reference``
so that it is auditable and adjustable without touching pipeline logic.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

CONTACT_EMAIL = "aerith.netzer@northwestern.edu"
PROJECT_URL = "https://github.com/aerithnetzer/data_center_dataset"
USER_AGENT = f"data-center-dataset/0.1 ({CONTACT_EMAIL}; +{PROJECT_URL})"

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]

DATA_DIR = Path(os.environ.get("DCD_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
REFERENCE_DIR = DATA_DIR / "reference"


def ensure_dirs() -> None:
    for d in (RAW_DIR, INTERIM_DIR, PROCESSED_DIR, REFERENCE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def raw_dir(source: str, snapshot: str, state: str | None = None) -> Path:
    """Directory for an immutable dated snapshot of one source.

    Most sources vary by state (OSM/PeeringDB/NEI are queried per state), so
    their snapshots live at ``raw/<source>/<STATE>/<snapshot>/``. A few sources
    are not state-scoped (e.g. the shared NEI archive, or California-only
    enrichments queried without a state segment) and pass ``state=None``.
    """
    p = RAW_DIR / source
    if state:
        p = p / state.upper()
    p = p / snapshot
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest_snapshot(source: str, state: str | None = None) -> str | None:
    """Most recent snapshot date available on disk for ``source``."""
    base = RAW_DIR / source
    if state:
        base = base / state.upper()
    if not base.is_dir():
        return None
    snaps = sorted(p.name for p in base.iterdir() if p.is_dir())
    return snaps[-1] if snaps else None


def processed_dir(state: str) -> Path:
    """Per-state output directory, e.g. ``data/processed/TX/``."""
    p = PROCESSED_DIR / state.upper()
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------

#: USA Contiguous Albers Equal Area Conic. Equal-area, metre units, and valid
#: across the full CONUS -- unlike the previous EPSG:3310 (California Albers),
#: which is accurate for California but not the correct choice once other
#: states are in scope. Used for every footprint-area and distance computation.
EQUAL_AREA_CRS = "EPSG:5070"
#: Deprecated alias kept for any external code referencing the old name.
CA_EQUAL_AREA_CRS = EQUAL_AREA_CRS
WGS84 = "EPSG:4326"

#: Generous CONUS bounding box, used only as a loose safety net in the
#: published-table schema. The tight, per-state boundary check that actually
#: rejects mislabelled records lives in ``normalize.classify`` and uses each
#: state's own bbox from :mod:`data_center_dataset.states`.
CONUS_BBOX = (-124.85, 24.40, -66.85, 49.40)
#: Deprecated alias.
CA_BBOX = (-124.55, 32.45, -114.05, 42.05)

# --------------------------------------------------------------------------
# Entity resolution
# --------------------------------------------------------------------------

#: Candidate pairs beyond this separation are never considered the same site.
DEDUPE_RADIUS_M = 1000.0

#: Match scores at or above this are merged automatically.
DEDUPE_AUTO_MERGE = 0.72

#: Scores in [review, auto) are written to a human review queue instead of
#: being merged silently.
DEDUPE_REVIEW_FLOOR = 0.50

# --------------------------------------------------------------------------
# Power model priors
# --------------------------------------------------------------------------

#: Fraction of gross building area that is raised-floor / white space.
WHITE_SPACE_FRACTION = 0.60
WHITE_SPACE_FRACTION_CI = (0.45, 0.72)

#: Storeys credited to a data center by the Tier C model, at most.
#:
#: Purpose-built data centers are one to three storeys. Above that the building
#: is an office or carrier-hotel tower in which the facility is one tenant among
#: many, so multiplying the footprint by every floor measures the *building*, not
#: the facility. Observed failure: One Wilshire in Los Angeles is 30 storeys, and
#: crediting all of them gave its tenant Multacom 1.3 million sqft and 150 MW --
#: roughly five times the entire building's actual critical load, and enough to
#: make it the largest facility in the dataset.
MAX_MODELLED_STOREYS = 3

#: Backup generation is sized above critical load for redundancy. Dividing
#: nameplate by this factor approximates supported critical load.
GENERATOR_REDUNDANCY_FACTOR = 1.30
GENERATOR_REDUNDANCY_CI = (1.10, 2.00)

#: Critical (UPS-protected) load to IT load. Accounts for distribution losses.
CRITICAL_TO_IT = 0.85

#: Mean fraction of contracted/design IT capacity actually drawn.
UTILIZATION = 0.70
UTILIZATION_CI = (0.55, 0.85)

HOURS_PER_YEAR = 8760

#: Deprecated: per-state anchors now live in ``data_center_dataset.states``.
#: Kept only as the default when a caller does not supply one explicitly.
CA_ANNUAL_TWH_ANCHOR = 18.0
CA_ANNUAL_TWH_ANCHOR_RANGE = (12.0, 26.0)

# --------------------------------------------------------------------------
# Power evidence tiers, in precedence order
# --------------------------------------------------------------------------

TIER_ATTESTED = "A_attested"
TIER_GENERATOR = "B_generator"
TIER_AREA = "C_area"

TIER_PRECEDENCE = (TIER_ATTESTED, TIER_GENERATOR, TIER_AREA)
