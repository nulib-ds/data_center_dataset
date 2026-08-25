"""Entity resolution across sources.

The same physical building appears in OSM as a tagged polygon and in PeeringDB
as an operator-submitted point, with different names ("STACK Infrastructure" vs
"STACK: SVY01B") and coordinates that can disagree by a few hundred metres.

Strategy: block candidate pairs by spatial proximity, score them on name,
operator and address agreement, then merge greedily above a confidence
threshold. Pairs in the uncertain middle band are written to a review queue
rather than being merged silently -- an automated guess that quietly fuses two
distinct facilities is worse than an admitted unknown.
"""

from __future__ import annotations

import hashlib
import logging
import re
from difflib import SequenceMatcher

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from ..config import (
    CA_EQUAL_AREA_CRS,
    DEDUPE_AUTO_MERGE,
    DEDUPE_RADIUS_M,
    DEDUPE_REVIEW_FLOOR,
    WGS84,
)

log = logging.getLogger(__name__)

#: Source ranking used when picking the surviving attribute value. PeeringDB
#: names are operator-supplied and most reliable; OSM contributes geometry.
SOURCE_PRIORITY = {"peeringdb": 0, "osm": 1, "ceqanet": 2, "epa_nei": 3, "epa_frs": 4}

_STOPWORDS = {
    "data",
    "center",
    "centre",
    "centers",
    "centres",
    "datacenter",
    "the",
    "at",
    "of",
    "and",
    "inc",
    "llc",
    "corp",
    "campus",
    "facility",
    "site",
    "building",
    "bldg",
}

_STREET_ABBREV = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "road": "rd",
    "lane": "ln",
    "court": "ct",
    "parkway": "pkwy",
    "place": "pl",
    "highway": "hwy",
    "terrace": "ter",
    "circle": "cir",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}


def _name_tokens(value: object) -> set[str]:
    if value is None or pd.isna(value):
        return set()
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value).lower())
    return {t for t in text.split() if t and t not in _STOPWORDS}


#: Data center marketing names are built around short site codes -- SFO1, SV5,
#: LA1, SJC11, SVY01, SC1. The code is far more diagnostic of identity than the
#: surrounding words, which change with rebranding ("Csquare SFO1" became
#: "Centersquare Silicon Valley (SFO1)"). Token-set overlap alone scores that
#: pair at 0.2 and misses the match, so codes are compared separately.
_CODE_RE = re.compile(r"^[a-z]{2,4}\d{1,3}[a-z]?$")


def _code_tokens(value: object) -> set[str]:
    return {t for t in _name_tokens(value) if _CODE_RE.match(t)}


def normalize_address(value: object) -> str:
    """Canonicalise a street address for comparison."""
    if value is None or pd.isna(value):
        return ""
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value).lower())
    words = []
    for word in text.split():
        words.append(_STREET_ABBREV.get(word, word))
    return " ".join(words).strip()


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _street_number(address: str) -> str | None:
    match = re.match(r"^(\d+)", address)
    return match.group(1) if match else None


def forbidden_pair(left: pd.Series, right: pd.Series) -> str | None:
    """Hard evidence that two records are *different* buildings.

    Returned as a reason string, or ``None`` if no blocker applies. These
    constraints must survive transitive clustering: without them a chain of
    pairwise merges silently collapses distinct buildings. Observed failures
    that motivated each rule are noted inline.
    """
    lcodes, rcodes = _code_tokens(left.get("name")), _code_tokens(right.get("name"))
    if lcodes and rcodes and not (lcodes & rcodes):
        # CoreSite SV3 and SV7 are separate buildings 283 m apart; without this
        # they merged transitively through SV4.
        return "distinct_site_codes"

    la = normalize_address(left.get("address"))
    ra = normalize_address(right.get("address"))
    lnum, rnum = _street_number(la), _street_number(ra)
    if lnum and rnum and lnum != rnum:
        # Digital Realty's 1201, 1525 and 1725 Comstock Street facilities plus
        # 1100/1500 Space Park merged into a single record because they share an
        # operator and sit within 250 m of one another.
        return "distinct_street_numbers"

    return None


