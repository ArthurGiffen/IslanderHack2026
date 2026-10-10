"""Which pixels belong to the lagoon (or bay) and its shores.

The open Gulf of Mexico is inside most bounding boxes along Padre and Mustang
Islands, and its surface (sun glint, turbid or Sargassum-covered water,
especially in summer) sometimes scores low enough on MNDWI to look "dry".
Left in, those flickering ocean pixels look like flats that flood and drain.

The water body is found from the satellite record itself: persistent water,
split into separate bodies wherever they only touch through a narrow channel
or pass (so a pass to the Gulf doesn't merge the two), keeping the body that
contains the seed point: the primary gauge, or `area.water_seed` when the
gauge lies outside the box. The analysis region is that water plus a band of
shoreline, minus anything closer to another large water body than to it.
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


def lagoon_region(
    masks: np.ndarray,
    grid: Grid,
    seed_lonlat: tuple[float, float],
    shore_km: float = 1.5,
    min_other_km2: float = 5.0,
    channel_cut_m: float = 150.0,
) -> np.ndarray:
    water = ndimage.binary_opening(persistent_water(masks), iterations=1)
    # Erode to break narrow connections (passes, channels), label the cores,
    # then give every water pixel to its nearest core.
    k = max(1, int(round(channel_cut_m / grid.res)))
    cores, n = ndimage.label(ndimage.binary_erosion(water, iterations=k))
    if n == 0:
        raise ValueError("no persistent water found in the box")
    _, (ir, ic) = ndimage.distance_transform_edt(cores == 0, return_indices=True)
    bodies = np.where(water, cores[ir, ic], 0)

    x, y = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform(*seed_lonlat)
    col, row = ~grid.transform * (x, y)
    r, c = int(row), int(col)
    if not (0 <= r < grid.height and 0 <= c < grid.width):
        raise ValueError("the water seed point lies outside the bounding box; set area.water_seed")
    seed_label = cores[ir[r, c], ic[r, c]]
    lagoon = bodies == seed_label

    px_km = grid.res / 1000
    sizes = ndimage.sum(water, bodies, index=np.arange(1, n + 1)) * px_km**2
    big_others = [i + 1 for i, s in enumerate(sizes) if s >= min_other_km2 and i + 1 != seed_label]
    others = np.isin(bodies, big_others)

    d_lagoon = ndimage.distance_transform_edt(~lagoon) * px_km
    d_other = ndimage.distance_transform_edt(~others) * px_km if others.any() else np.full(lagoon.shape, np.inf)
    return (d_lagoon <= shore_km) & (d_lagoon < d_other)
