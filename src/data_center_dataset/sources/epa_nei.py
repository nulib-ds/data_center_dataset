"""EPA National Emissions Inventory ingest (2020, per-state region files).

Why this source matters
-----------------------
Reconnaissance showed NEI is the strongest *free* per-facility signal available
for data centers, on two counts:

1. **Recall.** It contributes commercial sites that neither OSM nor PeeringDB
   records, because every large data center holds an air permit for backup
   diesel generators.

2. **Tier B power proxy.** The count of permitted emergency generators at a
   site scales with the critical load those generators must carry.

The archive is split into ten "byregions" files (EPA Regions 1-10, plus one
for unassigned facilities). One state maps to exactly one region file, and the
mapping was verified empirically against the archive rather than assumed from
the EPA's own region chart:

===== ================================
State  NEI region member
===== ================================
CA     point_9.csv  (Region 9: AZ CA HI NV)
AZ     point_9.csv
HI     point_9.csv
NV     point_9.csv
TX     point_6.csv  (Region 6: AR LA NM OK TX)
OK     point_6.csv
GA     point_4.csv  (Region 4: AL FL GA KY MS NC SC TN)
IL     point_5.csv  (Region 5: IL IN MI MN OH WI)
VA     point_3.csv  (Region 3: DE DC MD PA VA WV)
===== ================================

The full mapping lives in ``states.EPA_REGIONS``; each state's
:class:`~data_center_dataset.states.StateProfile` names the member it reads,
so that adding a state means adding one entry there, not touching this module.

Because the archive is organised by region, the states of a region share a
file. Reading it once for the whole region -- rather than once per state -- is
the difference between ten archive scans and one when every state is built.

Important caveats, measured on California data and assumed to generalise
------------------------------------------------------------------------
* The structured ``design capacity`` field is effectively unusable: only 32 of
  817 California data-center units populate it, and 30 of those report a
  placeholder ``.1 E6BTU/HR``.
* Real nameplate ratings appear instead as free text in ``unit description``
  and ``process description`` ("G1 1500 KW EMERGENCY GENERATOR", "947 BHP").
  These parse for ~7.5% of units, yielding a median of 2116 kW and an
  interquartile range of 885-2190 kW. Those measurements set the per-unit prior
  used when a rating cannot be parsed -- see ``GENERATOR_KW_*`` below. This
  prior was calibrated on California only; it is applied to every state as the
  best available estimate, not because it has been separately validated
  elsewhere.

The upstream archive uses Deflate64, which CPython's ``zipfile`` cannot read,
hence the ``zipfile_deflate64`` import.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import zipfile_deflate64  # noqa: F401  -- registers Deflate64 support

from ..config import RAW_DIR, raw_dir
from ..http import CachedClient
from ..normalize.schema import conform_source_frame

log = logging.getLogger(__name__)

SOURCE = "epa_nei"

ARCHIVE_URL = (
    "https://gaftp.epa.gov/air/nei/2020/data_summaries/"
    "2020nei_facility_process_byregions.zip"
)
ARCHIVE_NAME = "2020nei_facility_process_byregions.zip"

#: NAICS codes that identify data center / computing-infrastructure sites.
DATA_CENTER_NAICS = {"518210", "541513"}

#: Reciprocating internal-combustion engines for electricity generation.
#: 201001xx is distillate (diesel); 202001xx/202004xx are the stationary
#: reciprocating equivalents that also appear in CARB submissions.
GENERATOR_SCC_PREFIXES = ("20100", "20200")

#: Empirical per-generator prior, derived from the 60 California data-center
#: units whose nameplate rating could be parsed from NEI free text.
GENERATOR_KW_MID = 2100.0
GENERATOR_KW_LOW = 885.0
GENERATOR_KW_HIGH = 2190.0

_CAPACITY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(KW|MW|KVA|BHP|HP)\b", re.I)

#: Conversion of a parsed rating to electrical kW. KVA assumes 0.8 power
#: factor; brake horsepower converts at 0.746 kW/hp.
_TO_KW = {"KW": 1.0, "MW": 1000.0, "KVA": 0.8, "BHP": 0.746, "HP": 0.746}


def parse_unit_capacity_kw(text: str | None) -> float | None:
    """Extract an electrical rating in kW from NEI free-text descriptions."""
    if not text:
        return None
    match = _CAPACITY_RE.search(text)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    kw = value * _TO_KW[match.group(2).upper()]
    # Reject implausible ratings: below 50 kW is not a data center generator,
    # above 5 MW is almost certainly a misparse of a serial or permit number.
    return kw if 50.0 <= kw <= 5000.0 else None


# --------------------------------------------------------------------------
# Fetch + filter
# --------------------------------------------------------------------------


def _archive_path() -> "object":
    return RAW_DIR / SOURCE / ARCHIVE_NAME


#: Columns kept from the raw NEI extract. The archive carries far more; these
#: are the ones the pipeline reads.
KEEP_COLS = [
    "eis facility id",
    "company name",
    "site name",
    "primary naics code",
    "site latitude",
    "site longitude",
    "address",
    "city",
    "zip code",
    "eis unit id",
    "unit type",
    "unit description",
    "process description",
    "scc",
]


def _subset_path(state: str, snapshot: str) -> Path:
    return (
        raw_dir(SOURCE, snapshot, state=state)
        / f"nei2020_{state.lower()}_datacenter_units.csv"
    )


def _existing_subset(state: str, snapshot: str) -> Path | None:
    """Most recent per-state extract already on disk, if any.

    The extract is a deterministic function of the (immutable) upstream
    archive, so an extract from an earlier snapshot date is equally valid.
    Reusing it means a rebuild on a new date does not re-scan the archive.
    """
    current = _subset_path(state, snapshot)
    if current.exists():
        return current
    state_dir = RAW_DIR / SOURCE / state.upper()
    if not state_dir.is_dir():
        return None
    candidates = sorted(state_dir.glob("*/nei2020_*_datacenter_units.csv"))
    return candidates[-1] if candidates else None


def _read_subset(path: Path) -> pd.DataFrame:
    """Read a per-state extract.

    ``keep_default_na=False`` matters: an empty NEI field must stay an empty
    string, exactly as it arrived from the archive's CSV. The default turns it
    into NaN, which is truthy, and turns ``(value or "").strip()`` into a
    crash for the 17 states whose extracts have a blank company name.
    """
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _extract_region(
    archive: Path, region_member: str, states: tuple[str, ...], snapshot: str
) -> dict[str, Path]:
    """Scan one region member once and write the extract for each state.

    The archive is organised by EPA region, so the states of a region share a
    file. Reading it once for the whole region -- rather than once per state
    -- is the difference between ten archive scans and one when every state is
    built.
    """
    wanted = {s.upper() for s in states}
    rows: dict[str, list[dict]] = {s: [] for s in wanted}
    seen: set[tuple[str, str]] = set()

    with zipfile.ZipFile(archive) as zf, zf.open(region_member) as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
        for record in csv.DictReader(stream):
            state = (record.get("state") or "").strip().upper()
            if state not in wanted:
                continue
            if (record.get("primary naics code") or "").strip() not in DATA_CENTER_NAICS:
                continue
            key = (record["eis facility id"], record["eis unit id"])
            if key in seen:
                continue
            seen.add(key)
            rows[state].append({c: (record.get(c) or "").strip() for c in KEEP_COLS})

    written: dict[str, Path] = {}
    for state, state_rows in rows.items():
        path = _subset_path(state, snapshot)
        subset = pd.DataFrame(state_rows, columns=KEEP_COLS)
        subset.to_csv(path, index=False)
        written[state] = path
        log.info(
            "nei: %d %s data-center units at %d facilities -> %s",
            len(subset),
            state,
            subset["eis facility id"].nunique() if not subset.empty else 0,
            path.name,
        )
    return written


def fetch(
    client: CachedClient,
    state: str = "CA",
    snapshot: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Download the NEI archive if needed and snapshot the state's subset.

    The 157 MB archive is shared across every state and deliberately *not*
    committed. What is committed is the filtered per-state data-center
    extract produced here, which is small and fully reproducible from
    ``ARCHIVE_URL``.
    """
    from ..states import get as get_state_profile
    from ..states import region_states

    profile = get_state_profile(state)
    region_member = profile.nei_region_file

    snapshot = snapshot or date.today().isoformat()

    if not refresh:
        existing = _existing_subset(state, snapshot)
        if existing is not None:
            return _read_subset(existing)

    archive = _archive_path()
    archive.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading NEI 2020 archive (~157 MB, one time, shared across states)")
    client.download(ARCHIVE_URL, archive, refresh=refresh)

    # Extract the whole region in one pass, so a build covering every state
    # reads each region file once rather than once per state.
    _extract_region(
        archive, region_member, region_states(region_member), snapshot
    )

    subset_path = _subset_path(state, snapshot)
    if not subset_path.exists():
        # The state was not present in its region file at all.
        pd.DataFrame(columns=KEEP_COLS).to_csv(subset_path, index=False)
    return _read_subset(subset_path)


