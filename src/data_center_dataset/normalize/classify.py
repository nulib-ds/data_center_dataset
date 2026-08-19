"""Classification and scope filtering.

The project scope is *commercial* data centers: colocation, wholesale, and
hyperscale. OpenStreetMap's ``telecom=data_center`` tag is applied far more
loosely than that -- reconnaissance on the live data turned up a university
computer room, a student-run campus computing facility, a post office annex and
a community college district office alongside genuine Equinix and Digital
Realty sites.

Rather than silently dropping records, every exclusion is recorded with the
rule that fired, so the filter itself can be audited and argued with.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

import pandas as pd

from ..config import CA_BBOX, REFERENCE_DIR

log = logging.getLogger(__name__)

CLASS_COLOCATION = "colocation"
CLASS_WHOLESALE = "wholesale"
CLASS_HYPERSCALE = "hyperscale"
CLASS_TELECOM = "telecom"
CLASS_UNKNOWN = "unknown"

IN_SCOPE_CLASSES = {
    CLASS_COLOCATION,
    CLASS_WHOLESALE,
    CLASS_HYPERSCALE,
    CLASS_UNKNOWN,
}

# --------------------------------------------------------------------------
# Exclusion rules. Ordered; the first match wins and is reported.
# --------------------------------------------------------------------------

_EXCLUSION_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "education_facility",
        re.compile(
            r"\b(universit|college|campus\s+comput|school\s+district|"
            r"community\s+college|student|academic|sdsu|ucla|ucsd|"
            r"cal\s?poly|csu\b|uc\s+(berkeley|davis|irvine|merced|riverside))\b",
            re.I,
        ),
    ),
    (
        "server_room_not_facility",
        re.compile(
            r"\b(computer\s+room|server\s+room|comput(er|ing)\s+lab|"
            r"machine\s+room|it\s+closet|network\s+closet|"
            r"open\s+computing\s+facility)\b",
            re.I,
        ),
    ),
    (
        "government_non_commercial",
        re.compile(
            r"\b(post\s+office|postal\s+service|usp[os]\b|terminal\s+annex|"
            r"city\s+hall|county\s+of\b|police|fire\s+station|"
            r"public\s+library|water\s+district)\b",
            re.I,
        ),
    ),
    (
        "healthcare_facility",
        re.compile(r"\b(hospital|medical\s+cent(er|re)|clinic|health\s+system|kaiser|sutter)\b", re.I),
    ),
    (
        "enterprise_self_operated",
        # Companies whose California sites are corporate campuses or internal IT,
        # not commercial data center capacity sold to third parties. Hyperscalers
        # are deliberately absent: their real data centers are in scope, and
        # their office campuses are filtered by the generator-fleet gate instead.
        re.compile(
            r"\b(cisco|fujitsu|avnet|mechanics\s+bank|tri\s+counties\s+bank|"
            r"cbs\s+inc|regulus|alexandria\s+real\s+estate|wave\s+broadband|"
            r"digital\s?path|iscs|charter\s+communications|econtactlive|"
            r"deliverex|jamestown|somo\s+village)\b",
            re.I,
        ),
    ),
]

# --------------------------------------------------------------------------
# Positive classification signals
# --------------------------------------------------------------------------

_HYPERSCALE_OPERATORS = {
    "Amazon Web Services",
    "Microsoft",
    "Google",
    "Meta",
    "Apple",
    "Oracle",
}

_WHOLESALE_OPERATORS = {
    "Vantage Data Centers",
    "STACK Infrastructure",
    "CloudHQ",
    "Prime Data Centers",
    "Aligned",
    "QTS",
    "Novva",
    "Sabey Data Centers",
}

_TELECOM_OPERATORS = {"AT&T", "Verizon", "Lumen Technologies"}

_COLO_OPERATORS = {
    "Equinix",
    "Digital Realty",
    "CoreSite",
    "CyrusOne",
    "EdgeConneX",
    "NTT",
    "Cologix",
    "Flexential",
    "DataBank",
    "H5 Data Centers",
    "Iron Mountain",
    "Switch",
    "Colovore",
    "Csquare",
    "Serverfarm",
    "TierPoint",
    "Ntirety",
    "CenterSquare",
    "Hurricane Electric",
    "Layer42",
    "SV Colo",
    "LightEdge",
    "Connect Data Centers",
    "Prime Data Centers",
}

_TELECOM_NAME_RE = re.compile(
    r"\b(switching\s+cent(er|re)|central\s+office|telephone\s+exchange|"
    r"toll\s+cent(er|re)|wire\s+cent(er|re))\b",
    re.I,
)


# --------------------------------------------------------------------------
# Operator canonicalisation
# --------------------------------------------------------------------------


def _norm_operator_key(value: str) -> str:
    value = re.sub(r"[^a-z0-9& ]+", " ", str(value).lower())
    value = re.sub(
        r"\b(inc|incorporated|llc|l\.?p|lp|ltd|limited|corp|corporation|co|"
        r"company|trust|holdings|group|realty|real\s+estate)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    path = REFERENCE_DIR / "operator_aliases.csv"
    if not path.exists():
        log.warning("operator alias file missing: %s", path)
        return {}
    table = pd.read_csv(path)
    mapping: dict[str, str] = {}
    for canonical, alias in zip(table["canonical"], table["alias"]):
        mapping[_norm_operator_key(alias)] = canonical
        mapping[_norm_operator_key(canonical)] = canonical
    return mapping


def canonical_operator(raw: object) -> str | None:
    """Map a free-text operator string onto a canonical company name."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or not str(raw).strip():
        return None
    key = _norm_operator_key(raw)
    if not key:
        return None
    aliases = _alias_map()
    if key in aliases:
        return aliases[key]
    # Longest-alias-wins substring fallback, so "Equinix SV1" resolves even
    # though the exact string is not in the alias table.
    hits = [(a, c) for a, c in aliases.items() if a and (a in key or key in a)]
    if hits:
        return max(hits, key=lambda kv: len(kv[0]))[1]
    return str(raw).strip()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _text_blob(row: pd.Series) -> str:
    """Text used for exclusion matching.

    Deliberately limited to name and operator. Street addresses were included
    in an earlier version and produced systematic false positives: Santa Clara's
    "Mission College Boulevard" hosts several genuine data centers (QTS,
    Evocative, Quality Investment Properties) and tripped the education rule on
    every one of them. Institutional words in a street name say nothing about
    the occupant.
    """
    parts = [row.get("name"), row.get("operator_raw")]
    return " ".join(str(p) for p in parts if p and not pd.isna(p))


