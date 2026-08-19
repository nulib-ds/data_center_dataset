"""California Energy Commission GIS ingest.

Provides electric utility service territory boundaries, which let each facility
be attributed to the utility that serves it -- PG&E, SCE, SDG&E, SMUD, LADWP or
Silicon Valley Power. That attribution is one of the more analytically useful
fields in the dataset, since data center siting in California clusters heavily
in a few municipal utility territories (notably Santa Clara's SVP).

Deliberately not included
-------------------------
Distance to the nearest transmission substation was planned, but no reliable
free substation layer could be verified: the CEC ``Transmission_Line`` service
exposes no queryable fields and the HIFLD substation endpoints tested were
dead. Shipping a silently-empty column would be worse than omitting it, so it is
recorded as a known gap in LIMITATIONS.md instead.

The ``California_Counties`` layer holds centroids rather than polygons, so
county is taken from source record fields instead of a spatial join.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import date

import geopandas as gpd
import pandas as pd

from ..config import raw_dir
from ..http import CachedClient

log = logging.getLogger(__name__)

SOURCE = "cec_gis"
ARCGIS_BASE = "https://services3.arcgis.com/bWPjFyq029ChCGur/arcgis/rest/services"
UTILITY_LAYER = "ElectricLoadServingEntities_IOU_POU/FeatureServer/0"


def fetch_utility_territories(
    client: CachedClient, snapshot: str | None = None, refresh: bool = False
) -> gpd.GeoDataFrame:
    """Download electric load-serving entity territory polygons as GeoJSON.

    Stored gzipped: the raw GeoJSON is 12 MB and compresses to 3.9 MB, which is
    a reasonable size to commit for reproducibility. The polygons are kept at
    full fidelity rather than simplified, because facilities near a territory
    boundary are exactly the interesting cases -- Santa Clara's SVP border runs
    through one of the densest data center clusters in the state, and a
    simplified edge would misattribute them.
    """
    snapshot = snapshot or date.today().isoformat()
    dest = raw_dir(SOURCE, snapshot) / "cec_utility_territories.geojson.gz"

    if not dest.exists() or refresh:
        payload = client.fetch_json(
            f"{ARCGIS_BASE}/{UTILITY_LAYER}/query",
            params={
                "where": "1=1",
                "outFields": "Utility,Acronym,Type",
                "outSR": "4326",
                "f": "geojson",
            },
            refresh=refresh,
        )
        with gzip.open(dest, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh)
        log.info(
            "cec_gis: %d utility territories -> %s",
            len(payload.get("features", [])),
            dest.name,
        )

    with gzip.open(dest, "rb") as fh:
        gdf = gpd.read_file(io.BytesIO(fh.read()))

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    # A single readable label, preferring the acronym operators actually use.
    # Some rows carry an empty-string acronym rather than a null, so blanks are
    # normalised before falling back to the full utility name.
    acronym = gdf.get("Acronym")
    if acronym is None:
        acronym = pd.Series([None] * len(gdf), index=gdf.index)
    acronym = acronym.astype("object").where(
        acronym.notna() & (acronym.astype(str).str.strip() != ""), None
    )
    gdf["utility"] = acronym.fillna(gdf.get("Utility"))
    gdf["utility_name"] = gdf.get("Utility")
    gdf["utility_type"] = gdf.get("Type")
    return gdf[["utility", "utility_name", "utility_type", "geometry"]]
