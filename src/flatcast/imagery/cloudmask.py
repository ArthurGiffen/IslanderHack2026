"""Decode the HLS v2.0 Fmask quality band.

Bit layout (HLS v2.0 user guide, Table 9):
  0 cirrus
  1 cloud
  2 adjacent to cloud or cloud shadow
  3 cloud shadow
  4 snow / ice
  5 water
  6-7 aerosol level (0 climatology, 1 low, 2 moderate, 3 high)
A value of 255 is fill (no data).
"""

from __future__ import annotations

import numpy as np

CIRRUS = 1 << 0
CLOUD = 1 << 1
ADJACENT = 1 << 2
SHADOW = 1 << 3
SNOW = 1 << 4
WATER = 1 << 5
AEROSOL_SHIFT = 6
FILL = 255


def decode(fmask: np.ndarray) -> dict[str, np.ndarray]:
    f = np.asarray(fmask).astype(np.uint8)
    fill = f == FILL
    out = {
        name: ((f & bit) > 0) & ~fill
        for name, bit in (("cirrus", CIRRUS), ("cloud", CLOUD), ("adjacent", ADJACENT), ("shadow", SHADOW), ("snow", SNOW), ("water", WATER))
    }
    out["aerosol"] = np.where(fill, 0, (f >> AEROSOL_SHIFT) & 0b11).astype(np.uint8)
    out["fill"] = fill
    return out


def clear_mask(
    fmask: np.ndarray,
    *,
    mask_adjacent: bool = True,
    mask_cirrus: bool = True,
    mask_snow: bool = False,
    mask_high_aerosol: bool = False,
) -> np.ndarray:
    """True where the pixel is usable ground or water."""
    d = decode(fmask)
    bad = d["fill"] | d["cloud"] | d["shadow"]
    if mask_adjacent:
        bad |= d["adjacent"]
    if mask_cirrus:
        bad |= d["cirrus"]
    if mask_snow:
        bad |= d["snow"]
    if mask_high_aerosol:
        bad |= d["aerosol"] == 3
    return ~bad


def clear_mask_from_config(fmask: np.ndarray, icfg) -> np.ndarray:
    return clear_mask(
        fmask,
        mask_adjacent=icfg.mask_adjacent,
        mask_cirrus=icfg.mask_cirrus,
        mask_snow=icfg.mask_snow,
        mask_high_aerosol=icfg.mask_high_aerosol,
    )


def clear_fraction(clear: np.ndarray) -> float:
    """Share of the whole analysis area that is clear (fill counts as not clear)."""
    return float(np.count_nonzero(clear)) / clear.size if clear.size else 0.0
