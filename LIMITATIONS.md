# Limitations

Read this before using the dataset for anything consequential.

## 1. No power figure here is a measurement

Per-facility electricity consumption for California data centers is not public.
It sits with utilities as confidential customer data. Every commercial source
that claims to publish it (Baxtel, DC Byte, Uptime, 451) is paywalled, and the
free sources contain no power field at all.

So `best_power_mw` is **not metered consumption**. It is either a third party's
claim (Tier A) or this project's model output (Tiers B and C). The column exists
because a bounded estimate with an honest interval is more useful than a null —
but treating it as measured load would be a category error.

`est_annual_gwh` compounds this: it multiplies a modelled IT load by a *prior*
PUE and a *prior* utilization factor. Its uncertainty is strictly wider than the
IT load's.

## 2. Independent methods disagree by roughly 2x

This is the most important quality signal in the dataset, and it is published
rather than hidden. From `tier_agreement.csv`:

| Comparison | Facilities | Median ratio | IQR |
| --- | --- | --- | --- |
| Tier C (area) / Tier B (generators) | 14 | **0.56** | 0.34 – 0.97 |

Where both methods apply, the floor-area model returns about **half** what the
generator-fleet proxy returns. At least one is systematically biased. Plausible
causes, none yet resolved:

- **Tier B may overestimate.** The redundancy divisor is set to 1.30 (N+1). Much
  California colocation is built 2N, which would imply a divisor near 2.0 and
  would cut Tier B by ~35%.
- **Tier C may underestimate.** The 0.60 white-space fraction, or the W/sqft
  priors, may be too low for dense Santa Clara facilities. Colovore, for
  instance, operates well above typical rack densities.
- **Footprints may be incomplete.** An OSM polygon may cover one building of a
  multi-building campus.

A related error was caught and fixed rather than shipped: crediting every storey
of a high-rise gave One Wilshire's tenant Multacom 1.3 million sqft and 150 MW,
making it the largest facility in the dataset at roughly five times the entire
building's actual critical load. Storeys are now capped at three and such records
are flagged `partial_occupancy`. Their occupied share remains genuinely unknown,
which is why their intervals are the widest in the dataset.

The priors were deliberately **not** tuned to force agreement. Fitting them to
each other would manufacture false confidence while destroying the independence
that makes the comparison informative. Treat a factor of two as the practical
precision of any single facility's power figure.

## 3. Coverage is incomplete, and unevenly so

221 facilities are almost certainly fewer than California actually has.

- **OpenStreetMap is volunteer-mapped.** 114 raw features statewide, concentrated
  in the Bay Area and Los Angeles. Rural and inland facilities are underrepresented.
- **PeeringDB only lists interconnection facilities.** A single-tenant build-to-suit
  with no public peering will be absent.
- **EPA NEI only reaches permitted generators**, and NAICS is assigned at company
  level, which is why a generator-count gate is needed (see §5).
- **Facilities announced or built after the 2020 NEI vintage are missing** from
  the Tier B source entirely. Given how much California data center construction
  has been announced since 2020, this is a substantial and growing gap.

Only **87 of 221** facilities have footprint geometry, so Tier C cannot be applied
to the majority. **103 facilities have no power estimate at all.**

## 4. Tier A is empty

No curated attested figure shipped, because none could be independently verified
at build time, and inventing citations would be worse than an empty column.

CEQAnet, the only free automatable route to attested MW, is structurally
low-recall:

- The CEQA database has **no free-text keyword search**. The only text search on
  the site is a Google Custom Search wrapper needing an API key.
- The database endpoint **caps results at 100 rows and ignores page parameters**,
  forcing enumeration by narrow date windows.
- Project titles routinely describe a data center without saying so — "North Watt
  Avenue Specific Plan" is a real example — so title filtering misses them.

It is therefore opt-in (`--with-ceqanet`) and should not be expected to find much.

## 5. Scope boundaries are judgement calls

Scope is *commercial* colocation, wholesale and hyperscale. Enforcing that
requires decisions that are defensible but arguable, all logged in
`exclusions.csv` with the rule that fired:

- **Telco central offices are excluded**, unless the site appears in PeeringDB —
  presence there means the carrier sells colocation and interconnection at that
  address. A Lumen central office is out; a Lumen PeeringDB facility is in.
