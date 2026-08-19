# California Data Center Dataset

A reproducible pipeline that assembles a registry of **commercial data centers in
California** — colocation, wholesale and hyperscale — from public sources, and
attaches **power estimates that carry an explicit evidence tier and confidence
interval**.

> **Read this first.** No figure in this dataset is a metered electricity
> reading. Per-facility consumption is confidential utility data and is not
> publicly available for any California data center. Every power number here is
> either a *cited third-party claim* or a *model output*, and each is labelled as
> such. See [LIMITATIONS.md](LIMITATIONS.md).

## Current output

| Metric | Value |
| --- | --- |
| Facilities | 221 |
| With building footprint geometry | 87 |
| With a power estimate | 118 |
| Corroborated by 2+ sources | 53 |
| Bottom-up IT load | ~1,473 MW |
| Bottom-up annual energy | ~13.3 TWh/yr |

Power evidence tiers: **45** facilities Tier B (generator permits), **73** Tier C
(floor-area model), **0** Tier A (no curated citations yet — see
[Adding attested figures](#adding-attested-figures)).

### By serving utility

Utility attribution is one of the more analytically useful outputs, and it
confirms a known feature of California's data center geography: a single
municipal utility carries the largest concentration in the state.

| Utility | Sites | Sum IT MW |
| --- | --- | --- |
| **SVP** (Silicon Valley Power, Santa Clara) | 72 | 777 |
| PG&E | 68 | 468 |
| SMUD | 8 | 132 |
| LADWP | 36 | 59 |
| SCE | 17 | 18 |
| SDG&E | 13 | 18 |


## Quick start

```bash
uv sync --extra dev
uv run data-center-dataset build      # fetch, transform, export
uv run data-center-dataset validate   # check schema contracts
uv run data-center-dataset report     # summary tables
uv run pytest                         # 43 offline tests
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
| [EPA NEI 2020](https://www.epa.gov/air-emissions-inventories) Region 9 | Facility recall + **backup generator inventory** (Tier B) | Free bulk | US Public Domain |
| [CEC GIS](https://cecgis-caenergy.opendata.arcgis.com/) | Electric utility service territories | Free API | US Public Domain |
| [CEQAnet](https://ceqanet.opr.ca.gov/) | Attested project MW (opt-in) | Free scrape | US Public Domain |

**Deliberately not used.** Baxtel publishes per-facility MW but paywalls it
(`isAccessibleForFree: false`; values render as `░░░`). `datacenters.com` and
`datacentermap.com` sit behind bot-protection walls. Neither was circumvented.

### Why EPA NEI matters more than expected

Reconnaissance found NEI to be the strongest *free* per-facility source, because
every large data center holds an air permit for its backup diesel generators. It
supplies sites that neither OSM nor PeeringDB records — Google Mountain View,
Vantage Santa Clara, RagingWire Sacramento, AWS Santa Clara and Hayward,
Microsoft Santa Clara — each with coordinates.

## Outputs

Written to `data/processed/`:

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
  cli.py          fetch | build | validate | report
  config.py       paths, EPSG:3310, every model prior
  http.py         cached client: rate limit, retry, identifying UA
  pipeline.py     ingest -> classify -> resolve -> enrich -> power -> export
  sources/        osm, peeringdb, epa_nei, ceqanet, cec_gis
  normalize/      schema (pandera), classify, dedupe, geometry
  power/          evidence (A), generators (B), model (C), reconcile
  export.py       parquet/csv/geojson + datapackage
data/
  raw/<source>/<date>/   immutable dated snapshots (committed, except the NEI zip)
  reference/             operator aliases, power priors, curated overrides
  processed/             published tables
```

All areas and distances are computed in **EPSG:3310** (California Albers,
equal-area, metres). Computing areas in WGS84 degrees is a common and badly wrong
shortcut.

## Reproducibility

`data/raw/<source>/<date>/` snapshots are committed, so a build is reproducible
without re-querying upstream. The one exception is the 157 MB NEI archive, which
is gitignored; the pipeline re-downloads it from the documented URL and the
committed artefact is the filtered California extract it produces.

## Licensing

The dataset incorporates OpenStreetMap geometry and is therefore a derived
database under **ODbL-1.0**. Redistribution carries attribution *and share-alike*
obligations. If you need a permissively licensed product, rebuild while excluding
the OSM source — you will lose all footprint geometry, and with it Tier C.

PeeringDB is CC-BY-4.0; EPA and CEC material is US public domain.
