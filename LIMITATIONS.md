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
| Tier C (area) / Tier B (generators), California | 17 | **0.59** | 0.38 – 1.08 |
| Tier C (area) / Tier B (generators), all 51 states | 35 | **1.04** | 0.49 – 1.95 |

Where both methods apply, the floor-area model returns about **half** what the
generator-fleet proxy returns *in California*, and about the *same* as it
returns nationally. The national figure is the one to trust as a general
statement about the two methods; the California one is the outlier, and it
drags the national median up to parity. At least one of the two is
systematically biased in California. Plausible causes, none yet resolved:

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

216 facilities are almost certainly fewer than California actually has, and the
2,605 facilities published for all 51 jurisdictions are far fewer than the
United States actually has.

- **OpenStreetMap is volunteer-mapped.** 114 raw features statewide, concentrated
  in the Bay Area and Los Angeles. Rural and inland facilities are underrepresented.
- **PeeringDB only lists interconnection facilities.** A single-tenant build-to-suit
  with no public peering will be absent.
- **EPA NEI only reaches permitted generators**, and NAICS is assigned at company
  level, which is why a generator-count gate is needed (see §5).
- **Facilities announced or built after the 2020 NEI vintage are missing** from
  the Tier B source entirely. Given how much California data center construction
  has been announced since 2020, this is a substantial and growing gap.

Only **87 of 216** facilities have footprint geometry in California, so Tier C
cannot be applied to the majority; **100 facilities have no power estimate at
all**. Nationally the picture is the same shape and worse in the thin states:
1,368 of 2,605 facilities carry a footprint, and 1,170 carry no power estimate.

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

- Without the exclusion list, **37 California facilities** would be attributed
  to the Power and Water Resource Pooling Authority, an agricultural water JPA,
  instead of PG&E — the San Jose, Sunnyvale, Mountain View and Palo Alto
  clusters, plus one City of Palo Alto site. A further 69 Santa Clara
  facilities have PWRPA among their candidates too, but Silicon Valley Power
  is smaller and wins on area even without the exclusion.
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
bottom-up figure of ~12.8 TWh/yr falls inside it, which is reassuring but weak
evidence: the range is wide, the apportionment is coarse, and agreement on a
total is compatible with large offsetting per-facility errors.

Nationally the same check comes out inside the summed band — 248.2 TWh/yr
against a 105–288 TWh/yr band — but §11c explains why that band is wide by
construction and therefore weak.

`--apply-calibration` will rescale modelled tiers to the anchor midpoint. It
**never** rescales Tier A rows, since altering a cited figure would be
falsification. Calibration is off by default and its application is recorded in
the output.

## 11. Multi-state generalisation carries its own caveats

The pipeline now runs for **all 50 states and the District of Columbia** via
`--state`, and `build-all` assembles a national roll-up from the published
per-state tables. Four things do not travel automatically:

* **Two enrichments are California-only.** No free, nationwide, per-state
  electric-utility-territory API was found, so `utility` is `null` for every
  state except CA (`states.StateProfile.has_utility_layer`). CEQAnet is a
  California-specific environmental-review database with no equivalent
  elsewhere, so `--with-ceqanet` is silently a no-op outside CA.
* **Every anchor is a rougher guess than CA's.** CA's anchor was set from a
  national report apportioned by rough population/market share; the other
  states' anchors (`states.py`) are the same kind of heuristic but were not
  independently cross-checked against a published state-level estimate. Each
  anchor carries an `anchor_note` stating its rationale so a reviewer can argue
  with the assumption rather than guess at it. The band is deliberately wide
  (0.6x–1.6x the point estimate) because a statewide data-centre energy figure
  is not known to better than a factor of two.

  Texas, Georgia and Virginia's bottom-up totals landed **outside** their
  anchor ranges on the first run, before the site-vs-building footprint fix
  described in §11e. Two explanations were equally plausible and neither was
  investigated further:

  1. The anchors are simply too low for these states. Virginia in particular is
     widely reported as the single largest concentration of hyperscale capacity
     in the world (Loudoun County "Data Center Alley"); a bottom-up figure of
     ~81 TWh/yr against a rough 30 TWh/yr guess is not obviously wrong.
  2. The Tier C floor-area model, calibrated on California building stock and
     California power-density priors, may not transfer cleanly to a different
     mix of building typologies (Virginia and Texas both show enormous
     single-storey hyperscale campuses that are individually much larger than
     any facility recorded in California).

  The footprint fix turned out to explain much of it: Texas and Georgia now
  land *inside* their bands (24.9 TWh/yr against [12, 34], and 10.8 against
  [4, 14]) because their largest campus polygons were site outlines rather
  than buildings. Virginia is still above its band (64.8 against [18, 48]),
  and the remaining above-band states are the ones with the biggest hyperscale
  footprints.

  No calibration was applied to force agreement in either direction. This is
  reported as an open question, not resolved by adjusting a prior until the
  numbers looked right.