def classify_row(row: pd.Series) -> tuple[str, str | None, str | None]:
    """Return ``(facility_class, exclusion_rule, canonical_operator)``.

    ``exclusion_rule`` is ``None`` for in-scope records.
    """
    operator = canonical_operator(row.get("operator_raw")) or canonical_operator(
        row.get("name")
    )
    name = str(row.get("name") or "")
    blob = _text_blob(row)

    # PeeringDB records are operator-maintained interconnection facilities and
    # are commercial by construction. Trust them over the keyword rules, which
    # otherwise strip legitimate sites.
    trusted = row.get("source") == "peeringdb"

    if not trusted:
        for rule, pattern in _EXCLUSION_RULES:
            if pattern.search(blob):
                return CLASS_UNKNOWN, rule, operator

    if operator in _HYPERSCALE_OPERATORS:
        return CLASS_HYPERSCALE, None, operator
    if operator in _WHOLESALE_OPERATORS:
        return CLASS_WHOLESALE, None, operator
    if operator in _COLO_OPERATORS:
        return CLASS_COLOCATION, None, operator

    if operator in _TELECOM_OPERATORS or _TELECOM_NAME_RE.search(name):
        # A carrier's central office is out of scope, but a carrier-operated
        # site listed in PeeringDB is selling interconnection and colocation
        # space and therefore is in scope. Presence in PeeringDB is the
        # discriminator; the tag alone is not enough.
        if trusted:
            return CLASS_COLOCATION, None, operator
        return CLASS_TELECOM, "telecom_central_office", operator

    return CLASS_UNKNOWN, None, operator


