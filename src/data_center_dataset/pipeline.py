"""End-to-end pipeline orchestration.

Sequence: ingest -> classify -> resolve -> enrich -> estimate power -> reconcile
-> export. Each stage is a pure-ish function over DataFrames so that stages can
be tested in isolation and inspected between runs.

Every stage takes an explicit ``state`` (a two-letter postal code resolved
against :mod:`data_center_dataset.states`), which drives the OSM/PeeringDB/NEI
queries, the geographic sanity gate, and the reconciliation anchor. Two
enrichments -- CEC utility territories and CEQAnet -- are California-only and
are skipped automatically for every other state (see ``states.py`` for why no
free equivalent exists).
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from . import export as export_mod
from .config import EQUAL_AREA_CRS, ensure_dirs, processed_dir
from .http import CachedClient
from .normalize import classify, dedupe, geometry
from .normalize.schema import conform_source_frame
from .power import evidence, generators, model, reconcile
from .sources import cec_gis, ceqanet, epa_nei, osm, peeringdb
from .states import get as get_state_profile

log = logging.getLogger(__name__)


def ingest(
    client: CachedClient,
    *,
    state: str = "CA",
    snapshot: str | None = None,
    refresh: bool = False,
    with_ceqanet: bool = False,
) -> dict:
    """Fetch and normalize every enabled source for ``state``."""
    profile = get_state_profile(state)
    snapshot = snapshot or date.today().isoformat()
    kw = {"state": state, "snapshot": snapshot, "refresh": refresh}

    frames: list[pd.DataFrame] = []

    log.info("--- ingest: OpenStreetMap (%s)", profile.name)
    frames.append(osm.load(client, **kw))

    log.info("--- ingest: PeeringDB (%s)", profile.name)
    frames.append(peeringdb.load(client, **kw))

    log.info("--- ingest: EPA NEI 2020 (%s -> %s)", profile.name, profile.nei_region_file)
    nei_records, generator_inventory = epa_nei.load(client, **kw)
    frames.append(nei_records)

    records = conform_source_frame(pd.concat(frames, ignore_index=True))

    ceqa_evidence = pd.DataFrame()
    if with_ceqanet and profile.supports_ceqanet:
        log.info("--- ingest: CEQAnet (opt-in, low recall, CA only)")
        try:
            ceqa_evidence = ceqanet.load(client, snapshot=snapshot, refresh=refresh)
        except Exception as exc:
            log.warning("ceqanet ingest failed, continuing without it: %s", exc)
    elif with_ceqanet:
        log.info("--with-ceqanet requested but %s has no CEQA equivalent; skipping", profile.name)

    utilities = None
    if profile.has_utility_layer:
        log.info("--- ingest: CEC utility territories (CA only)")
        try:
            utilities = cec_gis.fetch_utility_territories(
                client, snapshot=snapshot, refresh=refresh
            )
        except Exception as exc:
            log.warning("CEC GIS unavailable, utility column will be null: %s", exc)
    else:
        log.info(
            "no free statewide utility-territory layer for %s; utility column will be null",
            profile.name,
        )

    log.info("ingest complete: %d raw source records", len(records))
    return {
        "state": profile.code,
        "records": records,
        "generator_inventory": generator_inventory,
        "ceqa_evidence": ceqa_evidence,
        "utilities": utilities,
        "snapshot": snapshot,
    }


def build(
    ingested: dict, *, apply_calibration: bool = False
) -> dict:
    """Transform ingested records into the published tables."""
    profile = get_state_profile(ingested.get("state", "CA"))
    records = ingested["records"]

    # Areas and distances are measured in an equal-area projection. The
    # CONUS Albers is right for the continental states; Alaska and Hawaii
    # carry their own, because distances (unlike areas) are distorted
    # outside the CONUS Albers domain.
    crs = profile.equal_area_crs or EQUAL_AREA_CRS

    # -- footprint area must precede dedupe: the resolver prefers the record
    #    with the largest measured footprint as the cluster anchor.
    log.info("--- geometry: footprint areas")
    records = geometry.add_footprint_area(records, crs=crs)

    log.info("--- classify: scope filter (%s)", profile.name)
    in_scope, excluded = classify.apply(
        records, bbox=profile.bbox, extra_bboxes=profile.extra_bboxes
    )

    log.info("--- resolve: entity resolution")
    facilities, crosswalk, review = dedupe.resolve(in_scope, crs=crs)

    log.info("--- scope gate: NEI-only generator fleet threshold")
    facilities, gated = classify.post_resolution_gate(
        facilities, crosswalk, ingested.get("generator_inventory")
    )
    crosswalk = crosswalk[crosswalk.facility_id.isin(facilities.facility_id)]
    if not gated.empty:
        excluded = pd.concat([excluded, gated], ignore_index=True)

    log.info("--- geometry: gross floor area")
    facilities = geometry.estimate_gross_area(facilities)

    if ingested.get("utilities") is not None:
        log.info("--- enrich: utility service territory")
        facilities = geometry.assign_utility(facilities, ingested["utilities"])
    else:
        facilities["utility"] = None
        facilities["utility_candidates"] = None

    # -- power ---------------------------------------------------------------
    pue_lookup = model.make_pue_lookup()
    estimate_frames = []

    log.info("--- power: tier A (attested)")
    tier_a = evidence.from_manual_overrides(facilities, pue_lookup)
    if not tier_a.empty:
        estimate_frames.append(tier_a)
    ceqa_a = evidence.from_ceqanet(facilities, ingested.get("ceqa_evidence"), pue_lookup)
    if not ceqa_a.empty:
        estimate_frames.append(ceqa_a)

    log.info("--- power: tier B (generator fleet)")
    tier_b = generators.estimate(
        facilities, ingested.get("generator_inventory"), crosswalk, pue_lookup
    )
    if not tier_b.empty:
        estimate_frames.append(tier_b)

    log.info("--- power: tier C (floor area)")
    tier_c = model.estimate(facilities)
    if not tier_c.empty:
        estimate_frames.append(tier_c)

    estimates = (
        pd.concat(estimate_frames, ignore_index=True)
        if estimate_frames
        else pd.DataFrame(columns=["facility_id", "method", "basis", "it_load_mw"])
    )

    log.info("--- power: resolve tiers")
    resolved = reconcile.resolve(estimates)
    facilities = facilities.merge(resolved, on="facility_id", how="left")

    # Surface the modelled white-space figure where Tier C produced one.
    if not tier_c.empty:
        ws = tier_c[["facility_id", "est_white_space_sqft"]].drop_duplicates("facility_id")
        facilities = facilities.merge(ws, on="facility_id", how="left")
    else:
        facilities["est_white_space_sqft"] = pd.NA

    facilities["footprint_sqft"] = pd.to_numeric(
        facilities.get("footprint_sqft"), errors="coerce"
    )

    log.info("--- reconcile: top-down check (%s)", profile.name)
    facilities, report = reconcile.reconcile(
        facilities,
        apply_calibration=apply_calibration,
        annual_twh_anchor=profile.annual_twh_anchor,
        annual_twh_anchor_range=profile.annual_twh_anchor_range,
    )
    agreement = reconcile.agreement_report(estimates)

    return {
        "state": profile.code,
        "facilities": facilities,
        "power_estimates": estimates,
        "crosswalk": crosswalk,
        "excluded": excluded,
        "review_queue": review,
        "reconciliation": report,
        "tier_agreement": agreement,
        "snapshot": ingested.get("snapshot"),
    }


def run(
    *,
    state: str = "CA",
    snapshot: str | None = None,
    refresh: bool = False,
    with_ceqanet: bool = False,
    apply_calibration: bool = False,
) -> dict:
    """Fetch, build and export in one call for ``state``."""
    ensure_dirs()
    with CachedClient() as client:
        ingested = ingest(
            client,
            state=state,
            snapshot=snapshot,
            refresh=refresh,
            with_ceqanet=with_ceqanet,
        )
    built = build(ingested, apply_calibration=apply_calibration)
    export_mod.write_all(built, outdir=processed_dir(state))
    return built