def pair_score(left: pd.Series, right: pd.Series, distance_m: float) -> tuple[float, dict]:
    """Score the likelihood that two records describe the same facility.

    Returns the score in [0, 1] plus the component breakdown, which is retained
    for the review queue so a human can see *why* a pair scored as it did.
    """
    # Distance: full credit under 60 m, decaying to zero at the block radius.
    if distance_m <= 60:
        dist_score = 1.0
    else:
        dist_score = max(0.0, 1.0 - (distance_m - 60) / (DEDUPE_RADIUS_M - 60))

    lt, rt = _name_tokens(left.get("name")), _name_tokens(right.get("name"))
    if lt and rt:
        name_score = len(lt & rt) / len(lt | rt)
    else:
        name_score = 0.0

    la, ra = normalize_address(left.get("address")), normalize_address(right.get("address"))
    addr_score = _ratio(la, ra) if la and ra else 0.0

    # Exact street-number agreement is a very strong signal.
    lnum = re.match(r"^(\d+)", la)
    rnum = re.match(r"^(\d+)", ra)
    if lnum and rnum:
        addr_score = 1.0 if lnum.group(1) == rnum.group(1) and addr_score > 0.55 else addr_score

    lo, ro = left.get("operator"), right.get("operator")
    if lo and ro and not pd.isna(lo) and not pd.isna(ro):
        op_score = 1.0 if str(lo) == str(ro) else _ratio(str(lo).lower(), str(ro).lower())
    else:
        op_score = 0.0

    components = {
        "distance_m": round(float(distance_m), 1),
        "distance": round(dist_score, 3),
        "name": round(name_score, 3),
        "address": round(addr_score, 3),
        "operator": round(op_score, 3),
    }

    score = (
        0.34 * dist_score
        + 0.24 * name_score
        + 0.22 * addr_score
        + 0.20 * op_score
    )

    lcodes, rcodes = _code_tokens(left.get("name")), _code_tokens(right.get("name"))
    components["codes"] = f"{sorted(lcodes)}|{sorted(rcodes)}"

    if lcodes and rcodes and (lcodes & rcodes):
        # Same site code within the blocking radius: the same building.
        score = max(score, 0.82)

    # Same operator at effectively the same coordinates is decisive even when
    # the marketing names differ completely.
    if op_score >= 0.99 and distance_m <= 250:
        score = max(score, 0.80)

    # Same operator at the same street address is the same facility regardless
    # of coordinate disagreement, which is often just geocoding noise. Safe
    # against carrier hotels: One Wilshire hosts several facilities at one
    # address but under *different* operators (CoreSite, Multacom, JMA NAC).
    if op_score >= 0.99 and addr_score >= 0.90:
        score = max(score, 0.82)

    # Conversely, a different operator at distance should never auto-merge.
    if lo and ro and op_score < 0.55 and distance_m > 150:
        score = min(score, 0.45)

    return min(score, 1.0), components


def _candidate_pairs(gdf: gpd.GeoDataFrame) -> list[tuple[int, int, float]]:
    """Spatially block: all pairs within ``DEDUPE_RADIUS_M``, across sources."""
    buffered = gdf.copy()
    buffered["geometry"] = buffered.geometry.buffer(DEDUPE_RADIUS_M)
    joined = gpd.sjoin(
        buffered[["geometry"]], gdf[["geometry"]], how="inner", predicate="intersects"
    )

    pairs: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    coords = {idx: (geom.x, geom.y) for idx, geom in gdf.geometry.items()}

    for left, right in zip(joined.index, joined.index_right):
        if left == right:
            continue
        key = (min(left, right), max(left, right))
        if key in seen:
            continue
        seen.add(key)
        (x1, y1), (x2, y2) = coords[key[0]], coords[key[1]]
        pairs.append((key[0], key[1], float(np.hypot(x1 - x2, y1 - y2))))
    return pairs