- **Enterprise and institutional sites are excluded** (Cisco campuses, Kaiser
  Permanente, university computer rooms) even when large. A hyperscaler's own
  data center *is* in scope, since it is purpose-built capacity at scale.
- **NEI-only records need ≥4 permitted generators.** NEI assigns NAICS at company
  level, so Google's Santa Barbara and San Bruno offices arrive tagged as data
  centers alongside its Mountain View facility. The California fleet-size
  distribution splits cleanly — 43 facilities have exactly one permitted
  generator (banks, Verizon Wireless MTSOs, hyperscaler offices) — but the
  threshold will drop genuinely small colocation sites that no second source
  corroborates.

## 6. Entity resolution is imperfect by design

`dedupe_review.csv` holds **128 pairs** that scored in the uncertain band and were
*not* merged. Silent over-merging is the worse failure, so the resolver stays
conservative.

Carrier hotels are the hard case. One Wilshire and 624 South Grand in Los Angeles
host many distinct PeeringDB facilities at identical coordinates under different
operators. Distance-based merging would wrongly collapse them; the resolver keeps
them separate and relies on operator disagreement.

Two hard cannot-link constraints are enforced across transitive merges:

- **Distinct site codes** — CoreSite SV3 and SV7 sit 283 m apart and are separate
  buildings. Plain Union-Find merged them through SV4 before this was added.
- **Distinct street numbers** — Digital Realty's 1201, 1525 and 1725 Comstock
  Street facilities plus 1100 and 1500 Space Park collapsed into a single record
  before this was added.

Residual known issues: some campuses that *should* be one record remain split
(the review queue is the place to look), and two OSM ways both named "Csquare
SFO1" did not merge with each other.

## 7. Fields that were planned and dropped

- **Distance to nearest transmission substation.** No reliable free substation
  layer could be verified: the CEC `Transmission_Line` service exposes no
  queryable fields and the HIFLD endpoints tested were dead. Shipping a
  silently-empty column would be worse than omitting it.
- **Cooling water consumption.** Not disclosed per facility anywhere public.
- **County via spatial join.** The CEC `California_Counties` layer holds centroids,
  not polygons. County is taken from source fields where present.

## 8. Utility attribution is a rule, not a fact

The CEC territory layer contains **overlapping** polygons, so attribution needs a
tie-break. The rule applied is: discard non-retail overlays listed in
`data/reference/utility_overlay_exclusions.csv`, then take the smallest remaining
containing territory.

Two failure modes this fixes, both observed in the raw join:

- Without the exclusion list, 73 facilities — the whole Santa Clara cluster —
  were attributed to the Power and Water Resource Pooling Authority, an
  agricultural water JPA, instead of Silicon Valley Power.
- Smallest-area alone is also wrong, because PWRPA (12,656 km²) is smaller than
  PG&E (180,702 km²) and would win in rural areas.

The judgement calls are visible and arguable. San Francisco commercial load is
assigned to PG&E rather than Hetch Hetchy Water and Power on the grounds that
Hetch Hetchy serves municipal facilities; a facility with a special municipal
arrangement would be misattributed. **Check `utility_candidates`**, which lists
every overlapping territory, before relying on the single value.

Attribution reflects *geography*, not contracts. Direct access, community choice
aggregation and behind-the-meter generation are not represented.

## 9. Vintage and staleness

- EPA NEI: **2020**. The generator inventory is five years stale and misses all
  post-2020 construction.
- OSM and PeeringDB: live at snapshot date.
- CEC utility territories: as published.

Snapshot dates are recorded in `data/raw/<source>/<date>/` and in
`datapackage.json`.

## 10. The statewide anchor is an order-of-magnitude check only

`reconciliation.json` compares the bottom-up total against a 12–26 TWh/yr range
for California, apportioned from LBNL's 2024 national report. The current
bottom-up figure of ~13.3 TWh/yr falls inside it, which is reassuring but weak
evidence: the range is wide, the apportionment is coarse, and agreement on a
total is compatible with large offsetting per-facility errors.

`--apply-calibration` will rescale modelled tiers to the anchor midpoint. It
**never** rescales Tier A rows, since altering a cited figure would be
falsification. Calibration is off by default and its application is recorded in
the output.

## 11. Licensing constrains redistribution

OpenStreetMap geometry makes this a derived database under **ODbL-1.0**:
attribution *and* share-alike apply. Rebuilding without the OSM source removes
that obligation but eliminates all footprint geometry and therefore all Tier C
estimates.
