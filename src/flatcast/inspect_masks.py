"""Contact sheets for checking the MNDWI thresholds by eye.

Picks images spread across the range of gauge levels and shows, for each one,
true colour, the MNDWI index, the resulting classes and the MNDWI histogram
with the configured thresholds and the automatic (Otsu) split. If the classes
don't match what you see in the true-colour image, adjust
imagery.mndwi.water_above / dry_below in config.yaml and rerun; only the
classification is redone, nothing is downloaded again.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flatcast import render
from flatcast.grid import Grid
from flatcast.imagery.masks import load_catalog, load_mndwi, load_stack
from flatcast.log import console
from flatcast.pairing import load_pairs
from flatcast.sources import hls

plt = render.plt


def _rgb(scene, grid, fs):
    bands = [hls.read_band(scene, b, grid, fs).astype(np.float32) for b in ("red", "green", "blue")]
    rgb = np.stack(bands, -1)
    rgb[rgb == hls.REFLECTANCE_NODATA] = np.nan
    rgb = np.clip(rgb / 10000 * 3.2, 0, 1) ** 0.85
    return np.nan_to_num(rgb, nan=0.9)


def run(cfg, n: int = 12, per_sheet: int = 3) -> list:
    grid = Grid.from_config(cfg)
    th = cfg.imagery.mndwi
    out = cfg.paths.outputs / "inspection"
    out.mkdir(parents=True, exist_ok=True)

    cat = load_catalog(cfg)
    used = cat[cat["used"].astype(bool)]
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    ax.hist(used["otsu"].dropna(), bins=40, color="#9fb8a8")
    ax.axvline(th.water_above, color="#1f6f95", lw=2, label=f"water_above = {th.water_above}")
    ax.axvline(th.dry_below, color="#c9792b", lw=2, label=f"dry_below = {th.dry_below}")
    med = float(used["otsu"].median())
    ax.axvline(med, color="#333", ls="--", lw=1.4, label=f"median Otsu = {med:.2f}")
    ax.set_xlabel("Automatic (Otsu) water/land split per scene (MNDWI)")
    ax.set_ylabel("Scenes")
    ax.set_title("Do the fixed thresholds bracket the automatic split?")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "otsu_summary.png")
    plt.close(fig)

    pairs = load_pairs(cfg)
    pairs = pairs[pairs["valid"] & (pairs["clear_frac"] > 0.85)].sort_values("h")
    if pairs.empty:
        return []
    pick = pairs.iloc[np.unique(np.linspace(0, len(pairs) - 1, n).round().astype(int))]
    scenes = {s.key: s for s in hls.search(cfg)}
    fs = hls.login()
    masks = load_stack(cfg, list(pick.index))
    sheets = []
    for start in range(0, len(pick), per_sheet):
        chunk = pick.iloc[start : start + per_sheet]
        fig, axes = plt.subplots(len(chunk), 4, figsize=(17, 4.3 * len(chunk)), gridspec_kw={"width_ratios": [1, 1, 1, 1.15]})
        axes = np.atleast_2d(axes)
        for r, (key, row) in enumerate(chunk.iterrows()):
            idx = load_mndwi(cfg, key)
            cls = masks[start + r]
            axes[r, 0].imshow(_rgb(scenes[key], grid, fs))
            im = axes[r, 1].imshow(idx, cmap="RdBu", vmin=-0.6, vmax=0.6)
            axes[r, 2].imshow(render.mask_rgb(cls), interpolation="nearest")
            v = idx[np.isfinite(idx)]
            axes[r, 3].hist(v, bins=120, range=(-0.8, 0.9), color="#7d8f99")
            for x, c in ((th.water_above, "#1f6f95"), (th.dry_below, "#c9792b")):
                axes[r, 3].axvline(x, color=c, lw=2)
            if np.isfinite(row["otsu"]):
                axes[r, 3].axvline(row["otsu"], color="#333", ls="--", lw=1.2)
            t = pd.Timestamp(row["time"]).tz_convert("America/Chicago")
            axes[r, 0].set_ylabel(f"{t:%d %b %Y}\ngauge {row['h']:+.2f} m", fontsize=11)
            for c in range(3):
                render.axes_off(axes[r, c])
            axes[r, 0].yaxis.label.set_visible(True)
            if r == 0:
                for c, title in enumerate(["True colour", "MNDWI", "Classes", "MNDWI histogram"]):
                    axes[r, c].set_title(title)
        fig.colorbar(im, ax=axes[:, 1].tolist(), shrink=0.6, label="MNDWI")
        fig.text(0.01, 0.005, "Classes: sand = dry, teal = water, grey-green = ambiguous (excluded), light grey = cloud / no data", fontsize=9, color="#555")
        p = out / f"contact_sheet_{start // per_sheet + 1:02d}.png"
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        sheets.append(p)
    console.log(f"Inspection: {len(sheets)} contact sheets and an Otsu summary written to {out}")
    return sheets
