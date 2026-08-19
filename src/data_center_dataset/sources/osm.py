"""OpenStreetMap ingest via the Overpass API.

Queries every California feature carrying a data-center tag and retains full
way geometry (``out geom``) so that building footprint area can be computed
downstream. Note that OSM contains *no* power tags for these features -- it
contributes location, footprint, operator and construction year only.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd
from shapely.geometry import Polygon
from shapely.geometry import mapping as shapely_mapping

from ..config import raw_dir
from ..http import CachedClient
from ..normalize.schema import conform_source_frame

log = logging.getLogger(__name__)

SOURCE = "osm"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: California is selected by ISO code rather than a bounding box so that
#: neighbouring states are excluded cleanly.
OVERPASS_QUERY = """
[out:json][timeout:300];
area["ISO3166-2"="US-CA"]["admin_level"="4"]->.ca;
(
  nwr(area.ca)["telecom"="data_center"];
  nwr(area.ca)["telecom"="data_centre"];
  nwr(area.ca)["building"="data_center"];
  nwr(area.ca)["building"="data_centre"];
);
out tags geom;
"""


def fetch(client: CachedClient, snapshot: str | None = None, refresh: bool = False) -> dict:
    """Download the raw Overpass response and persist a dated snapshot."""
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot) / "overpass_ca_datacenters.json"

    if dest.exists() and not refresh:
        return json.loads(dest.read_text())

    log.info("querying Overpass for California data centers")
    payload = client.fetch_json(
        OVERPASS_URL,
        method="POST",
        data={"data": OVERPASS_QUERY},
        refresh=refresh,
    )
    dest.write_text(json.dumps(payload, indent=1, sort_keys=True))
    log.info("osm: %d elements -> %s", len(payload.get("elements", [])), dest.name)
    return payload


def _geometry(element: dict) -> tuple[str | None, float | None, float | None, float | None]:
    """Return (wkt, lat, lon, footprint_sqm_placeholder).

    Area is deliberately *not* computed here: doing it in a projected CRS for
    the whole frame at once (see ``normalize.geometry``) is both faster and
    less error-prone than per-row transforms.
    """
    etype = element.get("type")

    if etype == "node":
        lat, lon = element.get("lat"), element.get("lon")
        return None, lat, lon, None

    geom = element.get("geometry") or []
    coords = [(p["lon"], p["lat"]) for p in geom if "lon" in p and "lat" in p]
    if len(coords) < 3:
        if coords:
            return None, coords[0][1], coords[0][0], None
        centre = element.get("center") or {}
        return None, centre.get("lat"), centre.get("lon"), None

    if coords[0] != coords[-1]:
        coords.append(coords[0])
    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            raise ValueError("empty polygon")
        centroid = poly.centroid
        return poly.wkt, centroid.y, centroid.x, None
    except Exception:  # pragma: no cover - degenerate geometry
        log.debug("unusable geometry on %s/%s", etype, element.get("id"))
        return None, coords[0][1], coords[0][0], None


def parse(payload: dict) -> pd.DataFrame:
    """Normalize an Overpass response into the common source-record shape."""
    rows: list[dict] = []

    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        wkt, lat, lon, _ = _geometry(el)
        if lat is None or lon is None:
            continue

        osm_type, osm_id = el.get("type"), el.get("id")
        rows.append(
            {
                "source": SOURCE,
                "source_id": f"{osm_type}/{osm_id}",
                "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                "name": tags.get("name") or tags.get("operator") or tags.get("short_name"),
                "operator_raw": tags.get("operator"),
                "lat": lat,
                "lon": lon,
                "address": " ".join(
                    p
                    for p in (tags.get("addr:housenumber"), tags.get("addr:street"))
                    if p
                )
                or None,
                "city": tags.get("addr:city"),
                "state": tags.get("addr:state"),
                "postcode": tags.get("addr:postcode"),
                "status_raw": tags.get("construction") or tags.get("disused"),
                "year_built": _year(tags.get("start_date")),
                "building_levels": tags.get("building:levels"),
                "geometry_wkt": wkt,
                "clli": tags.get("clli"),
                "website": tags.get("website") or tags.get("contact:website"),
                "raw_json": json.dumps(tags, sort_keys=True),
            }
        )

    df = conform_source_frame(pd.DataFrame(rows))
    log.info("osm: parsed %d records", len(df))
    return df


def _year(value: str | None) -> int | None:
    """Extract a 4-digit year from an OSM ``start_date`` value."""
    if not value:
        return None
    digits = "".join(c for c in str(value)[:4] if c.isdigit())
    if len(digits) == 4:
        year = int(digits)
        if 1900 <= year <= 2100:
            return year
    return None


def load(client: CachedClient, **kw) -> pd.DataFrame:
    return parse(fetch(client, **kw))
