"""Find the individual flats and give them names.

There is no official list of named flats here, so FlatCast draws them from the
fitted model: connected areas that go dry and flood within the normal range of
water levels. They are written once to config/flats.geojson with placeholder
names (Flat A, Flat B, ... from north to south). Rename them there, or replace
the file with your own outlines; FlatCast never overwrites an existing file.
"""

from __future__ import annotations

import json
import string

import numpy as np
from pyproj import Transformer
from rasterio import features
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union
from skimage import morphology

from flatcast.grid import Grid
from flatcast.log import console


def _name(i: int) -> str:
    letters = string.ascii_uppercase
    return f"Flat {letters[i]}" if i < 26 else f"Flat {letters[i // 26 - 1]}{letters[i % 26]}"


def detect(cfg, grid: Grid, zone: np.ndarray, z: np.ndarray) -> dict:
    px_km2 = (grid.res / 1000) ** 2
    min_px = int(cfg.walkable.min_flat_area_km2 / px_km2)
    clean = morphology.closing(zone, morphology.disk(2))
    clean = morphology.remove_small_holes(clean, max_size=50)
    labels = morphology.label(clean, connectivity=2)
    to_lonlat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform

    regions = []
    for lab in range(1, labels.max() + 1):
        m = labels == lab
        if m.sum() < min_px:
            continue
        polys = [shape(g) for g, v in features.shapes(m.astype(np.uint8), mask=m, transform=grid.transform) if v == 1]
        geom = unary_union(polys).simplify(grid.res * 1.5, preserve_topology=True)
        geom_ll = shp_transform(to_lonlat, geom)
        regions.append(
            {
                "geom": geom_ll,
                "area_km2": round(float(m.sum()) * px_km2, 2),
                "median_z": round(float(np.nanmedian(z[m & zone])), 3),
                "lat": geom_ll.centroid.y,
            }
        )
    regions.sort(key=lambda r: -r["lat"])
    feats = [
        {
            "type": "Feature",
            "properties": {
                "name": _name(i),
                "area_km2": r["area_km2"],
                "half_dry_level_m": r["median_z"],
                "note": "auto-detected; rename freely",
            },
            "geometry": mapping(r["geom"]),
        }
        for i, r in enumerate(regions)
    ]
    return {"type": "FeatureCollection", "features": feats}


def load_or_create(cfg, grid: Grid, zone: np.ndarray, z: np.ndarray) -> dict:
    path = cfg.paths.flats
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    fc = detect(cfg, grid, zone, z)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc, indent=1), encoding="utf-8")
    console.log(f"Flats: {len(fc['features'])} flats detected and written to {path.name}; rename them there")
    return fc


def masks(fc: dict, grid: Grid) -> list[tuple[str, np.ndarray, dict]]:
    """Rasterize each flat onto the grid: [(name, mask, properties)]."""
    to_grid = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform
    out = []
    for f in fc["features"]:
        geom = shp_transform(to_grid, shape(f["geometry"]))
        m = features.rasterize([(geom, 1)], out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8").astype(bool)
        if m.any():
            out.append((f["properties"].get("name", "Flat"), m, f["properties"]))
    return out
