"""Assemble the static web map: the web/ template plus each area's data.

site/data/areas.json            the area switcher's list
site/data/<area>/meta.json      grid, gauge position, wind tilt, file layouts
site/data/<area>/model.bin      the exposure model, cropped to the flats: per pixel
                                int16 half-dry level (mm), int16 sharpness k (x10), uint8 flat id
site/data/<area>/history.bin    hourly gauge level (int16 mm) and wind stress (int16 x10, x and y)
site/data/<area>/forecast.json  the 48-hour forecast
plus flats.geojson, slr/ and validation/ when they exist.

The browser recomputes the exposure map for any hour from these, so a date
picker, walkable highlighting and the forecast all share one code path.
"""

from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd
from rasterio import features

from flatcast.config import area_configs
from flatcast.grid import Grid
from flatcast.log import console
from flatcast.sources.coops import station_lonlat

ATTRIBUTION = [
    "Water levels and wind: NOAA CO-OPS / Texas Coastal Ocean Observation Network",
    "Imagery: NASA Harmonized Landsat Sentinel-2 (HLS v2.0)",
    "Wind forecasts: Open-Meteo.com (CC BY 4.0) or US National Weather Service",
    "Basemap: Sentinel-2 cloudless by EOX IT Services GmbH (CC BY-NC-SA 4.0), contains modified Copernicus Sentinel data",
    "Navigation channels: USACE National Channel Framework",
    "Crab rules: Texas Parks and Wildlife Department Outdoor Annual 2026-27",
]
NA16 = -32768


def _int16(a: np.ndarray, scale: float) -> np.ndarray:
    out = np.full(a.shape, NA16, dtype="<i2")
    ok = np.isfinite(a)
    out[ok] = np.clip(np.round(a[ok] * scale), -32767, 32767).astype("<i2")
    return out


def _corners(grid: Grid, r0: int, c0: int, h: int, w: int) -> list[list[float]]:
    from pyproj import Transformer

    t = grid.transform
    x0, y0 = t.c + c0 * t.a, t.f + r0 * t.e
    xs = [x0, x0 + w * t.a, x0 + w * t.a, x0]
    ys = [y0, y0, y0 + h * t.e, y0 + h * t.e]
    lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(xs, ys)
    return [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]


def export_area(cfg, out) -> dict:
    from flatcast import flats as flats_mod
    from flatcast.forecast import display_zone, exposure_model_path
    from flatcast.hydro import load_hourly
    from flatcast.models import exposure as E
    from flatcast.pairing import add_wind_features

    paths = cfg.paths
    out.mkdir(parents=True, exist_ok=True)
    grid = Grid.from_config(cfg)
    lon, lat = station_lonlat(cfg.stations.primary, paths.raw / "coops")
    west, south, east, north = cfg.area.bbox
    meta = {
        "id": cfg.area_id,
        "name": cfg.area.get("name", cfg.area_id),
        "bbox": [west, south, east, north],
        "center": [(west + east) / 2, (south + north) / 2],
        "corners": grid.corners_lonlat(),
        "station": {"id": cfg.stations.primary, "lon": lon, "lat": lat},
        "holdout_year": cfg.holdout_year,
        "walkable": {"p_dry": float(cfg.walkable.p_dry), "min_area_fraction": float(cfg.walkable.min_area_fraction)},
        "has_model": False,
    }

    latest = paths.outputs / "forecast" / "latest" / "forecast.json"
    if latest.exists():
        shutil.copy2(latest, out / "forecast.json")
    if paths.flats.exists():
        shutil.copy2(paths.flats, out / "flats.geojson")

    if exposure_model_path(cfg).exists():
        model = E.ExposureModel.load(exposure_model_path(cfg))
        zone = display_zone(model)
        if zone.any():
            rows, cols = np.where(zone)
            r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
            win = (slice(r0, r1), slice(c0, c1))
            h, w = r1 - r0, c1 - c0
            z = np.where(zone, model.z, np.nan)[win]
            k = np.where(zone, np.exp(model.logk), np.nan)[win]
            flat_id = np.zeros(grid.shape, dtype=np.uint8)
            names = []
            if paths.flats.exists():
                fc = json.loads(paths.flats.read_text(encoding="utf-8"))
                for i, (name, m, _) in enumerate(flats_mod.masks(fc, grid)[:255], start=1):
                    flat_id[m & zone] = i
                    names.append(name)
            blob = _int16(z, 1000).tobytes() + _int16(k, 10).tobytes() + flat_id[win].astype("u1").tobytes()
            (out / "model.bin").write_bytes(blob)
            # gauge position in window pixel coordinates (pixel centres sit at +0.5)
            gx, gy = ~grid.transform * _gauge_xy(grid, lon, lat)
            meta.update(
                has_model=True,
                model={
                    "width": int(w),
                    "height": int(h),
                    "res_m": grid.res,
                    "corners": _corners(grid, r0, c0, h, w),
                    "gauge_col": float(gx - c0),
                    "gauge_row": float(gy - r0),
                    "A": model.A.round(9).tolist(),
                    "level_range": list(model.level_range),
                    "z_scale": 0.001,
                    "k_scale": 0.1,
                    "pixels": int(zone.sum()),
                    "flat_names": names,
                },
            )

    hourly = add_wind_features(load_hourly(cfg), int(cfg.exposure.setup_window_hours))
    hist = _int16(hourly["h"].to_numpy(float), 1000).tobytes() + _int16(hourly["tau_x"].to_numpy(float), 10).tobytes() + _int16(hourly["tau_y"].to_numpy(float), 10).tobytes()
    (out / "history.bin").write_bytes(hist)
    meta["history"] = {"start": hourly.index[0].isoformat(), "hours": int(len(hourly)), "h_scale": 0.001, "tau_scale": 0.1}

    slr = paths.outputs / "slr"
    if (slr / "slr_summary.json").exists():
        (out / "slr").mkdir(exist_ok=True)
        for f in [slr / "slr_summary.json", *slr.glob("slr_*cm.png")]:
            shutil.copy2(f, out / "slr" / f.name)

    traps = paths.outputs / "traps"
    if (traps / "traps.json").exists():
        (out / "traps").mkdir(exist_ok=True)
        for f in ("traps.json", "trap_water.png", "channels.geojson"):
            if (traps / f).exists():
                shutil.copy2(traps / f, out / "traps" / f)
        meta["traps"] = json.loads((traps / "traps.json").read_text())

    val = paths.outputs / "validation"
    if val.exists():
        (out / "validation").mkdir(exist_ok=True)
        for f in ("waterlevel_summary.json", "exposure_summary.json", "waterlevel_error_by_lead.png"):
            if (val / f).exists():
                shutil.copy2(val / f, out / "validation" / f)
        for f in sorted((val / "low_water_events").glob("*.png"))[:10]:
            shutil.copy2(f, out / "validation" / f.name)
        meta["low_water_events"] = [f.name for f in sorted((out / "validation").glob("[0-9][0-9]_*.png"))]

    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    return {k: meta[k] for k in ("id", "name", "bbox", "center", "has_model")}


def _gauge_xy(grid: Grid, lon: float, lat: float):
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform(lon, lat)


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
    areas = []
    for a in area_configs(cfg):
        try:
            areas.append(export_area(a, data / a.area_id))
        except FileNotFoundError as exc:
            console.log(f"[yellow]Site: skipping {a.area_id} ({exc})")
    (data / "areas.json").write_text(
        json.dumps({"built": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), "areas": areas, "attribution": ATTRIBUTION}, indent=1)
    )
    console.log(f"Site: built {len(areas)} area(s) in {site} (preview with `flatcast serve`)")
