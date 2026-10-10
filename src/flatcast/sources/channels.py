"""Maintained navigation channels from the USACE National Channel Framework.

Service: services7.arcgis.com/n1YM8pTrFmm7L4hs/.../National_Channel_Framework/FeatureServer,
layer 1 (ChannelArea polygons). Public data; no key needed. In this region it
covers the Gulf Intracoastal Waterway, the Corpus Christi Ship Channel and
the La Quinta channel.
"""

from __future__ import annotations

from pathlib import Path

from flatcast.cache import fetch_json

SERVICE = "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/ArcGIS/rest/services/National_Channel_Framework/FeatureServer/1/query"
ATTRIBUTION = "Navigation channels: USACE National Channel Framework"


def channel_areas(bbox, cache_dir: Path) -> dict:
    """GeoJSON FeatureCollection of channel polygons intersecting bbox (cached for 90 days)."""
    west, south, east, north = bbox
    params = {
        "where": "1=1",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "channelareaidpk,sdsfeaturename",
        "returnGeometry": "true",
        "f": "geojson",
    }
    key = "_".join(f"{v:.3f}" for v in bbox)
    data = fetch_json(SERVICE, params, cache_dir / "channels" / f"{key}.geojson", max_age_hours=24 * 90)
    if data.get("type") != "FeatureCollection":
        return {"type": "FeatureCollection", "features": []}
    for f in data["features"]:
        name = (f.get("properties") or {}).get("sdsfeaturename") or "Channel"
        f["properties"] = {"name": name.replace("_", " ").title()}
    return data
