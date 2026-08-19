"""CEQAnet ingest -- best-effort Tier A evidence for new-build projects.

Scope and honesty note
----------------------
CEQAnet is the only free, automatable route to *attested* megawatt figures,
because CEQA filings for new data centers routinely state connected load. It is
nonetheless a **low-recall** source, and the limitations are structural rather
than fixable:

* The CEQA database has no free-text keyword filter. The only text search on the
  site is a Google Custom Search wrapper requiring an API key.
* The database endpoint returns at most 100 rows per query and ignores page
  parameters, so enumeration must proceed by narrow date windows.
* Project titles frequently describe a data center without using the words
  ("North Watt Avenue Specific Plan"), so title filtering misses real projects.

Consequently this module is **not run by default**. Enable it with
``--with-ceqanet``. The primary Tier A path is ``data/reference/
manual_overrides.csv``, where a curator records figures with citations.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

import pandas as pd
from selectolax.parser import HTMLParser

from ..config import raw_dir
from ..http import CachedClient

log = logging.getLogger(__name__)

SOURCE = "ceqanet"
SEARCH_URL = "https://ceqanet.opr.ca.gov/Search"
DETAIL_URL = "https://ceqanet.opr.ca.gov/{sch}"

#: Development types plausibly covering data center projects.
DEVELOPMENT_TYPES = ("Industrial", "Commercial", "Office", "Power")

_TITLE_RE = re.compile(r"data\s?cent(er|re)|colocation|hyperscale", re.I)

#: Matches a megawatt figure with enough surrounding context to judge it.
_MW_RE = re.compile(
    r"(?P<pre>[^.\n]{0,140}?)"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>MW|megawatts?)"
    r"(?P<post>[^.\n]{0,140})",
    re.I,
)

#: Words that indicate the figure describes electrical demand rather than, say,
#: a solar array or an unrelated generating station.
_LOAD_HINTS = re.compile(
    r"\b(load|demand|capacity|connected|critical|it\s+load|power|electrical|"
    r"substation|service|utility)\b",
    re.I,
)


def _month_windows(start: date, end: date) -> list[tuple[str, str]]:
    """Inclusive month-by-month windows, to stay under the 100-row cap."""
    windows = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        if cursor.month == 12:
            nxt = date(cursor.year + 1, 1, 1)
        else:
            nxt = date(cursor.year, cursor.month + 1, 1)
        windows.append((cursor.isoformat(), min(nxt - timedelta(days=1), end).isoformat()))
        cursor = nxt
    return windows


def _parse_results(html: str) -> list[dict]:
    doc = HTMLParser(html)
    table = doc.css_first("table")
    if table is None:
        return []
    out = []
    for tr in table.css("tr")[1:]:
        cells = [td.text(strip=True) for td in tr.css("td")]
        if len(cells) < 5:
            continue
        out.append(
            {
                "sch_number": cells[0],
                "document_type": cells[1],
                "lead_agency": cells[2],
                "received": cells[3],
                "title": cells[4],
            }
        )
    return out


def discover(
    client: CachedClient,
    *,
    start_year: int = 2015,
    snapshot: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Enumerate CEQA filings whose titles suggest a data center project."""
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot) / "ceqanet_candidates.csv"
    if dest.exists() and not refresh:
        return pd.read_csv(dest, dtype=str)

    windows = _month_windows(date(start_year, 1, 1), date.today())
    hits: dict[str, dict] = {}

    for dev_type in DEVELOPMENT_TYPES:
        for win_start, win_end in windows:
            try:
                html = client.fetch_text(
                    SEARCH_URL,
                    params={
                        "DevelopmentType": dev_type,
                        "StartRange": win_start,
                        "EndRange": win_end,
                    },
                    suffix=".html",
                )
            except Exception as exc:  # pragma: no cover - transient upstream
                log.warning("ceqanet window %s %s failed: %s", dev_type, win_start, exc)
                continue

            rows = _parse_results(html)
            if len(rows) >= 100:
                log.warning(
                    "ceqanet: %s %s hit the 100-row cap; results truncated",
                    dev_type,
                    win_start,
                )
            for row in rows:
                if _TITLE_RE.search(row["title"]):
                    row["development_type"] = dev_type
                    hits[row["sch_number"]] = row

    df = pd.DataFrame(sorted(hits.values(), key=lambda r: r["sch_number"]))
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "sch_number",
                "document_type",
                "lead_agency",
                "received",
                "title",
                "development_type",
            ]
        )
    df.to_csv(dest, index=False)
    log.info("ceqanet: %d candidate data-center filings", len(df))
    return df


def extract_mw(text: str) -> list[dict]:
    """Pull candidate megawatt figures with their surrounding sentence."""
    found = []
    for match in _MW_RE.finditer(text):
        context = f"{match.group('pre')}{match.group(0)[len(match.group('pre')):]}".strip()
        window = match.group("pre") + " " + match.group("post")
        if not _LOAD_HINTS.search(window):
            continue
        try:
            value = float(match.group("value").replace(",", ""))
        except ValueError:
            continue
        # Reject figures outside any plausible facility range.
        if not (0.5 <= value <= 1500):
            continue
        blob = window.lower()
        if "critical" in blob or "it load" in blob:
            basis = "critical_load"
        elif "connected" in blob or "total" in blob or "service" in blob:
            basis = "total_facility"
        else:
            basis = "total_facility"
        found.append(
            {
                "value_mw": value,
                "basis": basis,
                "quote": re.sub(r"\s+", " ", context)[:400],
            }
        )
    return found


def harvest(
    client: CachedClient,
    candidates: pd.DataFrame,
    *,
    snapshot: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch each candidate filing and extract attested MW figures."""
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot) / "ceqanet_mw_evidence.csv"
    if dest.exists() and not refresh:
        return pd.read_csv(dest)

    rows = []
    for _, cand in candidates.iterrows():
        sch = str(cand["sch_number"])
        url = DETAIL_URL.format(sch=sch)
        try:
            html = client.fetch_text(url, suffix=".html")
        except Exception as exc:  # pragma: no cover
            log.debug("ceqanet detail %s failed: %s", sch, exc)
            continue
        text = re.sub(r"\s+", " ", HTMLParser(html).text())
        for hit in extract_mw(text):
            rows.append(
                {
                    "source": SOURCE,
                    "sch_number": sch,
                    "title": cand["title"],
                    "lead_agency": cand.get("lead_agency"),
                    "source_url": url,
                    "retrieved_at": snapshot,
                    **hit,
                }
            )

    df = pd.DataFrame(rows)
    df.to_csv(dest, index=False)
    log.info("ceqanet: %d MW figures extracted from %d filings", len(df), len(candidates))
    return df


def load(client: CachedClient, **kw) -> pd.DataFrame:
    return harvest(client, discover(client, **kw), **kw)
