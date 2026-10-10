"""Generates 01_masks_vs_gauge.ipynb (kept as code so the notebook stays reviewable in git)."""

from pathlib import Path

import nbformat as nbf

cells = [
    nbf.v4.new_markdown_cell(
        "# FlatCast · Phase 1: wet/dry masks next to the gauge record\n\n"
        "Each clear satellite image has been turned into a water / ambiguous / dry mask. This notebook checks the "
        "core idea behind FlatCast: **the lower the water at the Bird Island gauge, the more flat is exposed**, and "
        "wind explains much of what's left.\n\n"
        "Run `uv run flatcast run --only gauges imagery masks pairs` first."
    ),
    nbf.v4.new_code_cell(
        "import numpy as np, pandas as pd, matplotlib.pyplot as plt\n"
        "from flatcast.config import load_config\n"
        "from flatcast.hydro import load_hourly\n"
        "from flatcast.pairing import load_pairs\n"
        "from flatcast.imagery.masks import load_stack\n"
        "from flatcast import render\n\n"
        "cfg = load_config('../config.yaml')\n"
        "hourly = load_hourly(cfg)\n"
        "pairs = load_pairs(cfg)\n"
        "pairs = pairs[pairs['valid']].copy()\n"
        "pairs['time'] = pd.to_datetime(pairs['time'], utc=True)\n"
        "print(f\"{len(pairs)} clear images paired with the gauge, {pairs.time.min():%Y-%m-%d} to {pairs.time.max():%Y-%m-%d}\")\n"
        "pairs[['time', 'sensor', 'clear_frac', 'h', 'speed', 'frac_dry', 'frac_water', 'frac_ambiguous']].tail()"
    ),
    nbf.v4.new_markdown_cell("## The gauge record, with every satellite image on it\nDots are coloured by how much of the area the satellite saw as dry."),
    nbf.v4.new_code_cell(
        "fig, ax = plt.subplots(figsize=(14, 4.5))\n"
        "daily = hourly['h'].resample('D').mean()\n"
        "ax.plot(daily.index, daily, color='#9aa7ad', lw=0.7, label='Bird Island daily mean')\n"
        "sc = ax.scatter(pairs['time'], pairs['h'], c=pairs['frac_dry'], cmap=render.EXPOSURE_CMAP, s=22, edgecolor='k', linewidth=0.3, zorder=3)\n"
        "fig.colorbar(sc, ax=ax, label='Share of area dry')\n"
        "ax.set_ylabel('Water level (m NAVD88)')\n"
        "ax.set_title('Satellite overpasses on the Bird Island water-level record')\n"
        "ax.legend(frameon=False)\n"
        "plt.show()"
    ),
    nbf.v4.new_markdown_cell("## Dry area against water level\nIf FlatCast's premise holds, this is a tight, falling curve. Colour shows the along-lagoon wind (north-south component): wind tilts the water, which spreads the points."),
    nbf.v4.new_code_cell(
        "fig, ax = plt.subplots(figsize=(8, 5.5))\n"
        "sc = ax.scatter(pairs['h'], pairs['frac_dry'], c=pairs['v24'], cmap='coolwarm', vmin=-10, vmax=10, s=24, edgecolor='k', linewidth=0.3)\n"
        "fig.colorbar(sc, ax=ax, label='24-h mean wind toward north (m/s)')\n"
        "ax.set_xlabel('Gauge water level at overpass (m NAVD88)')\n"
        "ax.set_ylabel('Share of area classified dry')\n"
        "r = np.corrcoef(pairs['h'], pairs['frac_dry'])[0, 1]\n"
        "ax.set_title(f'Lower water, more exposed flat (r = {r:.2f})')\n"
        "plt.show()"
    ),
    nbf.v4.new_markdown_cell("## Masks across the range of water levels\nEight clear images from the lowest to the highest water."),
    nbf.v4.new_code_cell(
        "clear = pairs[pairs['clear_frac'] > 0.85].sort_values('h')\n"
        "pick = clear.iloc[np.linspace(0, len(clear) - 1, 8).round().astype(int)]\n"
        "masks = load_stack(cfg, list(pick.index))\n"
        "fig, axes = plt.subplots(2, 4, figsize=(16, 8.5))\n"
        "for ax, m, (_, row) in zip(axes.flat, masks, pick.iterrows()):\n"
        "    ax.imshow(render.mask_rgb(m), interpolation='nearest')\n"
        "    ax.set_title(f\"{row['time'].tz_convert('America/Chicago'):%d %b %Y}\\ngauge {row['h']:+.2f} m · wind {row['speed']:.0f} m/s\", fontsize=10)\n"
        "    render.axes_off(ax)\n"
        "fig.suptitle('Sand = dry, teal = water, grey-green = ambiguous, light grey = cloud')\n"
        "plt.tight_layout(); plt.show()"
    ),
    nbf.v4.new_markdown_cell("## How often is each pixel dry?\nThe flats that matter are the pixels that are neither always wet nor always dry."),
    nbf.v4.new_code_cell(
        "allm = load_stack(cfg, list(pairs.index))\n"
        "valid = (allm == 0) | (allm == 1)\n"
        "freq = np.where(valid.sum(0) > 10, (allm == 0).sum(0) / np.maximum(valid.sum(0), 1), np.nan)\n"
        "fig, ax = plt.subplots(figsize=(8, 7.5))\n"
        "im = ax.imshow(freq, cmap=render.EXPOSURE_CMAP, vmin=0, vmax=1)\n"
        "fig.colorbar(im, ax=ax, label='Share of clear images in which the pixel was dry')\n"
        "render.axes_off(ax); ax.set_title('Exposure frequency, all years'); plt.show()\n"
        "print(f\"Intermittently exposed (dry 5-95% of the time): {np.nansum((freq > 0.05) & (freq < 0.95)) * 0.0009:.1f} km2\")"
    ),
]

nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3"}})
nbf.write(nb, Path(__file__).with_name("01_masks_vs_gauge.ipynb"))
print("wrote 01_masks_vs_gauge.ipynb")
