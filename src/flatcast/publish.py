"""Assemble the static web map: the web/ template plus the latest data."""

from __future__ import annotations

import json
import shutil

import pandas as pd

from flatcast.grid import Grid
from flatcast.log import console
from flatcast.sources.coops import station_lonlat

ATTRIBUTION = [
    "Water levels and wind: NOAA CO-OPS / Texas Coastal Ocean Observation Network",
    "Imagery: NASA Harmonized Landsat Sentinel-2 (HLS v2.0)",
    "Wind forecasts: Open-Meteo.com (CC BY 4.0) or US National Weather Service",
    "Basemap: Sentinel-2 cloudless by EOX IT Services GmbH (CC BY-NC-SA 4.0), contains modified Copernicus Sentinel data",
]


def build_site(cfg) -> None:
    paths = cfg.paths
    template = paths.root / "web"
    site = paths.site
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(template, site)
    # Cache-bust the script and stylesheet so a rebuilt site is picked up straight away.
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d%H%M%S")
    index = site / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace('src="app.js"', f'src="app.js?v={stamp}"').replace('href="style.css"', f'href="style.css?v={stamp}"'),
        encoding="utf-8",
    )
    data = site / "data"
    data.mkdir(exist_ok=True)

    latest = paths.outputs / "forecast" / "latest"
    if (latest / "forecast.json").exists():
        shutil.copy2(latest / "forecast.json", data / "forecast.json")
        if (latest / "png").exists():
            shutil.copytree(latest / "png", data / "frames")
    if paths.flats.exists():
        shutil.copy2(paths.flats, data / "flats.geojson")

    slr = paths.outputs / "slr"
    if (slr / "slr_summary.json").exists():
        (data / "slr").mkdir(exist_ok=True)
        for f in [slr / "slr_summary.json", *slr.glob("slr_*cm.png")]:
            shutil.copy2(f, data / "slr" / f.name)

    val = paths.outputs / "validation"
    if val.exists():
        (data / "validation").mkdir(exist_ok=True)
        for f in ("waterlevel_summary.json", "exposure_summary.json", "waterlevel_error_by_lead.png"):
            if (val / f).exists():
                shutil.copy2(val / f, data / "validation" / f)
        events = sorted((val / "low_water_events").glob("*.png"))[:10]
        for f in events:
            shutil.copy2(f, data / "validation" / f.name)

    grid = Grid.from_config(cfg)
    lon, lat = station_lonlat(cfg.stations.primary, paths.raw / "coops")
    meta = {
        "built": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "bbox": list(cfg.area.bbox),
        "corners": grid.corners_lonlat(),
        "station": {"id": cfg.stations.primary, "name": "S. Bird Island", "lon": lon, "lat": lat},
        "holdout_year": cfg.holdout_year,
        "low_water_events": [f.name for f in sorted((data / "validation").glob("[0-9][0-9]_*.png"))] if (data / "validation").exists() else [],
        "attribution": ATTRIBUTION,
    }
    (data / "meta.json").write_text(json.dumps(meta, indent=1))
    console.log(f"Site: built in {site} (preview with `flatcast serve`)")