def apply(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split source records into (in_scope, excluded).

    The excluded frame carries an ``exclusion_rule`` column naming the rule
    responsible, and is written to ``data/processed/exclusions.csv`` for review.
    """
    if df.empty:
        empty = df.assign(facility_class=None, exclusion_rule=None, operator=None)
        return empty, empty

    verdicts = df.apply(classify_row, axis=1, result_type="expand")
    verdicts.columns = ["facility_class", "exclusion_rule", "operator"]
    tagged = pd.concat([df.reset_index(drop=True), verdicts.reset_index(drop=True)], axis=1)

    # Geographic sanity gate. PeeringDB carries genuine upstream data-entry
    # errors: QTS Charlotte (North Carolina), AceHost Durham (North Carolina)
    # and Cogent Fairfax are all recorded with state="CA". A coordinate several
    # hundred miles outside California is decisive regardless of the state field.
    minx, miny, maxx, maxy = CA_BBOX
    lat = pd.to_numeric(tagged.lat, errors="coerce")
    lon = pd.to_numeric(tagged.lon, errors="coerce")
    out_of_bounds = ~(lat.between(miny, maxy) & lon.between(minx, maxx))
    tagged.loc[out_of_bounds & tagged.exclusion_rule.isna(), "exclusion_rule"] = (
        "outside_california"
    )

    keep = tagged.exclusion_rule.isna() & tagged.facility_class.isin(IN_SCOPE_CLASSES)
    in_scope = tagged[keep].reset_index(drop=True)
    excluded = tagged[~keep].reset_index(drop=True)

    log.info(
        "classify: %d in scope, %d excluded (%s)",
        len(in_scope),
        len(excluded),
        excluded.exclusion_rule.value_counts().to_dict() if not excluded.empty else {},
    )
    return in_scope, excluded


#: An NEI-only record must show at least this many permitted generators to be
#: admitted as a data center. Justification: NEI assigns NAICS at the *company*
#: level, so Google's Santa Barbara and San Bruno offices arrive tagged 518210
#: alongside its Mountain View data center. Measuring the California fleet-size
#: distribution shows the split plainly -- 43 facilities have a single permitted
#: generator (Mechanics Bank, Tri Counties Bank, Avnet, Verizon Wireless MTSOs,
#: hyperscaler offices), while every facility above three units is a recognisable
#: data center. A commercial site with redundant power does not run one engine.
MIN_NEI_ONLY_GENERATORS = 4


def post_resolution_gate(
    facilities: pd.DataFrame,
    crosswalk: pd.DataFrame,
    generator_inventory: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop NEI-only facilities whose generator fleet is too small to be a DC.

    Applied *after* entity resolution so that corroboration counts: an NEI
    record that matched a PeeringDB or OSM record is retained regardless of
    fleet size, because a second independent source already vouches for it.
    """
    if facilities.empty:
        return facilities, facilities.iloc[0:0].copy()

    out = facilities.copy()

    if generator_inventory is None or generator_inventory.empty:
        fleet = pd.Series(0, index=out.index)
    else:
        links = crosswalk[crosswalk.source == "epa_nei"][["facility_id", "source_id"]]
        inv = generator_inventory.assign(
            source_id=generator_inventory.source_id.astype(str)
        )
        merged = links.assign(source_id=links.source_id.astype(str)).merge(
            inv[["source_id", "n_generator_units"]], on="source_id", how="left"
        )
        totals = merged.groupby("facility_id").n_generator_units.sum()
        fleet = out.facility_id.map(totals).fillna(0)

    nei_only = out.source_list.fillna("") == "epa_nei"
    drop = nei_only & (fleet < MIN_NEI_ONLY_GENERATORS)

    dropped = out[drop].copy()
    dropped["exclusion_rule"] = "nei_only_insufficient_generator_fleet"
    dropped["n_generator_units"] = fleet[drop].values

    kept = out[~drop].reset_index(drop=True)
    log.info(
        "scope gate: dropped %d NEI-only facilities with <%d permitted generators",
        len(dropped),
        MIN_NEI_ONLY_GENERATORS,
    )
    return kept, dropped
