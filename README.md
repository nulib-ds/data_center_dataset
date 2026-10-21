# US Data Center Dataset

A reproducible pipeline that assembles a registry of **commercial data centers**
— colocation, wholesale and hyperscale — from public sources, and attaches
**power estimates that carry an explicit evidence tier and confidence
interval**. Runs for **every US state and the District of Columbia** via
`--state`; the California build received the most validation.

> **Read this first.** No figure in this dataset is a metered electricity
> reading. Per-facility consumption is confidential utility data and is not
> publicly available for any US data center. Every power number here is
> either a *cited third-party claim* or a *model output*, and each is labelled as
> such. See [LIMITATIONS.md](LIMITATIONS.md).

## Supported states

All 51 jurisdictions — the 50 states plus Washington, DC — are supported.

```bash
uv run data-center-dataset build --state TX   # any of the 51 codes
uv run data-center-dataset build-all          # every supported state, one after another
uv run data-center-dataset build-all --states CA,TX,VA,OH   # a subset
uv run data-center-dataset national           # re-assemble the national roll-up
```

Each state writes to its own `data/processed/<STATE>/` directory, and
`build-all` finishes by concatenating the published per-state tables into
`data/processed/national/` (see [National roll-up](#national-roll-up)).

Two enrichments are California-only because no free nationwide equivalent exists
(see `states.py` for the reasoning): CEC electric-utility territories, and the
CEQAnet attested-MW crawl. Every other source (OSM, PeeringDB, EPA NEI) is
queried per state. The EPA NEI archive is national and shared: one 157 MB
download serves all 51 states, and its ten EPA-region members are scanned once
per region rather than once per state.

## Results

All 51 jurisdictions were built from the same pipeline on the 2026-10-21
snapshots. The full machine-readable version is
`data/processed/national/summary.csv`.

| State | Facilities | With power est. | Sum IT MW | Bottom-up TWh/yr | vs. anchor |
| --- | --- | --- | --- | --- | --- |
| Virginia (VA) | 418 | 365 | ~7,448 | ~64.8 | **above** [18, 48] |
| Texas (TX) | 271 | 176 | ~2,805 | ~24.9 | within [12, 34] |
| Oregon (OR) | 116 | 82 | ~1,978 | ~17.2 | **above** [1, 2] |
| Ohio (OH) | 116 | 57 | ~1,923 | ~16.3 | **above** [4, 11] |
| California (CA) | 216 | 116 | ~1,424 | ~12.8 | within [12, 26] |
| Illinois (IL) | 103 | 55 | ~1,400 | ~12.6 | within [4, 15] |
| Georgia (GA) | 80 | 39 | ~1,227 | ~10.8 | within [4, 14] |
| Iowa (IA) | 55 | 32 | ~1,120 | ~9.7 | **above** [1, 2] |
| Arizona (AZ) | 67 | 36 | ~1,107 | ~9.8 | within [4, 16] |
| New Jersey (NJ) | 84 | 47 | ~823 | ~7.4 | **above** [2, 6] |
| Nebraska (NE) | 29 | 20 | ~767 | ~6.6 | **above** [1, 2] |
| Washington (WA) | 102 | 58 | ~741 | ~6.6 | within [3, 8] |
| Tennessee (TN) | 41 | 14 | ~620 | ~5.4 | **above** [1, 3] |
| Wyoming (WY) | 37 | 24 | ~486 | ~4.3 | **above** [0, 1] |
| Nevada (NV) | 33 | 26 | ~472 | ~4.3 | **above** [1, 3] |
| Missouri (MO) | 41 | 18 | ~422 | ~3.7 | **above** [1, 2] |
| Utah (UT) | 34 | 17 | ~410 | ~3.6 | within [2, 4] |
| Alabama (AL) | 15 | 10 | ~371 | ~3.2 | **above** [1, 2] |
| North Carolina (NC) | 47 | 16 | ~370 | ~3.3 | within [2, 6] |
| Colorado (CO) | 47 | 26 | ~325 | ~2.9 | within [2, 6] |
| Mississippi (MS) | 9 | 8 | ~259 | ~2.3 | **above** [0, 1] |
| Minnesota (MN) | 47 | 15 | ~203 | ~1.8 | within [2, 4] |
| Indiana (IN) | 30 | 10 | ~192 | ~1.7 | within [1, 3] |
| South Carolina (SC) | 12 | 6 | ~176 | ~1.5 | within [1, 3] |
| New York (NY) | 90 | 20 | ~157 | ~1.4 | **below** [4, 11] |
| Pennsylvania (PA) | 80 | 41 | ~133 | ~1.2 | **below** [2, 6] |
| New Mexico (NM) | 14 | 5 | ~113 | ~1.0 | within [0, 1] |
| Michigan (MI) | 32 | 9 | ~111 | ~1.0 | **below** [1, 3] |
| Florida (FL) | 73 | 11 | ~103 | ~0.9 | **below** [4, 10] |
| Massachusetts (MA) | 40 | 9 | ~101 | ~0.9 | **below** [2, 6] |
| Oklahoma (OK) | 19 | 6 | ~84 | ~0.7 | within [1, 2] |
| Idaho (ID) | 11 | 4 | ~81 | ~0.7 | within [0, 1] |
| Maryland (MD) | 22 | 8 | ~63 | ~0.6 | **below** [2, 4] |
| Kansas (KS) | 15 | 8 | ~54 | ~0.5 | **below** [1, 2] |
| Wisconsin (WI) | 27 | 9 | ~51 | ~0.5 | **below** [2, 4] |
| Kentucky (KY) | 13 | 5 | ~37 | ~0.3 | **below** [0, 1] |
| South Dakota (SD) | 8 | 2 | ~34 | ~0.3 | within [0, 1] |
| Connecticut (CT) | 20 | 4 | ~32 | ~0.3 | **below** [1, 1] |
| North Dakota (ND) | 18 | 10 | ~14 | ~0.1 | **below** [0, 1] |
| Delaware (DE) | 3 | 1 | ~8 | ~0.1 | **below** [0, 1] |
| Maine (ME) | 7 | 2 | ~7 | ~0.1 | **below** [0, 1] |
| New Hampshire (NH) | 7 | 3 | ~5 | ~0.0 | **below** [0, 1] |
| Arkansas (AR) | 6 | 2 | ~4 | ~0.0 | **below** [0, 1] |
| Montana (MT) | 11 | 2 | ~1 | ~0.0 | **below** [0, 1] |
| Vermont (VT) | 6 | 1 | ~0 | ~0.0 | **below** [0, 0] |
| District of Columbia (DC) | 4 | 0 | ~0 | ~0.0 | **below** [1, 1] |
| Alaska (AK) | 4 | 0 | ~0 | ~0.0 | **below** [0, 1] |
| Louisiana (LA) | 7 | 0 | ~0 | ~0.0 | **below** [1, 2] |
| Hawaii (HI) | 7 | 0 | ~0 | ~0.0 | **below** [0, 1] |
| Rhode Island (RI) | 5 | 0 | ~0 | ~0.0 | **below** [0, 1] |
| West Virginia (WV) | 6 | 0 | ~0 | ~0.0 | **below** [0, 1] |
| **Total (51)** | **2,605** | **1,435** | **~28,264** | **~248.2** | within [105, 288] |

Nationally the bottom-up total is **248.2 TWh/yr**, inside the summed anchor band
of 105–288 TWh/yr, with 2,605 facilities and 1,368 of them carrying a measured
building footprint. The evidence mix is **0 Tier A**, **102 Tier B** and
**1,333 Tier C**.

> These numbers are about **48% lower** than they were before the site-vs-building
> footprint rule described under [Tier C](#tier-c--floor-area-carea). The
> previous build counted a `landuse=industrial` *site* polygon as if it were a
> *building*, which overstated the floor area — and so the power estimate — by
> roughly 3x for those campuses. No facility was dropped; 19 states simply lost
> part of their Tier C total. See [LIMITATIONS.md §11e](LIMITATIONS.md).

### Reading the anchor column

The anchor is a crude population/market-share heuristic per state, not a
measurement, and the column says which way the disagreement goes because the
two directions mean different things:

* **Above the band** — the bottom-up total exceeds even the anchor's optimistic
  end. Either the anchor is too low for that state, or the model is too high.
  Virginia, Texas, Oregon and Ohio are the largest cases; they host some of the
  biggest hyperscale campuses in the country, so "the anchor is too low" is at
  least as plausible as "the model is too high".
* **Below the band** — the bottom-up total falls short of the anchor's
  pessimistic end. This is usually a *coverage* statement, not a modelling
  error: the state's facilities are simply not in the sources (see
  [LIMITATIONS.md §11a](LIMITATIONS.md)). Florida, New York, Pennsylvania and
  Massachusetts all fall here.

Nothing was tuned to make these agree. Calibration is off by default, and when
enabled it never touches attested figures.

## California detail

The rest of this document describes the California build in depth, which
received the most validation; the same pipeline and caveats apply to every
other state.

## Current output (California)

| Metric | Value |
| --- | --- |
| Facilities | 216 |
| With building footprint geometry | 87 |
| With a power estimate | 116 |
| Corroborated by 2+ sources | 57 |
| Bottom-up IT load | ~1,424 MW |
| Bottom-up annual energy | ~12.8 TWh/yr |

Power evidence tiers: **46** facilities Tier B (generator permits), **70** Tier C
(floor-area model), **0** Tier A (no curated citations yet — see
[Adding attested figures](#adding-attested-figures)).

### By serving utility

Utility attribution is one of the more analytically useful outputs, and it
confirms a known feature of California's data center geography: a single
municipal utility carries the largest concentration in the state.

| Utility | Sites | Sum IT MW |
| --- | --- | --- |
| **SVP** (Silicon Valley Power, Santa Clara) | 69 | 757 |
| PG&E | 68 | 469 |
| LADWP | 34 | 50 |
| SCE | 18 | 20 |
| SDG&E | 13 | 16 |
| SMUD | 7 | 110 |


## Quick start

```bash
uv sync --extra dev
uv run data-center-dataset build      # fetch, transform, export (CA by default)
uv run data-center-dataset build-all  # every supported state, then the national roll-up
uv run data-center-dataset validate   # check schema contracts
uv run data-center-dataset report     # summary tables
uv run data-center-dataset national   # re-assemble data/processed/national/
uv run pytest                         # 226 offline tests
```

The first build downloads a 157 MB EPA archive once; later runs reuse it. All
HTTP traffic is disk-cached and identifies itself as
`data-center-dataset/0.1 (aerith.netzer@northwestern.edu)`.

Optional flags:

```bash
uv run data-center-dataset build --with-ceqanet        # crawl CEQA filings (slow, low recall)
uv run data-center-dataset build --apply-calibration   # rescale modelled tiers to the state anchor
uv run data-center-dataset build --refresh             # bypass caches
```

## Sources

| Source | Contributes | Access | Licence |
| --- | --- | --- | --- |
| [OpenStreetMap](https://www.openstreetmap.org) via Overpass | Location, **building footprint polygons**, operator, year built | Free API | ODbL-1.0 |
| [PeeringDB](https://www.peeringdb.com) `/api/fac` | Colocation registry, interconnection counts, CLLI | Free API | CC-BY-4.0 |
| [EPA NEI 2020](https://www.epa.gov/air-emissions-inventories) (all 10 regions) | Facility recall + **backup generator inventory** (Tier B) | Free bulk | US Public Domain |
| [CEC GIS](https://cecgis-caenergy.opendata.arcgis.com/) | Electric utility service territories | Free API | US Public Domain |
| [CEQAnet](https://ceqanet.opr.ca.gov/) | Attested project MW (opt-in) | Free scrape | US Public Domain |

**Deliberately not used.** Baxtel publishes per-facility MW but paywalls it
(`isAccessibleForFree: false`; values render as `░░░`). `datacenters.com` and
`datacentermap.com` sit behind bot-protection walls. Neither was circumvented.

### How PeeringDB is collected

PeeringDB is queried **once for the whole country**, not once per state. Two
findings forced this:

* The `state` query parameter is a **full-text search, not an exact match**.
  A `state=CA` query also returns facilities in North and South Carolina, and
  `state=CO` matches "**Co**gent" Communications. Querying per state therefore
  writes other states' facilities into a state's raw snapshot.
* The anonymous API quota is roughly **13 requests per hour**, so a 51-state
  sweep one query at a time takes hours and fails with HTTP 429.

The entire US facility set is 1,378 records (~1.3 MB) and fits in one request.
The pipeline fetches it once per snapshot
(`data/raw/peeringdb/_US/<snapshot>/`) and splits it locally, which also puts
every state on a single timestamp. Assigning a facility to a state is a
documented ordered fallback — postal code → full state name → state-prefixed
ZIP → ZIP prefix → geographic containment — and a facility that cannot be
placed is left unassigned rather than guessed into a neighbour. See
[LIMITATIONS.md §11d](LIMITATIONS.md).

### Why EPA NEI matters more than expected

Reconnaissance found NEI to be the strongest *free* per-facility source, because
every large data center holds an air permit for its backup diesel generators. It
supplies sites that neither OSM nor PeeringDB records — Google Mountain View,
Vantage Santa Clara, RagingWire Sacramento, AWS Santa Clara and Hayward,
Microsoft Santa Clara — each with coordinates.

## Outputs

Written to `data/processed/<STATE>/`:

| File | Contents |
| --- | --- |
| `facilities.{csv,parquet}` | One row per resolved site, with the preferred power figure |
| `facilities.geojson` | Same, as points, for mapping |
| `power_estimates.{csv,parquet}` | **Every** estimate from **every** method, uncollapsed |
| `facility_sources.csv` | `facility_id` → contributing source records |
| `exclusions.csv` | Records filtered out, each with the rule that fired |
| `dedupe_review.csv` | Uncertain match pairs awaiting human judgement |
| `tier_agreement.csv` | How far apart independent methods land |
| `reconciliation.json` | Bottom-up total vs top-down anchor |
| `datapackage.json` | Frictionless descriptor with field documentation |

`facilities.best_power_mw` is a convenience column. For anything analytical,
join `power_estimates` and filter on `method` so you control which evidence you
are willing to rely on.

> **Note on the California files at the root of `data/processed/`.** The
> original California-only build wrote its tables directly into
> `data/processed/` (`facilities.csv`, `power_estimates.csv`, …). Per-state
> builds now write to `data/processed/CA/` and the rest of `data/processed/`
> holds one directory per jurisdiction plus `national/`. The root-level
> California files are the *legacy* layout and are retained as-is for
> reference; the current California build is `data/processed/CA/`.

### National roll-up

`data/processed/national/` holds the combined tables, assembled by
`data-center-dataset national` (also run automatically at the end of
`build-all`):

| File | Contents |
| --- | --- |
| `facilities.{csv,parquet,geojson}` | Every facility from every state, with a `state` column |
| `power_estimates.{csv,parquet}` | Every estimate from every state, uncollapsed |
| `summary.csv` | One row per state: facilities, tiers, IT MW, TWh/yr, anchor band |
| `reconciliation.json` | National bottom-up total vs the summed anchor band |

Nothing is recomputed here. The national tables are a concatenation of what
each state build published, so a disagreement with a per-state table is a bug
in the roll-up rather than a modelling difference.

## How power is estimated

Three tiers, in precedence order. `power_tier` records which one produced
`best_power_mw`.

### Tier A — attested (`A_attested`)

A figure stated by a source, with a URL, retrieval date and verbatim quote. The
schema contract **rejects any Tier A row without a citation**, so an uncited
number cannot acquire the authority of attested evidence.

### Tier B — backup generator fleet (`B_generator`)

Backup generation exists to carry critical load through an outage, so installed
capacity physically bounds what a site can serve.

```
nameplate_mw = parsed ratings + (unrated units × per-unit prior)
critical_mw  = nameplate_mw / redundancy_factor
it_load_mw   = critical_mw × 0.85
```

The per-unit prior is **measured, not guessed**. Of 817 California data-center
generator units in NEI, 60 state a nameplate in free text; their median is
2,116 kW with an IQR of 885–2,190 kW. Those values set the prior.

The dominant uncertainty is the redundancy divisor: NEI does not record whether
a site is N+1 or 2N, and the two imply very different loads for the same fleet.
The interval spans 1.10–2.00 accordingly.

### Tier C — floor area (`C_area`)

```
white_space = footprint_sqft × min(storeys, 3) × 0.60
it_load_mw  = white_space × W_per_sqft / 1e6
```

Storeys are capped at three. Purpose-built data centers are one to three storeys;
above that the facility is a tenant in an office or carrier-hotel tower, and
crediting every floor measures the building rather than the facility. Such records
carry `partial_occupancy = true` and a lower bound assuming a single storey.

Density and PUE priors live in `data/reference/power_density_priors.csv`, keyed on
facility class and vintage, and are editable without touching code.

**A facility with no footprint measurement gets no Tier C estimate.** Treating a
missing polygon as zero area would fabricate a zero-power data center.

**An OSM polygon counts as a footprint only if it is a building.** OpenStreetMap
maps some campuses as the *site* that contains them — a `landuse=industrial`
polygon drawn around the grounds — rather than as building outlines. Measured
across the live data, the buildings mapped inside those site polygons cover
2.5%–68.8% of the site area (median 29.9%), so counting the site as a
building overstates the floor area by roughly 3x and the power estimate
inherits the factor. A site polygon therefore yields a **null** footprint, not
a small one, and the facility keeps its location but loses its Tier C estimate.
See [LIMITATIONS.md §11e](LIMITATIONS.md).

### Annual energy

`annual_gwh = it_load_mw × PUE × utilization × 8760 / 1000`, with PUE from the
vintage-keyed priors and utilization at 0.70 (range 0.55–0.85).

## Adding attested figures

Tier A is currently empty because no figure was added that could not be
independently verified at build time. To contribute one, append a row to
`data/reference/manual_overrides.csv`:

```csv
match_name,match_operator,basis,value_mw,source_url,retrieved_at,quote
Colovore,Colovore,critical_load,9.0,https://example.gov/doc,2026-08-19,"...verbatim sentence..."
```

`basis` is `it_load`, `critical_load` or `total_facility`; the pipeline converts
between them. Rows lacking a value or URL are skipped with a warning.

## Architecture

```
src/data_center_dataset/
  cli.py          fetch | build | build-all | national | validate | map | charts | report
  config.py       paths, EPSG:5070, every model prior
  http.py         cached client: rate limit, retry, resume, identifying UA
  pipeline.py     ingest -> classify -> resolve -> enrich -> power -> export
  states.py       all 51 jurisdictions: bbox, EPA region, anchor, map presets
  national.py     national roll-up of the published per-state tables
  sources/        osm, peeringdb, epa_nei, ceqanet, cec_gis
  normalize/      schema (pandera), classify, dedupe, geometry
  power/          evidence (A), generators (B), model (C), reconcile
  export.py       parquet/csv/geojson + datapackage
data/
  raw/<source>/<STATE>/<date>/  immutable dated snapshots (committed, except the NEI zip)
  raw/peeringdb/_US/<date>/    the country-wide PeeringDB payload the state splits come from
  reference/             operator aliases, power priors, curated overrides
  processed/<STATE>/     published tables, one directory per jurisdiction
  processed/national/    combined national tables
```

All areas and distances are computed in **EPSG:5070** (CONUS Albers,
equal-area, metres). Computing areas in WGS84 degrees is a common and badly wrong
shortcut. Alaska and Hawaii are outside that projection's comfortable domain:
areas are still exact (an equal-area projection preserves area everywhere) but
*distances* are distorted, and distance is what the entity-resolution merge
radius uses. Those two states therefore carry their own equal-area projection
(`states.StateProfile.equal_area_crs`): EPSG:3338 for Alaska, a Lambert
azimuthal equal-area projection centred on the archipelago for Hawaii.

Adding a state means adding one entry to `states.py`. The EPA NEI region
member, the geographic gate, the reconciliation anchor and the map presets all
travel with that entry.

## Reproducibility

`data/raw/<source>/<STATE>/<date>/` snapshots are committed, so a build is
reproducible without re-querying upstream. The one exception is the 157 MB NEI
archive, which is gitignored; the pipeline re-downloads it from the documented
URL and the committed artefact is the filtered per-state extract it produces.

## Licensing

The dataset incorporates OpenStreetMap geometry and is therefore a derived
database under **ODbL-1.0**. Redistribution carries attribution *and share-alike*
obligations. If you need a permissively licensed product, rebuild while excluding
the OSM source — you will lose all footprint geometry, and with it Tier C.

PeeringDB is CC-BY-4.0; EPA and CEC material is US public domain.
# data_center_dataset
