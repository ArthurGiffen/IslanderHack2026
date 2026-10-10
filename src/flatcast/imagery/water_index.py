"""MNDWI water index and the water / ambiguous / dry classification.

MNDWI = (green - SWIR1) / (green + SWIR1)   (Xu 2006)
Open water absorbs SWIR strongly, so it scores high; dry sand and vegetation
score low. Wet sand, algal mats and a film of water fall in between, which is
why FlatCast keeps a separate "ambiguous" class instead of a single cut.
"""

from __future__ import annotations

import numpy as np

DRY = 0
WATER = 1
AMBIGUOUS = 2
NODATA = 255

REFLECTANCE_FILL = -9999
# Atmospheric correction can push SWIR over water slightly below zero.
# Clamp to a tiny positive reflectance (scaled units) so the ratio stays defined.
MIN_SCALED_REFLECTANCE = 1


def mndwi(green: np.ndarray, swir1: np.ndarray, fill: int = REFLECTANCE_FILL) -> np.ndarray:
    """MNDWI from HLS scaled reflectance (int16, scale 1e-4). NaN where either band is fill."""
    g = np.asarray(green, dtype=np.float32)
    s = np.asarray(swir1, dtype=np.float32)
    invalid = (g == fill) | (s == fill) | ~np.isfinite(g) | ~np.isfinite(s)
    g = np.maximum(g, MIN_SCALED_REFLECTANCE)
    s = np.maximum(s, MIN_SCALED_REFLECTANCE)
    out = (g - s) / (g + s)
    out[invalid] = np.nan
    return out


def classify(index: np.ndarray, water_above: float, dry_below: float, clear: np.ndarray | None = None) -> np.ndarray:
    if dry_below > water_above:
        raise ValueError("dry_below must be <= water_above")
    out = np.full(index.shape, AMBIGUOUS, dtype=np.uint8)
    out[index >= water_above] = WATER
    out[index <= dry_below] = DRY
    nodata = ~np.isfinite(index)
    if clear is not None:
        nodata |= ~clear
    out[nodata] = NODATA
    return out


def otsu_threshold(index: np.ndarray, valid: np.ndarray | None = None) -> float:
    """Automatic split between the water and land modes, as a cross-check on the fixed thresholds."""
    from skimage.filters import threshold_otsu

    v = index[np.isfinite(index)] if valid is None else index[valid & np.isfinite(index)]
    if v.size < 100 or np.ptp(v) == 0:
        return float("nan")
    return float(threshold_otsu(np.clip(v, -1, 1), nbins=256))


def encode(index: np.ndarray) -> np.ndarray:
    """Store MNDWI compactly as int16 (x10000, -32768 = no data)."""
    out = np.full(index.shape, -32768, dtype=np.int16)
    ok = np.isfinite(index)
    out[ok] = np.round(np.clip(index[ok], -1, 1) * 10000).astype(np.int16)
    return out


def decode(stored: np.ndarray) -> np.ndarray:
    out = stored.astype(np.float32) / 10000
    out[stored == -32768] = np.nan
    return out
