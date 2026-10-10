"""Long-term view: which flats would be permanently flooded under sea-level rise.

For each pixel, FlatCast computes how often it would be exposed if the recent
range of water levels (the last few years of the gauge record) were shifted up
by 10, 25 or 50 cm. "Permanently flooded" means exposed less than 5% of the
time. This is a "bathtub" view: it assumes the flats stay where they are, but
in reality they can build up with sediment, erode or move. Treat it as an
illustration of sensitivity, not a prediction of a particular year.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.special import expit

from flatcast import render
from flatcast.grid import Grid
from flatcast.log import console
from flatcast.models import exposure as E

plt = render.plt


def exposure_fraction(model: E.ExposureModel, levels: np.ndarray, shift_m: float) -> np.ndarray:
    """Share of time each pixel is exposed for the given level distribution (calm winds)."""
    edges = np.arange(np.floor(levels.min() * 100) / 100, levels.max() + 0.02, 0.01)
    counts, _ = np.histogram(levels, bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2
    weights = counts / counts.sum()
    k = np.exp(model.logk)
    frac = np.zeros(model.shape, dtype=np.float64)
    for c, w in zip(centers, weights):
        if w > 0:
            frac += w * expit(k * (model.z - (c + shift_m)))
    frac = frac.astype(np.float32)
    frac[model.status != E.FITTED] = np.nan
    return frac


def run(cfg, model: E.ExposureModel, hourly: pd.DataFrame, flat_masks=None) -> dict:
    grid = Grid.from_config(cfg)
    years = int(cfg.slr.climate_years)
    recent = hourly["h"].dropna()
    recent = recent[recent.index >= recent.index.max() - pd.DateOffset(years=years)].to_numpy()
    out = cfg.paths.outputs / "slr"
    out.mkdir(parents=True, exist_ok=True)
    perm = float(cfg.slr.permanent_p_dry)
    px_km2 = (grid.res / 1000) ** 2

    base = exposure_fraction(model, recent, 0.0)
    exposed_now = np.nan_to_num(base) >= perm
    scenarios = {"0": base}
    for cm in cfg.slr.scenarios_cm:
        scenarios[str(cm)] = exposure_fraction(model, recent, cm / 100)

    summary = {"climate_years": years, "permanent_threshold": perm, "exposed_today_km2": round(float(exposed_now.sum()) * px_km2, 2), "scenarios": []}
    for name, frac in scenarios.items():
        grid.write(out / f"exposure_fraction_slr{name}cm.tif", np.nan_to_num(frac, nan=-1).astype(np.float32), nodata=-1)
        lost = exposed_now & (np.nan_to_num(frac) < perm)
        rgba = render.exposure_rgba(frac, show=exposed_now | (np.nan_to_num(frac) > 0.0))
        rgba[lost] = (176, 40, 64, 200)  # newly permanently flooded: crimson
        render.save_png(rgba, out / f"slr_{name}cm.png")
        entry = {
            "rise_cm": int(name),
            "permanently_flooded_km2": round(float(lost.sum()) * px_km2, 2),
            "share_of_today_lost": round(float(lost.sum()) / max(int(exposed_now.sum()), 1), 3),
            "png": f"slr_{name}cm.png",
        }
        if flat_masks:
            entry["flats"] = [
                {"name": n, "share_lost": round(float((lost & m).sum()) / max(int((exposed_now & m).sum()), 1), 3)} for n, m, _ in flat_masks
            ]
        summary["scenarios"].append(entry)

    fig, axes = plt.subplots(1, len(scenarios), figsize=(4 * len(scenarios), 4.4))
    for ax, (name, frac) in zip(axes, scenarios.items()):
        img = render.exposure_rgba(frac, show=exposed_now | (np.nan_to_num(frac) > 0))
        lost = exposed_now & (np.nan_to_num(frac) < perm)
        img[lost] = (176, 40, 64, 255)
        ax.set_facecolor("#dfe8ec")
        ax.imshow(img, interpolation="nearest")
        ax.set_title("Today" if name == "0" else f"+{name} cm sea level", fontsize=11)
        render.axes_off(ax)
    fig.suptitle("Share of time each flat is exposed (crimson: exposed today, permanently flooded under the scenario)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "slr_panels.png")
    plt.close(fig)
    (out / "slr_summary.json").write_text(json.dumps(summary, indent=2))
    console.log("Sea level: " + ", ".join(f"+{s['rise_cm']} cm -> {s['permanently_flooded_km2']} km2 lost" for s in summary["scenarios"][1:]))
    return summary
