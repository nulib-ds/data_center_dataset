"""PeeringDB ingest.

PeeringDB's facility endpoint is the highest-precision free source for
commercial colocation sites: every record is a real interconnection facility,
maintained by the operators themselves. It carries no power figures, but
``net_count``/``ix_count``/``carrier_count`` quantify interconnection density,
which is a genuinely useful statistic in its own right and a weak signal of
facility significance.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from ..config import raw_dir
from ..http import CachedClient
from ..normalize.schema import conform_source_frame

log = logging.getLogger(__name__)

SOURCE = "peeringdb"
API_URL = "https://www.peeringdb.com/api/fac"
PAGE_SIZE = 250


def fetch(client: CachedClient, snapshot: str | None = None, refresh: bool = False) -> list[dict]:
    """Page through all California facilities and snapshot the raw payload."""
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot) / "peeringdb_fac_ca.json"

    if dest.exists() and not refresh:
        return json.loads(dest.read_text())

    records: list[dict] = []
    offset = 0
    while True:
        payload = client.fetch_json(
            API_URL,
            params={
                "state": "CA",
                "country": "US",
                "limit": PAGE_SIZE,
                "skip": offset,
            },
            refresh=refresh,
        )
        batch = payload.get("data", [])
        records.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    dest.write_text(json.dumps(records, indent=1, sort_keys=True))
    log.info("peeringdb: %d facilities -> %s", len(records), dest.name)
    return records


def parse(records: list[dict]) -> pd.DataFrame:
    rows = []
    for r in records:
        if str(r.get("status", "ok")).lower() != "ok":
            continue
        lat, lon = r.get("latitude"), r.get("longitude")
        if lat is None or lon is None:
            continue

        addr = " ".join(p for p in (r.get("address1"), r.get("address2")) if p) or None
        rows.append(
            {
                "source": SOURCE,
                "source_id": str(r.get("id")),
                "source_url": f"https://www.peeringdb.com/fac/{r.get('id')}",
                "name": r.get("name"),
                "operator_raw": r.get("org_name"),
                "lat": lat,
                "lon": lon,
                "address": addr,
                "city": r.get("city"),
                "state": r.get("state"),
                "postcode": r.get("zipcode"),
                "clli": r.get("clli") or None,
                "website": r.get("website") or None,
                "pdb_net_count": r.get("net_count"),
                "pdb_ix_count": r.get("ix_count"),
                "pdb_carrier_count": r.get("carrier_count"),
                "campus_raw": r.get("campus_id"),
                "raw_json": json.dumps(
                    {
                        k: r.get(k)
                        for k in (
                            "id",
                            "org_id",
                            "org_name",
                            "name",
                            "aka",
                            "campus_id",
                            "available_voltage_services",
                            "diverse_serving_substations",
                            "property",
                            "net_count",
                            "ix_count",
                            "carrier_count",
                        )
                    },
                    sort_keys=True,
                ),
            }
        )

    df = conform_source_frame(pd.DataFrame(rows))
    log.info("peeringdb: parsed %d records", len(df))
    return df


def load(client: CachedClient, **kw) -> pd.DataFrame:
    return parse(fetch(client, **kw))