* **The Tier B generator-count prior (median 2,116 kW/unit) was calibrated on
  California NEI records only** and applied unchanged to every other state's
  generator fleets. It was not re-derived per state, because most other states
  contributed too few parseable nameplate ratings to support a separate
  calibration (Texas contributed exactly one Tier B facility on this run).
* **The NEI-only generator-count scope gate (≥4 units)** was likewise derived
  from California's fleet-size distribution and applied unchanged elsewhere.

### 11a. Coverage is much thinner outside the six original states

The six states that were built and reviewed first (CA, TX, GA, AZ, IL, VA)
carry the overwhelming majority of the data. The other 45 jurisdictions were
extended later and inherit the same pipeline, but the *inputs* are far thinner
in them, for reasons that are structural rather than accidental:

* **OSM tagging is concentrated.** A state is only in the OSM output if
  somebody tagged its facilities `building=data_center` or `telecom=data_center`.
  States with active open-source mapping communities (and a large commercial
  colocation market) are well covered; states with neither return zero OSM
  elements. This is a *volunteer-mapping* artefact and must not be read as
  "there are no data centers in this state".
* **EPA NEI only reaches permitted generators, and the NAICS filter is
  coarse.** **Twenty-five** states have **zero** data-center NAICS units in
  NEI 2020 (AK, AR, AZ, CT, DC, GA, HI, ID, IN, LA, ME, MT, NC, ND, NH, NJ,
  NM, NY, RI, SD, UT, VT, WA, WI, WV), so they have no Tier B evidence at
  all. A further six have NEI units but no Tier B facility surviving the
  ≥4-unit scope gate (AL, FL, NE, NV, SC, TN), which leaves **31 of the 51**
  jurisdictions with no Tier B facility at all. This is a property of how
  NAICS is assigned — at the *company* level — not a claim that those states
  have no data centers.
* **PeeringDB lists interconnection facilities only.** A single-tenant
  build-to-suit with no public peering is absent everywhere.
* **Tier C needs an OSM footprint polygon.** A state with thin OSM coverage
  gets thin Tier C coverage, because "no polygon" is recorded as no
  measurement rather than as zero area. And a polygon only counts if it is a
  *building*: a campus mapped as a site outline yields no footprint and no
  Tier C estimate at all (§11e).

A state with a small facility count in this dataset is therefore *a statement
about the sources*, not about the state. Treat the 45 newer jurisdictions as
lower bounds on the real inventory.

### 11b. Equal-area projection is per state, and the exceptions matter

Areas and distances are computed in **EPSG:5070** (CONUS Albers), an
equal-area projection with metre units, for the continental states. Equal-area
projections preserve *area* everywhere, so the footprint-area model is
unaffected by this choice; but *distances* are distorted outside a
projection's domain, and distance is what the entity-resolution merge radius
uses. Measured worst-case distance error inside Alaska is **12.8%** under
EPSG:5070, against **0.3%** under EPSG:3338 (NAD83 / Alaska Albers); for
Hawaii the CONUS Albers gives **3.2%**, against **0.03%** for a Lambert
azimuthal equal-area projection centred on the archipelago. Alaska and Hawaii
therefore carry their own `equal_area_crs`; every other state uses the CONUS
Albers. The per-state value lives in `states.StateProfile.equal_area_crs`.

### 11c. The national anchor band is wide by construction

`data/processed/national/reconciliation.json` compares the national bottom-up
total against the *sum* of the 51 per-state bands. Each band is an
order-of-magnitude heuristic, so the union of 51 wide bands is very wide, and
agreement with it is weak evidence. It is a smell test, not a measurement.

