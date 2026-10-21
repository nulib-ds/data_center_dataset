"""PeeringDB ingest.

PeeringDB's facility endpoint is the highest-precision free source for
commercial colocation sites: every record is a real interconnection facility,
maintained by the operators themselves. It carries no power figures, but
``net_count``/``ix_count``/``carrier_count`` quantify interconnection density,
which is a genuinely useful statistic in its own right and a weak signal of
facility significance.

Why the country is fetched in one request
-----------------------------------------
The obvious implementation -- one ``/api/fac?state=XX`` query per state -- is
wrong twice over:

1. **The ``state`` parameter is not an exact-match filter.** It is a
   full-text search across the facility and organisation records, so a
   per-state query returns facilities from *other* states. Observed in the
   live data: the North/South Carolina facilities QTS Charlotte (CTL1),
   AceHost Durham and Cogent Fairfax were returned by the California,
   Arkansas and Colorado queries (and "Cogent" matched ``state=CO``), while
   the Wisconsin facility CyberLynk MKE1 was returned by the Colorado query.
   The downstream bounding-box gate caught these, but only after they had
   already entered the per-state raw snapshots.

2. **It does not survive the anonymous API quota.** PeeringDB throttles
   unauthenticated callers to roughly 13 requests per hour; a 51-state sweep
   one query at a time takes hours and fails outright with HTTP 429.

The whole US facility set is 1,378 records (~1.3 MB) and fits in a single
request, well under the API's 5,000-record page cap. Fetching the country once
and splitting it locally removes the cross-contamination, puts every state on
one timestamp -- so the national roll-up is internally consistent -- and costs
one request instead of 51.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import pandas as pd

from ..config import raw_dir
from ..http import CachedClient
from ..normalize.schema import conform_source_frame
from ..states import (
    STATES,
    bbox_contains,
    state_bboxes,
    state_name_to_code,
    zip3_state,
)

log = logging.getLogger(__name__)

SOURCE = "peeringdb"
API_URL = "https://www.peeringdb.com/api/fac"
#: PeeringDB caps a page at 5,000 records; the US facility set is well under
#: that, so one page holds the whole country.
BULK_LIMIT = 5000
#: Sub-directory of ``raw/peeringdb`` holding the country-wide payload that
#: the per-state splits are derived from.
NATIONAL_DIR = "_US"

#: A ZIP written with its state in front, e.g. "FL 33487" or "VA 23454".
_ZIP_STATE_PREFIX = re.compile(r"^([A-Z]{2})\b")


def fetch_national(
    client: CachedClient,
    snapshot: str | None = None,
    refresh: bool = False,
) -> list[dict]:
    """Fetch every US facility in one request and snapshot the raw payload."""
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot, state=NATIONAL_DIR) / "peeringdb_fac_us.json"

    if dest.exists() and not refresh:
        return json.loads(dest.read_text())

    payload = client.fetch_json(
        API_URL,
        params={"country": "US", "limit": BULK_LIMIT},
        refresh=refresh,
    )
    records = payload.get("data", [])
    dest.write_text(json.dumps(records, indent=1, sort_keys=True))
    log.info("peeringdb: %d US facilities -> %s", len(records), dest.name)
    return records


def assign_state(record: dict) -> str | None:
    """Best-effort jurisdiction for one PeeringDB facility record.

    PeeringDB's ``state`` field is normally a two-letter postal code, but the
    live data carries three kinds of defect, all of which are handled here:

    * the full state name instead of the code ("South Carolina");
    * an empty ``state`` (nine records in the 2026-10-21 snapshot);
    * a code for a territory or a military ZIP, which is out of scope.

    The fallback order is postal code -> full name -> state-prefixed ZIP ->
    ZIP prefix -> geographic containment in a state bounding box. A record that
    still cannot be placed is returned as ``None`` rather than guessed into a
    neighbouring state; it stays in the national raw payload, so nothing is
    silently lost.

    A code for a territory that is not one of the 51 jurisdictions (Puerto
    Rico, say) is returned as itself, so that "out of scope" stays
    distinguishable from "could not be resolved".
    """
    raw = str(record.get("state") or "").strip()
    if len(raw) == 2 and raw.isalpha():
        return raw.upper()
    by_name = state_name_to_code(raw)
    if by_name:
        return by_name

    zipcode = str(record.get("zipcode") or "").strip()
    prefixed = _ZIP_STATE_PREFIX.match(zipcode.upper())
    if prefixed and prefixed.group(1) in STATES:
        return prefixed.group(1)
    by_zip = zip3_state(zipcode)
    if by_zip:
        return by_zip

    lat, lon = record.get("latitude"), record.get("longitude")
    if lat is not None and lon is not None:
        hits = [
            code
            for code, profile in STATES.items()
            if any(bbox_contains(box, lat, lon) for box in state_bboxes(profile))
        ]
        if len(hits) == 1:
            return hits[0]
        if hits:
            # The state boxes are deliberately generous, so a point near a
            # border can fall inside two of them. Guessing here would place a
            # facility in the wrong state, which is worse than leaving it out.
            log.warning(
                "peeringdb: facility %s is ambiguous between %s; left unassigned",
                record.get("id"),
                ", ".join(sorted(hits)),
            )
    return None


def fetch(
    client: CachedClient,
    state: str = "CA",
    snapshot: str | None = None,
    refresh: bool = False,
) -> list[dict]:
    """Return the PeeringDB facilities in ``state``, snapshotting the split.

    The per-state file is derived from the country-wide payload rather than
    queried directly, so it contains exactly the facilities that belong to the
    state -- and every state is drawn from the same payload.
    """
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot, state=state) / "peeringdb_fac.json"

    if dest.exists() and not refresh:
        return json.loads(dest.read_text())

    records = fetch_national(client, snapshot=snapshot, refresh=refresh)
    wanted = state.strip().upper()
    subset = [r for r in records if assign_state(r) == wanted]
    dest.write_text(json.dumps(subset, indent=1, sort_keys=True))
    log.info("peeringdb: %d facilities -> %s", len(subset), dest.name)
    return subset


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
                "state": assign_state(r),
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
