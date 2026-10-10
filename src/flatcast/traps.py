"""Suggested crab-trap water.

There is no public record of where crabs are caught here, so this is a
practical and habitat suggestion built from what FlatCast does know:

  * stays covered at low water: exposed less than 5% of the time even at the
    low end of the last few years' water levels, so a trap set there isn't
    left high and dry when a norther drains the flats;
  * edge habitat: within `edge_m` (250 m) of the flats, where blue
    crabs forage along grass beds and flat edges;
  * clear of maintained navigation channels (USACE), plus a buffer, where boat
    traffic cuts trap lines. (In Nueces and Kleberg counties channels aren't
    legally off limits for traps; in Aransas County traps must be 200 ft away
    from a marked channel.)

People's own experience goes in the web map's "My spots" log, which stays on
their device.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from pyproj import Transformer
from rasterio import features
from scipy import ndimage
from scipy.special import expit
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from flatcast import render
from flatcast.grid import Grid
from flatcast.log import console
from flatcast.models import exposure as E
from flatcast.sources.channels import channel_areas

LOW_WATER_QUANTILE = 0.05
STRANDING_P = 0.05
TRAP_RGBA = (255, 92, 168, 190)  # magenta: stands apart from sand, teal and the walkable green


def compute(cfg, model: E.ExposureModel, region: np.ndarray, hourly: pd.DataFrame, edge_m: float = 250, channel_buffer_m: float = 60):
    grid = Grid.from_config(cfg)
    recent = hourly["h"].dropna()
    recent = recent[recent.index >= recent.index.max() - pd.DateOffset(years=int(cfg.slr.climate_years))]
    low = float(np.quantile(recent, LOW_WATER_QUANTILE))

    k = np.exp(model.logk)
    p_low = expit(k * (model.z - low))  # chance dry at low water, calm winds
    wet_at_low = region & (
        (model.status == E.NEVER_DRY) | ((model.status == E.FITTED) & (p_low < STRANDING_P))
    )
    # the flats: ground that goes dry at low water (not steep mainland shore)
    edges = region & (model.status == E.FITTED) & (p_low >= STRANDING_P) & (model.n_dry >= 3)
    dist_m = ndimage.distance_transform_edt(~edges) * grid.res
    candidate = wet_at_low & (dist_m <= edge_m)

    channels = channel_areas(cfg.area.bbox, cfg.paths.raw)
    blocked = np.zeros(grid.shape, bool)
    if channels["features"]:
        to_grid = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform
        shapes = [(shp_transform(to_grid, shape(f["geometry"])).buffer(channel_buffer_m), 1) for f in channels["features"] if f.get("geometry")]
        blocked = features.rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8").astype(bool)
    zone = candidate & ~blocked
    zone = ndimage.binary_opening(zone, iterations=1)  # drop single-pixel specks
    return zone, channels, low


def run(cfg, model: E.ExposureModel, region: np.ndarray, hourly: pd.DataFrame) -> dict:
    grid = Grid.from_config(cfg)
    zone, channels, low = compute(cfg, model, region, hourly)
    out = cfg.paths.outputs / "traps"
    out.mkdir(parents=True, exist_ok=True)
    grid.write(out / "trap_water.tif", zone.astype(np.uint8), tags={"meaning": "1 = suggested crab-trap water"})
    rgba = np.zeros((*grid.shape, 4), np.uint8)
    rgba[zone] = TRAP_RGBA
    render.save_png(rgba, out / "trap_water.png")
    (out / "channels.geojson").write_text(json.dumps(channels))
    summary = {
        "area_km2": round(float(zone.sum()) * (grid.res / 1000) ** 2, 2),
        "low_water_m": round(low, 3),
        "channels": sorted({f["properties"]["name"] for f in channels["features"]}),
        "png": "trap_water.png",
    }
    (out / "traps.json").write_text(json.dumps(summary, indent=1))
    console.log(f"Traps: {summary['area_km2']} km2 of suggested trap water; low water {low:+.2f} m; channels: {', '.join(summary['channels']) or 'none'}")
    return summary