The current figures are **248.2 TWh/yr bottom-up** against a summed band of
**104.8–288.4 TWh/yr** — inside it, but only just. The band is 1.8x wide, and
the bottom-up figure sits at 78% of the way up it. Two things move that number
a long way: the site-vs-building footprint fix of §11e moved it by **-47%**
(national 472.7 → 248.2 TWh/yr), and the thin-coverage states of §11a would
move it the other way if their sources improved. Treat the national total as
"the sum of what the sources can currently see", not as a national inventory.

The national total is also only as good as its parts, and 31 of the 51
jurisdictions contribute no Tier B evidence at all. A national figure assembled
from 51 partial inventories is a partial inventory; it does not average out.

### 11d. PeeringDB's `state` filter is a search, not a lookup

The obvious way to collect PeeringDB per state is one
`/api/fac?state=XX` query per state. That is wrong twice over, and both faults
were found in the live data rather than reasoned about in advance.

**The `state` parameter is a full-text search, not an exact match.** It matches
across the facility *and* organisation records, so a per-state query returns
facilities from other states. In the 2026-10-21 snapshot:

| Query | Returned | Actually in the state |
|---|---|---|
| `state=CA` | 151 | 148 |
| `state=AR` | 8 | 5 |
| `state=CO` | 33 | 31 |

The three intruders are QTS Charlotte (CTL1), AceHost Durham and Cogent
Fairfax — all recorded with a full state *name* rather than a postal code, and
all in North or South Carolina. Cogent Fairfax was also returned by
`state=CO`, because "**Co**gent" contains the letters `co`. The pipeline's
bounding-box gate caught these downstream, but only after they had already
been written into the per-state raw snapshots.

**The anonymous API quota does not allow 51 queries.** PeeringDB throttles
unauthenticated callers to roughly 13 requests per hour, and answers a
throttled request with `429` and a `retry-after` measured in tens of minutes.
Querying state by state took hours and failed outright.

The whole US facility set is **1,378 records** (about 1.3 MB) and fits in a
single request, well under the API's 5,000-record page cap. The pipeline now
fetches the country once per snapshot
(`data/raw/peeringdb/_US/<snapshot>/peeringdb_fac_us.json`) and splits it
locally. That removes the cross-contamination at the source, costs one request
instead of 51, and puts every state on a single timestamp — which is what
makes the national roll-up internally consistent.

Assigning a facility to a state is a documented, ordered fallback
(`sources/peeringdb.assign_state`): postal code → full state name →
state-prefixed ZIP (`"FL 33487"`) → ZIP prefix → geographic containment in a
state bounding box. A facility that cannot be placed is left unassigned rather
than guessed into a neighbouring state.

Three consequences are worth stating plainly:

* **Nine of the 1,378 records have an empty `state` field.** The ZIP code
  resolves all nine exactly. The bounding boxes cannot: the boxes are
  deliberately generous, so Cheyenne, Wyoming falls inside both the Wyoming and
  the Colorado box, and the nearest-centroid tiebreak gets it wrong. The ZIP
  prefix table (`states.ZIP3_RANGES`, the standard USPS 3-digit allocation) is
  therefore what resolves it. The same table is what rejects PeeringDB's Tokyo
  facility, whose `zipcode` is the Japanese `135-0061` — whose first three
  digits fall inside a New York range.
* **The facility count per state is not the same as a naive
  `state=XX` query would give.** Seven records spell the state out
  ("South Carolina"); under the old query they were returned for the wrong
  states and then discarded by the bounding-box gate, so they never reached
  the published tables. They are now in the tables, in the state they belong
  to.
* **Puerto Rico is out of scope.** Two PeeringDB records are filed under `PR`
  and are not assigned to any of the 51 jurisdictions, so Puerto Rico's data
  centres are absent from this dataset entirely. The same is true of the one
  Tokyo record.

### 11e. An OSM polygon is not automatically a building

This one changed published numbers materially, so it is worth stating in full.

OpenStreetMap has two ways to map the same thing. A data centre may be a
**building** (`building=data_center`, sometimes `building=commercial` or
`building=warehouse` on a purpose-built shell), or it may be the **site** that
contains the buildings (`landuse=industrial`, a large polygon drawn around the
campus including the grounds, the parking and any undeveloped land).

The pipeline originally treated *any* polygon as a building footprint. That is a
category error, and it is expensive, because the Tier C model multiplies the
footprint by the storey count and by the white-space fraction.

Measured on the live data, the buildings that OSM *also* maps inside these site
polygons cover only a fraction of the site area:

