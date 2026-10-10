"""Which pixels belong to the lagoon and its shores.

The open Gulf of Mexico is inside most bounding boxes along Padre Island, and
its surface (sun glint, turbid or Sargassum-covered water, especially in
summer) sometimes scores low enough on MNDWI to look "dry". Left in, those
flickering ocean pixels look like flats that flood and drain.

The lagoon is found from the satellite record itself: the body of
persistent water that contains the primary gauge. The analysis region is that
water plus a band of shoreline, minus anything closer to another large water
body (the Gulf) than to the lagoon.
"""

from __future__ import annotations

import numpy as np
from pyproj import Transformer
from scipy import ndimage

from flatcast.grid import Grid
from flatcast.imagery.water_index import DRY, WATER


def persistent_water(masks: np.ndarray, min_obs: int = 15, share: float = 0.9) -> np.ndarray:
    valid = (masks == DRY) | (masks == WATER)
    n = valid.sum(0)
    wet = (masks == WATER).sum(0)
    return (n >= min_obs) & (wet >= share * np.maximum(n, 1))


def lagoon_region(masks: np.ndarray, grid: Grid, gauge_lonlat: tuple[float, float], shore_km: float = 1.5, min_other_km2: float = 5.0) -> np.ndarray:
    water = persistent_water(masks)
    water = ndimage.binary_opening(water, iterations=1)
    labels, n = ndimage.label(water)
    x, y = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform(*gauge_lonlat)
    col, row = ~grid.transform * (x, y)
    r, c = int(row), int(col)
    # The gauge sits on a spoil island in the channel: take the nearest persistent-water component.
    if not (0 <= r < grid.height and 0 <= c < grid.width):
        raise ValueError("primary gauge lies outside the bounding box")
    dist, (ir, ic) = ndimage.distance_transform_edt(labels == 0, return_indices=True)
    lagoon_label = labels[ir[r, c], ic[r, c]]
    lagoon = labels == lagoon_label

    px_km = grid.res / 1000
    sizes = ndimage.sum(water, labels, index=np.arange(1, n + 1)) * px_km**2
    others = np.isin(labels, [i + 1 for i, s in enumerate(sizes) if s >= min_other_km2 and i + 1 != lagoon_label])

    d_lagoon = ndimage.distance_transform_edt(~lagoon) * px_km
    d_other = ndimage.distance_transform_edt(~others) * px_km if others.any() else np.full(lagoon.shape, np.inf)
    return (d_lagoon <= shore_km) & (d_lagoon < d_other)
