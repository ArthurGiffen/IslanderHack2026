"""Harmonized Landsat Sentinel-2 (HLS v2.0) search and windowed reads via earthaccess.

Only the pixels inside the analysis grid are fetched: each band is a
cloud-optimized GeoTIFF, so a window read costs a few HTTP range requests
instead of a whole-tile download.

Terms: HLS is NASA open data (no restrictions); access needs a free NASA
Earthdata Login, read from EARTHDATA_USERNAME / EARTHDATA_PASSWORD (or
EARTHDATA_TOKEN).
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from flatcast.grid import Grid

SENSOR_BANDS = {
    "L30": {"blue": "B02", "green": "B03", "red": "B04", "swir1": "B06", "fmask": "Fmask"},
    "S30": {"blue": "B02", "green": "B03", "red": "B04", "swir1": "B11", "fmask": "Fmask"},
}
NODATA = {"fmask": 255}
REFLECTANCE_NODATA = -9999
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_NUM_THREADS="1")


class MissingCredentials(RuntimeError):
    pass


@dataclass
class Scene:
    """One satellite pass over the area (possibly spanning several MGRS tiles)."""

    key: str
    sensor: str
    time: pd.Timestamp
    granules: list[str] = field(default_factory=list)
    urls: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


def have_credentials() -> bool:
    return bool(os.environ.get("EARTHDATA_TOKEN")) or bool(
        os.environ.get("EARTHDATA_USERNAME") and os.environ.get("EARTHDATA_PASSWORD")
    )


def login():
    """Log in to Earthdata and return an fsspec HTTPS filesystem for reading granules."""
    if not have_credentials():
        raise MissingCredentials(
            "NASA Earthdata credentials not found. Set EARTHDATA_USERNAME and EARTHDATA_PASSWORD "
            "(free account at https://urs.earthdata.nasa.gov) and run again."
        )
    import earthaccess
    import fsspec

    auth = earthaccess.login(strategy="environment")
    if not auth or not auth.authenticated:
        raise MissingCredentials("Earthdata login failed; check EARTHDATA_USERNAME / EARTHDATA_PASSWORD.")
    token = auth.token["access_token"]
    # Small blocks: we only want a ~20 km window out of each 110 km tile.
    return fsspec.filesystem(
        "https",
        client_kwargs={"headers": {"Authorization": f"Bearer {token}"}, "trust_env": False},
        block_size=512 * 1024,
    )


def _search_year(collection: str, year: int, bbox, cache_dir: Path) -> list[dict]:
    path = cache_dir / "hls" / "search" / f"{collection}_{year}.json"
    current = year >= pd.Timestamp.now().year
    if path.exists() and (not current or time.time() - path.stat().st_mtime < 12 * 3600):
        return json.loads(path.read_text())

    import earthaccess

    results = earthaccess.search_data(
        short_name=collection,
        version="2.0",
        bounding_box=tuple(bbox),
        temporal=(f"{year}-01-01T00:00:00", f"{year}-12-31T23:59:59"),
        count=-1,
    )
    records = [
        {
            "id": g["meta"]["native-id"],
            "time": g["umm"]["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"],
            "links": [u for u in g.data_links(access="external") if u.endswith(".tif")],
        }
        for g in results
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records))
    return records


def search(cfg) -> list[Scene]:
    """Every HLS pass over the bounding box, grouped across tiles, oldest first."""
    cache = cfg.paths.raw
    start = pd.Timestamp(cfg.imagery.start)
    # Tiles from one pass share the date and sensor; the scene keeps the first tile's time.
    scenes: dict[tuple, Scene] = {}
    for collection in cfg.imagery.collections:
        sensor = collection[-3:]
        for year in range(start.year, pd.Timestamp.now().year + 1):
            for rec in _search_year(collection, year, cfg.area.bbox, cache):
                t = pd.Timestamp(rec["time"])
                t = t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")
                if t < start.tz_localize("UTC"):
                    continue
                scene = scenes.setdefault(
                    (sensor, t.date()), Scene(key=f"{t:%Y%m%dT%H%M}_{sensor}", sensor=sensor, time=t)
                )
                scene.granules.append(rec["id"])
                bands = SENSOR_BANDS[sensor]
                for name, band in bands.items():
                    scene.urls[name] += [u for u in rec["links"] if u.endswith(f".{band}.tif")]
    return sorted(scenes.values(), key=lambda s: s.time)


def read_band(scene: Scene, band: str, grid: Grid, fs) -> np.ndarray:
    """Read one band for the whole grid, mosaicking the scene's tiles."""
    nodata = NODATA.get(band, REFLECTANCE_NODATA)
    dtype = np.uint8 if band == "fmask" else np.int16
    out = np.full(grid.shape, nodata, dtype=dtype)
    for url in scene.urls[band]:
        arr = _read_url(url, grid, fs, nodata, dtype)
        empty = out == nodata
        out[empty] = arr[empty]
        if not (out == nodata).any():
            break
    return out


def _read_url(url: str, grid: Grid, fs, nodata, dtype, attempts: int = 4) -> np.ndarray:
    delay = 3.0
    for attempt in range(attempts):
        try:
            with rasterio.Env(**GDAL_ENV), rasterio.open(url, opener=fs) as src:
                src_nodata = src.nodata if src.nodata is not None else nodata
                with WarpedVRT(
                    src,
                    crs=grid.crs,
                    transform=grid.transform,
                    width=grid.width,
                    height=grid.height,
                    resampling=Resampling.nearest,
                    src_nodata=src_nodata,
                    nodata=nodata,
                ) as vrt:
                    return vrt.read(1).astype(dtype)
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")
