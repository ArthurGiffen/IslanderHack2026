"""The fixed analysis grid that every raster in FlatCast is resampled onto."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.warp import transform_bounds


@dataclass(frozen=True)
class Grid:
    crs: str
    transform: Affine
    width: int
    height: int

    @classmethod
    def from_config(cls, cfg) -> "Grid":
        res = float(cfg.area.resolution_m)
        west, south, east, north = transform_bounds("EPSG:4326", cfg.area.crs, *cfg.area.bbox)
        # Snap to multiples of the resolution so the grid lines up with HLS pixels.
        x0 = math.floor(west / res) * res
        y1 = math.ceil(north / res) * res
        x1 = math.ceil(east / res) * res
        y0 = math.floor(south / res) * res
        width = int(round((x1 - x0) / res))
        height = int(round((y1 - y0) / res))
        return cls(cfg.area.crs, Affine(res, 0, x0, 0, -res, y1), width, height)

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def res(self) -> float:
        return self.transform.a

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        """Projected x, y coordinates of pixel centres, each shaped (height, width)."""
        cols = np.arange(self.width) + 0.5
        rows = np.arange(self.height) + 0.5
        x = self.transform.c + cols * self.transform.a
        y = self.transform.f + rows * self.transform.e
        return np.meshgrid(x, y)

    def offsets_km(self, lon: float, lat: float) -> tuple[np.ndarray, np.ndarray]:
        """East and north distance (km) of every pixel from a lon/lat point."""
        px, py = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True).transform(lon, lat)
        x, y = self.centers()
        return ((x - px) / 1000).astype(np.float32), ((y - py) / 1000).astype(np.float32)

    def corners_lonlat(self) -> list[list[float]]:
        """Image corners for a MapLibre image source: TL, TR, BR, BL."""
        t = self.transform
        xs = [t.c, t.c + self.width * t.a, t.c + self.width * t.a, t.c]
        ys = [t.f, t.f, t.f + self.height * t.e, t.f + self.height * t.e]
        lon, lat = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True).transform(xs, ys)
        return [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]

    def profile(self, dtype: str, nodata=None, count: int = 1) -> dict:
        return {
            "driver": "GTiff",
            "dtype": dtype,
            "nodata": nodata,
            "width": self.width,
            "height": self.height,
            "count": count,
            "crs": self.crs,
            "transform": self.transform,
            "compress": "deflate",
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
        }

    def write(self, path: Path, array: np.ndarray, nodata=None, tags: dict | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        arr = array if array.ndim == 3 else array[None]
        with rasterio.open(path, "w", **self.profile(str(arr.dtype), nodata, arr.shape[0])) as dst:
            dst.write(arr)
            if tags:
                dst.update_tags(**{k: str(v) for k, v in tags.items()})
        return path


def read(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read(1)