| Campus | Site polygon | Mapped buildings inside | Their share of the site |
| --- | --- | --- | --- |
| Google, Lockbourne (OH) | `landuse=industrial` | 1 | 2.5% |
| Google, Jackson County (AL) | `landuse=industrial` | 2 | 3.8% |
| Microsoft, Texas Research Park (TX) | `landuse=industrial` | 6 | 16.4% |
| Meta, Stanton Springs (GA) | `landuse=industrial` | 3 | 19.6% |
| Amazon Web Services, Loudoun (VA) | `landuse=industrial` | 3 | 41.1% |
| Digital Loudoun Plaza (VA) | `landuse=industrial` | 8 | 39.9% |
| Centersquare Ashburn IAD4 (VA) | `landuse=None` | 2 | 59.2% |
| Texas Cryptologic Center (TX) | `landuse=None` | 3 | 68.8% |

Across the 75 site polygons in the corpus, 37 have at least one mapped building
fully inside them, and those buildings cover **2.5% to 68.8%** of the site
area, with a **median of 29.9%** and a mean of 30.7%. The remaining 38 site
polygons have **no** mapped building inside them at all. So a site polygon used
as a footprint overstates the building area by roughly **3x** on average, and
the Tier C power estimate inherits that factor.

**The fix.** `osm.parse` now records whether the element carries a `building`
tag (`is_building`), and `normalize.geometry.add_footprint_area` measures an
area only for a record whose polygon is a building. A site polygon yields a
**null** footprint, not a small one and not a zero.

**What this costs.** A facility whose only OSM geometry is a site polygon now
has no measured footprint, and therefore **no Tier C estimate**. It keeps its
location, its name, its operator and any Tier B generator evidence; it simply
loses the floor-area estimate. `facility_footprints.geojson` contains only
polygons that carry a measured area, so the published footprint file no longer
contains site outlines.

**How much it moved the numbers.** Rebuilding all 51 states from the same
snapshots with and without the fix:

| | before | after | change |
| --- | --- | --- | --- |
| Facilities | 2,605 | 2,605 | 0 |
| Facilities with a measured footprint | 1,434 | 1,368 | −66 |
| Bottom-up IT load | 54,269 MW | 28,264 MW | **−47.9%** |
| Bottom-up annual energy | 472.7 TWh/yr | 248.2 TWh/yr | **−47.5%** |

**No facility was lost** — the count is identical — but 19 of the 51 states
lost power, and the largest single moves were:

| State | before | after | change |
| --- | --- | --- | --- |
| Tennessee (TN) | 7,423 MW | 620 MW | −6,803 |
| Texas (TX) | 7,210 MW | 2,805 MW | −4,405 |
| Virginia (VA) | 10,233 MW | 7,448 MW | −2,785 |
| Ohio (OH) | 4,687 MW | 1,923 MW | −2,764 |
| Indiana (IN) | 2,026 MW | 192 MW | −1,834 |
| Georgia (GA) | 2,952 MW | 1,227 MW | −1,725 |
| Alabama (AL) | 1,964 MW | 371 MW | −1,593 |
| New Jersey (NJ) | 2,062 MW | 823 MW | −1,240 |
| South Carolina (SC) | 1,337 MW | 176 MW | −1,161 |

The pattern is consistent: a single enormous site polygon was carrying the
whole state's Tier C total. Microsoft's Texas Research Park campus alone fell
from 1,043 MW to 33 MW once the 672,738 m² `landuse=industrial` parcel stopped
counting as a building; Meta's Stanton Springs campus fell from 1,301 MW to
nothing, because OSM maps its site polygon and its three buildings as separate
elements and only the site polygon carried a usable area.

**What this does not fix.** Where OSM maps both the site *and* the individual
buildings inside it, the buildings are separate source records; entity
resolution merges them into one facility and the published footprint is the
**largest** measured building in the cluster, not the sum of them
(`normalize.dedupe.resolve` takes `group.footprint_sqm.max()`). A three-building
campus is therefore credited with one building's area. That is an
*under*-estimate, and it is the opposite error to the one this section
describes. It was left alone deliberately: changing `max` to `sum` is a change
to the estimation model, not a fix to a measurement bug, and this project's
methodology treats such changes as needing their own justification and
validation.

## 12. Licensing constrains redistribution

OpenStreetMap geometry makes this a derived database under **ODbL-1.0**:
attribution *and* share-alike apply. Rebuilding without the OSM source removes
that obligation but eliminates all footprint geometry and therefore all Tier C
estimates.