class _ConstrainedClusters:
    """Greedy agglomerative clustering that respects hard cannot-link pairs.

    Plain Union-Find is unsafe here. Pairwise guards prevented CoreSite SV3 from
    merging with SV7 directly, but both merged with SV4 and transitivity did the
    damage anyway. This structure tests the full cross product of two clusters
    against the cannot-link set before joining them, so a forbidden pair can
    never end up co-clustered by any chain of intermediate merges.
    """

    def __init__(self, keys) -> None:
        self.members: dict[int, set] = {k: {k} for k in keys}
        self.owner: dict = {k: k for k in keys}
        self.cannot_link: set[tuple] = set()

    def forbid(self, a, b) -> None:
        self.cannot_link.add((min(a, b), max(a, b)))

    def _blocked(self, ca, cb) -> bool:
        left, right = self.members[ca], self.members[cb]
        # Iterate the smaller side for a cheaper check.
        if len(left) > len(right):
            left, right = right, left
        for i in left:
            for j in right:
                if (min(i, j), max(i, j)) in self.cannot_link:
                    return True
        return False

    def try_union(self, a, b) -> bool:
        ca, cb = self.owner[a], self.owner[b]
        if ca == cb:
            return True
        if self._blocked(ca, cb):
            return False
        # Fold the smaller cluster into the larger.
        if len(self.members[ca]) < len(self.members[cb]):
            ca, cb = cb, ca
        for member in self.members[cb]:
            self.owner[member] = ca
        self.members[ca] |= self.members.pop(cb)
        return True

    def label(self, key):
        return self.owner[key]


def _facility_id(name: str, lat: float, lon: float) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:44] or "facility"
    digest = hashlib.sha1(f"{slug}|{lat:.5f}|{lon:.5f}".encode()).hexdigest()[:6]
    return f"{slug}-{digest}"


def _pick(group: pd.DataFrame, column: str):
    """Choose the best value for a column, respecting source priority."""
    sub = group[group[column].notna()]
    if sub.empty:
        return None
    sub = sub.assign(_rank=sub["source"].map(SOURCE_PRIORITY).fillna(99))
    return sub.sort_values("_rank").iloc[0][column]


def _review_row(
    work: pd.DataFrame, i: int, j: int, score: float, comps: dict, note: str | None
) -> dict:
    return {
        "left_source": work.loc[i, "source"],
        "left_source_id": work.loc[i, "source_id"],
        "left_name": work.loc[i, "name"],
        "left_address": work.loc[i, "address"],
        "right_source": work.loc[j, "source"],
        "right_source_id": work.loc[j, "source_id"],
        "right_name": work.loc[j, "name"],
        "right_address": work.loc[j, "address"],
        "score": round(score, 3),
        "note": note,
        **comps,
    }


