"""Colours and image rendering shared by the maps, the validation figures and the site."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, ListedColormap  # noqa: E402
from PIL import Image  # noqa: E402

from flatcast.imagery.water_index import AMBIGUOUS, DRY, NODATA, WATER  # noqa: E402

# Flooded (deep teal) -> uncertain (pale shell) -> exposed (warm sand)
EXPOSURE_STOPS = [(0.0, "#145c7a"), (0.2, "#3f8fa8"), (0.5, "#efe6cf"), (0.8, "#e0b25c"), (1.0, "#c9792b")]
EXPOSURE_CMAP = LinearSegmentedColormap.from_list("exposure", [(p, c) for p, c in EXPOSURE_STOPS])
MASK_CMAP = ListedColormap(["#d8b46a", "#1f6f95", "#9fb8a8"])  # dry, water, ambiguous
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "figure.dpi": 110,
    }
)


def exposure_rgba(p: np.ndarray, show: np.ndarray | None = None, alpha: float = 0.85) -> np.ndarray:
    """P(dry) -> RGBA uint8. Pixels outside `show` (or NaN) are transparent."""
    rgba = (EXPOSURE_CMAP(np.nan_to_num(p, nan=0.0)) * 255).astype(np.uint8)
    a = np.full(p.shape, int(alpha * 255), dtype=np.uint8)
    # Confidently flooded pixels are lighter so the basemap water still reads as water.
    a = np.where(np.nan_to_num(p) < 0.2, int(alpha * 255 * 0.55), a).astype(np.uint8)
    hide = ~np.isfinite(p)
    if show is not None:
        hide |= ~show
    a[hide] = 0
    rgba[..., 3] = a
    return rgba


def save_png(rgba: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
    return path


def mask_rgb(cls: np.ndarray) -> np.ndarray:
    out = np.full((*cls.shape, 3), 0.92)
    out[cls == DRY] = (0.85, 0.71, 0.42)
    out[cls == WATER] = (0.12, 0.44, 0.58)
    out[cls == AMBIGUOUS] = (0.62, 0.72, 0.66)
    out[cls == NODATA] = (0.92, 0.92, 0.92)
    return out


def agreement_rgb(actual: np.ndarray, p: np.ndarray, zone: np.ndarray) -> np.ndarray:
    """Green = both right, orange = predicted flooded but was dry, purple = predicted dry but was wet."""
    out = np.full((*actual.shape, 3), 0.94)
    valid = zone & ((actual == DRY) | (actual == WATER)) & np.isfinite(p)
    pred_dry = p >= 0.5
    act_dry = actual == DRY
    out[valid & pred_dry & act_dry] = (0.80, 0.68, 0.38)
    out[valid & ~pred_dry & ~act_dry] = (0.35, 0.58, 0.68)
    out[valid & ~pred_dry & act_dry] = (0.93, 0.45, 0.13)
    out[valid & pred_dry & ~act_dry] = (0.55, 0.25, 0.65)
    return out


def axes_off(ax):
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
