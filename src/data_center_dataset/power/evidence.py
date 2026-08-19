"""Tier A -- attested power figures.

Highest precedence, and the only tier whose numbers are *observations* rather
than inferences. Two inputs feed it:

* ``data/reference/manual_overrides.csv`` -- curated figures, each carrying a
  URL, retrieval date and verbatim quote.
* CEQAnet extractions, when that opt-in source is enabled.

The published-table contract in ``normalize.schema`` rejects any Tier A row
lacking a citation, so an uncited figure cannot silently acquire the authority
of attested evidence.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from ..config import (
    CRITICAL_TO_IT,
    HOURS_PER_YEAR,
    REFERENCE_DIR,
    TIER_ATTESTED,
    UTILIZATION,
)

log = logging.getLogger(__name__)

#: Converts a stated figure of a given basis into IT load. Total facility power
#: includes cooling and losses, so it must be divided by an assumed PUE; that
#: happens in ``model.py`` where the PUE priors live. Here we only handle the
#: electrical-chain step.
_BASIS_TO_IT = {
    "it_load": 1.0,
    "critical_load": CRITICAL_TO_IT,
    # total_facility is handled separately because it needs a PUE assumption.
}


def _match_facilities(facilities: pd.DataFrame, name: str, operator: str | None) -> pd.Series:
    mask = facilities.name.str.contains(str(name), case=False, na=False, regex=False)
    if operator and not pd.isna(operator):
        mask &= facilities.operator.fillna("").str.contains(
            str(operator), case=False, na=False, regex=False
        )
    return mask


def from_manual_overrides(facilities: pd.DataFrame, pue_lookup) -> pd.DataFrame:
    """Load curated Tier A rows and attach them to facilities by name match."""
    path = REFERENCE_DIR / "manual_overrides.csv"
    if not path.exists():
        return pd.DataFrame()

    table = pd.read_csv(path, comment="#")
    if table.empty:
        log.info("tier A: no curated overrides present")
        return pd.DataFrame()

    rows = []
    for _, ov in table.iterrows():
        if pd.isna(ov.get("value_mw")) or pd.isna(ov.get("source_url")):
            log.warning("tier A: skipping override without value or citation: %s", ov.to_dict())
            continue
        mask = _match_facilities(facilities, ov["match_name"], ov.get("match_operator"))
        matched = facilities[mask]
        if matched.empty:
            log.warning("tier A: override matched no facility: %r", ov["match_name"])
            continue
        if len(matched) > 1:
            log.warning(
                "tier A: override %r matched %d facilities; applying to all",
                ov["match_name"],
                len(matched),
            )
        for _, fac in matched.iterrows():
            rows.append(_build_row(fac, ov, pue_lookup))
    return pd.DataFrame(rows)


def from_ceqanet(
    facilities: pd.DataFrame, evidence: pd.DataFrame, pue_lookup
) -> pd.DataFrame:
    """Attach CEQAnet-extracted figures by fuzzy title/name overlap.

    Matching is deliberately conservative: a CEQA project title must share a
    distinctive token with the facility name. Unmatched figures are retained in
    the standalone ``power_evidence`` export so the extraction is not lost.
    """
    if evidence is None or evidence.empty:
        return pd.DataFrame()

    rows = []
    for _, ev in evidence.iterrows():
        title = str(ev.get("title") or "")
        tokens = {
            t
            for t in "".join(c if c.isalnum() else " " for c in title.lower()).split()
            if len(t) > 4
        }
        if not tokens:
            continue
        best = None
        for _, fac in facilities.iterrows():
            fac_tokens = {
                t
                for t in "".join(
                    c if c.isalnum() else " " for c in str(fac["name"]).lower()
                ).split()
                if len(t) > 4
            }
            overlap = tokens & fac_tokens
            if overlap and (best is None or len(overlap) > best[0]):
                best = (len(overlap), fac)
        if best is None:
            continue
        rows.append(
            _build_row(
                best[1],
                {
                    "basis": ev.get("basis", "total_facility"),
                    "value_mw": ev["value_mw"],
                    "source_url": ev["source_url"],
                    "retrieved_at": ev.get("retrieved_at"),
                    "quote": ev.get("quote"),
                },
                pue_lookup,
            )
        )
    return pd.DataFrame(rows)


def _build_row(facility: pd.Series, evidence: dict | pd.Series, pue_lookup) -> dict:
    """Convert an attested figure of any basis into a Tier A estimate row."""
    basis = str(evidence.get("basis") or "total_facility")
    value = float(evidence["value_mw"])
    pue = pue_lookup(facility)

    if basis == "it_load":
        it_load = value
    elif basis == "critical_load":
        it_load = value * CRITICAL_TO_IT
    else:  # total_facility
        it_load = value / pue["mid"]

    # Attested values carry real but non-zero uncertainty: the basis is often
    # ambiguous in the source, so allow a modest band rather than claiming
    # a point measurement.
    return {
        "facility_id": facility["facility_id"],
        "method": TIER_ATTESTED,
        "basis": basis,
        "stated_value_mw": value,
        "it_load_mw": it_load,
        "ci_low_mw": it_load * 0.85,
        "ci_high_mw": it_load * 1.15,
        "annual_gwh": it_load * pue["mid"] * UTILIZATION * HOURS_PER_YEAR / 1000.0,
        "pue_used": pue["mid"],
        "source_url": evidence.get("source_url"),
        "retrieved_at": evidence.get("retrieved_at"),
        "quote": evidence.get("quote"),
        "assumptions_json": json.dumps(
            {
                "basis": basis,
                "stated_value_mw": value,
                "critical_to_it": CRITICAL_TO_IT if basis == "critical_load" else None,
                "pue_mid": pue["mid"],
                "utilization": UTILIZATION,
                "note": "Attested figure; band reflects ambiguity in stated basis.",
            },
            sort_keys=True,
        ),
    }