def resolve(records: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Cluster source records into canonical facilities.

    Returns ``(facilities, crosswalk, review_queue)``.
    """
    if records.empty:
        return records.copy(), records.copy(), records.copy()

    work = records.reset_index(drop=True).copy()
    gdf = gpd.GeoDataFrame(
        work[["name", "operator", "address", "source"]].copy(),
        geometry=[Point(xy) for xy in zip(work.lon, work.lat)],
        crs=WGS84,
    ).to_crs(CA_EQUAL_AREA_CRS)

    pairs = _candidate_pairs(gdf)
    log.info("dedupe: %d candidate pairs from %d records", len(pairs), len(work))

    clusters = _ConstrainedClusters(work.index)
    review_rows = []
    scored = []

    # First pass: score everything and register the hard cannot-link pairs, so
    # that constraints are known before any merging happens.
    for i, j, dist in pairs:
        reason = forbidden_pair(work.loc[i], work.loc[j])
        if reason:
            clusters.forbid(i, j)
        score, comps = pair_score(work.loc[i], work.loc[j], dist)
        scored.append((score, i, j, comps, reason))

    # Second pass: merge best-first so that the strongest evidence wins when a
    # constraint forces a choice between competing merges.
    n_blocked = 0
    for score, i, j, comps, reason in sorted(scored, key=lambda t: -t[0]):
        if reason:
            continue
        if score >= DEDUPE_AUTO_MERGE:
            if not clusters.try_union(i, j):
                n_blocked += 1
                review_rows.append(
                    _review_row(work, i, j, score, comps, "blocked_by_constraint")
                )
        elif score >= DEDUPE_REVIEW_FLOOR:
            review_rows.append(_review_row(work, i, j, score, comps, None))

    work["_cluster"] = [clusters.label(i) for i in work.index]
    if n_blocked:
        log.info("dedupe: %d merges refused by cannot-link constraints", n_blocked)

    facilities = []
    crosswalk = []

    for cluster_id, group in work.groupby("_cluster", sort=False):
        # Prefer a polygon-derived centroid; otherwise average the points.
        with_area = group[group.footprint_sqm.notna()]
        if not with_area.empty:
            anchor = with_area.sort_values("footprint_sqm", ascending=False).iloc[0]
            lat, lon = float(anchor.lat), float(anchor.lon)
        else:
            lat, lon = float(group.lat.mean()), float(group.lon.mean())

        name = _pick(group, "name") or "Unnamed data center"
        fid = _facility_id(str(name), lat, lon)

        classes = [c for c in group.facility_class.dropna().unique() if c != "unknown"]
        facility_class = classes[0] if classes else "unknown"

        facilities.append(
            {
                "facility_id": fid,
                "name": str(name),
                "operator": _pick(group, "operator"),
                "operator_confidence": _pick(group, "operator_confidence"),
                "facility_class": facility_class,
                "lat": lat,
                "lon": lon,
                "address": _pick(group, "address"),
                "city": _pick(group, "city"),
                "postcode": _pick(group, "postcode"),
                "year_built": _pick(group, "year_built"),
                "building_levels": _pick(group, "building_levels"),
                "footprint_sqm": group.footprint_sqm.max(),
                "footprint_sqft": (
                    group.footprint_sqft.max()
                    if "footprint_sqft" in group.columns
                    else pd.NA
                ),
                "geometry_wkt": _pick(group, "geometry_wkt"),
                "clli": _pick(group, "clli"),
                "website": _pick(group, "website"),
                "pdb_net_count": group.pdb_net_count.max(),
                "pdb_ix_count": group.pdb_ix_count.max(),
                "pdb_carrier_count": group.pdb_carrier_count.max(),
                "source_list": ",".join(sorted(group.source.unique())),
                "n_sources": int(group.source.nunique()),
            }
        )

        for _, row in group.iterrows():
            crosswalk.append(
                {
                    "facility_id": fid,
                    "source": row.source,
                    "source_id": row.source_id,
                    "source_url": row.source_url,
                    "source_name": row["name"],
                }
            )

    fac_df = pd.DataFrame(facilities)
    # Guard against a hash collision producing duplicate ids.
    if fac_df.facility_id.duplicated().any():
        dup = fac_df.facility_id.duplicated(keep=False)
        fac_df.loc[dup, "facility_id"] = fac_df.loc[dup, "facility_id"] + "-" + (
            fac_df.loc[dup].groupby("facility_id").cumcount().astype(str)
        )

    review_df = pd.DataFrame(review_rows).sort_values(
        "score", ascending=False
    ) if review_rows else pd.DataFrame(
        columns=["left_name", "right_name", "score"]
    )

    log.info(
        "dedupe: %d records -> %d facilities (%d pairs queued for review)",
        len(work),
        len(fac_df),
        len(review_df),
    )
    return fac_df, pd.DataFrame(crosswalk), review_df