# --------------------------------------------------------------------------
# Shape into source records + generator inventory
# --------------------------------------------------------------------------


def _is_generator(row: pd.Series) -> bool:
    scc = str(row.get("scc") or "")
    if scc.startswith(GENERATOR_SCC_PREFIXES):
        return True
    text = f"{row.get('unit description') or ''} {row.get('process description') or ''}"
    return bool(re.search(r"\b(generator|gen\s?set|ic\s+engine|standby|emergency)\b", text, re.I))


def _text(value: object) -> str:
    """Empty-string-safe text.

    ``pandas.read_csv`` turns an empty field into NaN unless told otherwise,
    and NaN is truthy -- so the obvious ``(value or "").strip()`` returns NaN
    and then explodes on ``.strip()``. NEI records frequently have a blank
    company name, so this guard is load-bearing rather than defensive.
    """
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


def parse(subset: pd.DataFrame, state: str = "CA") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(source_records, generator_inventory)``.

    ``generator_inventory`` is one row per facility and feeds the Tier B
    estimator; it is kept separate because it is evidence, not identity.
    """
    if subset.empty:
        return conform_source_frame(pd.DataFrame()), pd.DataFrame()

    df = subset.copy()
    df["is_generator"] = df.apply(_is_generator, axis=1)
    df["parsed_kw"] = [
        parse_unit_capacity_kw(f"{u} | {p}")
        for u, p in zip(df["unit description"], df["process description"])
    ]

    records: list[dict] = []
    inventory: list[dict] = []

    for eis_id, group in df.groupby("eis facility id", sort=False):
        first = group.iloc[0]
        lat = pd.to_numeric(first["site latitude"], errors="coerce")
        lon = pd.to_numeric(first["site longitude"], errors="coerce")
        if pd.isna(lat) or pd.isna(lon):
            continue

        name = _text(first["site name"]) or _text(first["company name"])
        records.append(
            {
                "source": SOURCE,
                "source_id": str(eis_id),
                "source_url": (
                    "https://enviro.epa.gov/enviro/efsystemquery.frs_program"
                    f"?fac_search=facility_id&fac_value={eis_id}"
                ),
                "name": name or None,
                "operator_raw": _text(first["company name"]) or None,
                "lat": float(lat),
                "lon": float(lon),
                "address": _text(first["address"]) or None,
                "city": _text(first["city"]) or None,
                "state": state.upper(),
                "postcode": _text(first["zip code"]) or None,
                "raw_json": json.dumps(
                    {
                        "eis_facility_id": str(eis_id),
                        "primary_naics": first["primary naics code"],
                        "n_units": int(len(group)),
                    },
                    sort_keys=True,
                ),
            }
        )

        gens = group[group.is_generator]
        parsed = gens["parsed_kw"].dropna()
        inventory.append(
            {
                "source": SOURCE,
                "source_id": str(eis_id),
                "name": name or None,
                "lat": float(lat),
                "lon": float(lon),
                "n_generator_units": int(len(gens)),
                "n_units_total": int(len(group)),
                "n_units_rated": int(len(parsed)),
                "rated_kw_sum": float(parsed.sum()) if len(parsed) else 0.0,
            }
        )

    source_df = conform_source_frame(pd.DataFrame(records))
    inv_df = pd.DataFrame(inventory)
    log.info(
        "nei: %d facilities, %d with >=1 permitted generator",
        len(source_df),
        int((inv_df.n_generator_units > 0).sum()) if not inv_df.empty else 0,
    )
    return source_df, inv_df


def load(client: CachedClient, **kw) -> tuple[pd.DataFrame, pd.DataFrame]:
    subset = fetch(client, **kw)
    return parse(subset, state=kw.get("state", "CA"))
