"""OpenStreetMap element history -- for dataset provenance, *not* build years.

Why this module does not produce construction dates
---------------------------------------------------
It is tempting to read an element's first changeset timestamp as the year the
building appeared. Tested against the six California data-center elements that
carry a genuine ``start_date`` tag, that reading is wrong by a median of about
forty years and shows no correlation at all:

===========================  ==========  =========  =========
Facility                     start_date  OSM v1     error
===========================  ==========  =========  =========
USPO Terminal Annex          1938        2009       +71 yr
Serverfarm LAX1              1973        2016       +43 yr
AT&T                         1974        2017       +43 yr
(unnamed)                    1976        2016       +40 yr
DRT LAX12                    1979        2016       +37 yr
(unnamed)                    1990        2016       +26 yr
===========================  ==========  =========  =========

The OSM dates cluster in 2016 -- a Los Angeles mapping campaign -- regardless of
whether the building went up in 1938 or 1990.

A second date is also available and equally unsuitable. For LightEdge SAN1 the
footprint was drawn in 2009 (v1) but ``telecom=data_center`` only appeared in
2023 (v5). So OSM offers "when someone drew this building" and "when someone
noticed it was a data center", and neither is when it was built.

What the data *is* good for
---------------------------
Measuring the dataset itself: when these facilities entered OpenStreetMap, and
how quickly the community has been identifying data centers. That is a genuine
statement about coverage and vintage, and it is the only way this module's output
is used -- see ``charts.provenance_timeline``.
"""

from __future__ import annotations

import glob
import json
import logging
from datetime import date

import pandas as pd

from ..config import RAW_DIR, raw_dir
from ..http import CachedClient

log = logging.getLogger(__name__)

SOURCE = "osm_history"
API_TEMPLATE = "https://api.openstreetmap.org/api/0.6/{type}/{id}/history.json"

#: Tag/value pairs that mark an element as a data center.
DATA_CENTER_TAGS = {
    "telecom": {"data_center", "data_centre"},
    "building": {"data_center", "data_centre"},
}


def _is_data_center(tags: dict) -> bool:
    return any(tags.get(k) in vals for k, vals in DATA_CENTER_TAGS.items())


def _latest_osm_snapshot() -> dict:
    paths = sorted(glob.glob(str(RAW_DIR / "osm" / "*" / "overpass_ca_datacenters.json")))
    if not paths:
        raise FileNotFoundError("No OSM snapshot found. Run `fetch` or `build` first.")
    return json.loads(open(paths[-1]).read())


def fetch(
    client: CachedClient,
    *,
    snapshot: str | None = None,
    refresh: bool = False,
    elements: list[dict] | None = None,
) -> pd.DataFrame:
    """Harvest version history for every data-center element in the OSM snapshot.

    One request per element against the OSM API, rate limited by
    :class:`CachedClient`. Roughly 115 requests for California.
    """
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot) / "osm_element_history.csv"
    if dest.exists() and not refresh:
        return pd.read_csv(dest)

    if elements is None:
        elements = _latest_osm_snapshot().get("elements", [])

    rows: list[dict] = []
    for i, element in enumerate(elements, start=1):
        etype, eid = element.get("type"), element.get("id")
        if etype is None or eid is None:
            continue
        url = API_TEMPLATE.format(type=etype, id=eid)
        try:
            versions = client.fetch_json(url, refresh=refresh).get("elements", [])
        except Exception as exc:  # pragma: no cover - upstream availability
            log.warning("osm history %s/%s failed: %s", etype, eid, exc)
            continue
        if not versions:
            continue

        versions = sorted(versions, key=lambda v: v.get("version", 0))
        first = versions[0]

        dc_first = None
        for version in versions:
            if _is_data_center(version.get("tags") or {}):
                dc_first = version.get("timestamp")
                break

        latest_tags = versions[-1].get("tags") or {}
        rows.append(
            {
                "osm_type": etype,
                "osm_id": eid,
                "source_id": f"{etype}/{eid}",
                "name": latest_tags.get("name"),
                "n_versions": len(versions),
                "created_ts": first.get("timestamp"),
                "created_user": first.get("user"),
                "created_changeset": first.get("changeset"),
                "datacenter_tagged_ts": dc_first,
                "start_date_tag": latest_tags.get("start_date"),
            }
        )
        if i % 25 == 0:
            log.info("osm history: %d/%d elements", i, len(elements))

    df = pd.DataFrame(rows)
    if not df.empty:
        df["created_year"] = pd.to_datetime(
            df.created_ts, errors="coerce", utc=True
        ).dt.year
        df["datacenter_tagged_year"] = pd.to_datetime(
            df.datacenter_tagged_ts, errors="coerce", utc=True
        ).dt.year
    df.to_csv(dest, index=False)
    log.info("osm history: %d elements -> %s", len(df), dest.name)
    return df


def start_date_validation(history: pd.DataFrame) -> pd.DataFrame:
    """Compare OSM dates against real ``start_date`` tags.

    Returns the evidence table that justifies refusing to treat changeset
    timestamps as construction years. Published alongside the provenance chart
    so the negative result travels with the figure.
    """
    if history.empty or "start_date_tag" not in history.columns:
        return pd.DataFrame()

    sub = history[history.start_date_tag.notna()].copy()
    if sub.empty:
        return pd.DataFrame()

    sub["real_year"] = pd.to_numeric(
        sub.start_date_tag.astype(str).str.extract(r"(\d{4})")[0], errors="coerce"
    )
    sub = sub[sub.real_year.between(1850, 2100)]
    sub["osm_created_year"] = pd.to_numeric(sub.created_year, errors="coerce")
    sub["error_years"] = sub.osm_created_year - sub.real_year

    return sub[
        [
            "source_id",
            "name",
            "real_year",
            "osm_created_year",
            "datacenter_tagged_year",
            "error_years",
            "n_versions",
        ]
    ].sort_values("real_year")


def load(client: CachedClient, **kw) -> pd.DataFrame:
    return fetch(client, **kw)
