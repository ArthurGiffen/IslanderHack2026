"""Pipeline stages, in order. Each stage reads what earlier stages cached, so any
stage can be rerun alone and a full rerun only does work that is out of date."""

from __future__ import annotations

import json
from typing import Callable

import numpy as np
import pandas as pd
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from flatcast import render
from flatcast.grid import Grid
from flatcast.log import console
from flatcast.sources import hls


class Skip(Exception):
    """A stage can't run yet (e.g. no Earthdata login); later stages carry on where they can."""


# ---- Phase 1: data ---------------------------------------------------------------------------


def stage_gauges(cfg):
    from flatcast.hydro import build_hourly

    build_hourly(cfg)


def stage_imagery(cfg):
    from flatcast.imagery.masks import download_scenes, load_catalog

    if not hls.have_credentials():
        n = len(load_catalog(cfg))
        raise Skip(
            "no Earthdata login (set EARTHDATA_USERNAME and EARTHDATA_PASSWORD)"
            + (f"; using the {n} scenes already downloaded" if n else "")
        )
    download_scenes(cfg)


def _require_masks(cfg):
    from flatcast.imagery.masks import load_catalog

    cat = load_catalog(cfg)
    if cat.empty or not cat["used"].astype(bool).any():
        raise Skip("no satellite scenes yet (the imagery stage needs an Earthdata login)")


def stage_masks(cfg):
    from flatcast.imagery.masks import classify_all

    _require_masks(cfg)
    classify_all(cfg)


def stage_pairs(cfg):
    from flatcast.pairing import build_pairs

    _require_masks(cfg)
    build_pairs(cfg)


def stage_inspect(cfg):
    from flatcast import inspect_masks

    _require_masks(cfg)
    if not hls.have_credentials():
        raise Skip("contact sheets need the Earthdata login to fetch true-colour bands")
    try:
        inspect_masks.run(cfg)
    except Exception as exc:  # optional check: a network blip must not stop training
        raise Skip(f"contact sheets failed ({type(exc).__name__}); rerun with --only inspect") from exc


# ---- Phase 2: models and validation ----------------------------------------------------------


def stage_waterlevel(cfg):
    from flatcast.hydro import load_hourly
    from flatcast.models.waterlevel import features as F
    from flatcast.models.waterlevel import model_dir
    from flatcast.models.waterlevel.gbm import GBMForecaster
    from flatcast.validate import holdout_bounds

    hourly = F.add_rolling(load_hourly(cfg), int(cfg.exposure.setup_window_hours))
    start, _ = holdout_bounds(cfg)
    wl = cfg.waterlevel
    for name, end in (("validation", start), ("production", hourly.index.max())):
        with _progress() as prog:
            task = prog.add_task(f"Water level: training {name} models", total=len(wl.horizons_h) * len(wl.quantiles))
            m = GBMForecaster.train(hourly, wl.horizons_h, wl.quantiles, end=end, stride=int(wl.train_stride_h), progress=lambda *_: prog.advance(task))
        m.save(model_dir(cfg) / name)
    console.log(f"Water level: models trained (validation through {start.date()}, production through {hourly.index.max().date()})")


def stage_exposure(cfg):
    from flatcast.imagery.masks import load_stack
    from flatcast.models import exposure as E
    from flatcast.pairing import load_pairs
    from flatcast.sources.coops import station_lonlat
    from flatcast.validate import holdout_bounds

    _require_masks(cfg)
    grid = Grid.from_config(cfg)
    lon, lat = station_lonlat(cfg.stations.primary, cfg.paths.raw / "coops")
    dx, dy = grid.offsets_km(lon, lat)
    pairs = load_pairs(cfg)
    pairs = pairs[pairs["valid"]].sort_values("time")
    start, _ = holdout_bounds(cfg)
    times = pd.to_datetime(pairs["time"], utc=True)
    ex = cfg.exposure
    all_masks = load_stack(cfg, list(pairs.index))
    if cfg.area.get("lagoon_only", True):
        from flatcast.imagery.region import lagoon_region
        from flatcast.imagery.water_index import NODATA

        seed = tuple(cfg.area.get("water_seed") or (lon, lat))
        region = lagoon_region(all_masks, grid, seed, shore_km=float(cfg.area.get("shore_km", 1.5)))
        np.save(cfg.paths.models / "region.npy", region)
        grid.write(cfg.paths.outputs / "maps" / "analysis_region.tif", region.astype(np.uint8), tags={"meaning": "1 = lagoon and its shores (analysed), 0 = excluded (Gulf, inland)"})
        all_masks[:, ~region] = NODATA
        console.log(f"Exposure: analysing the lagoon and {cfg.area.get('shore_km', 1.5)} km of shore ({region.sum() * (grid.res / 1000) ** 2:.0f} km2); Gulf excluded")
    for name, sel in (("exposure_validation", (times < start).to_numpy()), ("exposure", np.ones(len(pairs), bool))):
        subset = pairs[sel]
        if len(subset) < 30:
            raise Skip(f"only {len(subset)} usable images; need at least 30 to fit")
        masks = all_masks[sel]
        rounds = int(ex.setup_rounds) if ex.fit_wind_setup else 1
        with _progress() as prog:
            task = prog.add_task(f"Exposure: fitting {name} on {len(subset)} images", total=rounds * 100)

            def tick(r, total_rounds, done, n):
                prog.update(task, completed=r * 100 + 100 * done / max(n, 1))

            model = E.fit(
                masks,
                subset["h"].to_numpy(),
                subset[["tau_x", "tau_y"]].to_numpy(),
                pd.to_datetime(subset["time"], utc=True).dt.tz_localize(None).to_numpy(),
                dx,
                dy,
                half_life_years=float(ex.recency_half_life_years),
                min_obs=int(ex.min_obs),
                iterations=int(ex.newton_iterations),
                rounds=rounds,
                fit_setup=bool(ex.fit_wind_setup),
                progress=tick,
            )
        model.save(cfg.paths.models / f"{name}.npz")
        del masks
    _exposure_maps(cfg, model, grid)


def _exposure_maps(cfg, model, grid):
    """The by-products people ask for first: the 'elevation' map and how the wind tilts the water."""
    from flatcast.models import exposure as E

    out = cfg.paths.outputs / "maps"
    out.mkdir(parents=True, exist_ok=True)
    z = np.where(model.status == E.FITTED, model.z, np.nan).astype(np.float32)
    grid.write(out / "half_dry_level_navd88_m.tif", np.nan_to_num(z, nan=-9999), nodata=-9999, tags={"meaning": "gauge-equivalent water level (m NAVD88) at which the pixel is dry half the time"})
    grid.write(out / "fit_status.tif", model.status, tags={"classes": json.dumps(E.STATUS_NAMES)})
    lo, hi = np.nanpercentile(z, [2, 98])
    fig, ax = render.plt.subplots(figsize=(8, 7.5))
    ax.set_facecolor("#dfe8ec")
    im = ax.imshow(z, cmap="terrain", vmin=lo, vmax=hi, interpolation="nearest")
    fig.colorbar(im, ax=ax, shrink=0.8, label="Water level at which the flat is half-exposed (m NAVD88)")
    s = model.slope((100.0, 0.0)) * 10, model.slope((0.0, 100.0)) * 10
    ax.set_title("Flat 'elevation' learned from satellite history")
    render.axes_off(ax)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.text(
        0.5, 0.015,
        f"Learned wind tilt of the water surface, per 10 km: a 10 m/s wind toward the east raises it {s[0][0]*100:+.1f} cm eastward "
        f"and {s[0][1]*100:+.1f} cm northward;\na 10 m/s wind toward the north, {s[1][0]*100:+.1f} cm eastward and {s[1][1]*100:+.1f} cm northward.",
        ha="center", fontsize=8.5, color="#444",
    )
    fig.savefig(out / "half_dry_level.png", bbox_inches="tight")
    render.plt.close(fig)
    fitted = int((model.status == E.FITTED).sum())
    console.log(f"Exposure: {fitted:,} pixels fitted; elevation map in {out}")


def stage_validate(cfg):
    from flatcast.models import exposure as E
    from flatcast.pairing import load_pairs
    from flatcast.validate import validate_exposure, validate_waterlevel

    summary = validate_waterlevel(cfg)
    for L in ("6", "24", "48"):
        m = summary.get("FlatCast, forecast winds", summary.get("FlatCast, perfect winds"))[L]
        p = summary["Persistence (no change)"][L]
        console.log(f"  {L:>2} h ahead: FlatCast {m['mae_cm']:.1f} cm mean error vs {p['mae_cm']:.1f} cm for 'no change'")
    path = cfg.paths.models / "exposure_validation.npz"
    if not path.exists():
        raise Skip("exposure validation needs the satellite stages")
    s = validate_exposure(cfg, E.ExposureModel.load(path), load_pairs(cfg))
    for mode in ("from_observed_gauge", "from_24h_forecast"):
        if s.get(mode):
            r = s[mode]
            console.log(f"  Exposure {mode.replace('_', ' ')}: accuracy {r['accuracy']:.1%} (climatology {r['climatology_accuracy']:.1%}) on {r['images']} images")


# ---- Phase 3: forecast and site --------------------------------------------------------------


def stage_forecast(cfg):
    from flatcast import forecast
    from flatcast.models.waterlevel import model_dir

    if cfg.waterlevel.model == "gbm" and not (model_dir(cfg) / "production" / "meta.json").exists():
        raise Skip("no trained water-level model for this area yet")
    forecast.run(cfg)


def stage_traps(cfg):
    from flatcast import traps
    from flatcast.forecast import exposure_model_path
    from flatcast.hydro import load_hourly
    from flatcast.models import exposure as E

    if not exposure_model_path(cfg).exists():
        raise Skip("suggested trap water needs the fitted exposure model")
    model = E.ExposureModel.load(exposure_model_path(cfg))
    region_path = cfg.paths.models / "region.npy"
    region = np.load(region_path) if region_path.exists() else model.status != E.SPARSE
    traps.run(cfg, model, region, load_hourly(cfg))


# ---- Phase 4: sea-level rise ------------------------------------------------------------------


def stage_slr(cfg):
    from flatcast import flats as flats_mod
    from flatcast import slr
    from flatcast.forecast import display_zone, exposure_model_path
    from flatcast.hydro import load_hourly
    from flatcast.models import exposure as E

    if not exposure_model_path(cfg).exists():
        raise Skip("sea-level view needs the fitted exposure model")
    model = E.ExposureModel.load(exposure_model_path(cfg))
    grid = Grid.from_config(cfg)
    fc = flats_mod.load_or_create(cfg, grid, display_zone(model), model.z)
    slr.run(cfg, model, load_hourly(cfg), flats_mod.masks(fc, grid))


def stage_site(cfg):
    from flatcast.publish import build_site

    build_site(cfg)


STAGES: dict[str, tuple[str, Callable]] = {
    "gauges": ("Download gauge water levels, wind and tides", stage_gauges),
    "imagery": ("Download HLS scenes, mask clouds, compute MNDWI", stage_imagery),
    "masks": ("Classify water / ambiguous / dry for each date", stage_masks),
    "pairs": ("Pair each image with gauge level and wind", stage_pairs),
    "inspect": ("Contact sheets for checking thresholds by eye", stage_inspect),
    "waterlevel": ("Train the water-level forecast models", stage_waterlevel),
    "exposure": ("Fit the per-pixel exposure model", stage_exposure),
    "validate": ("Score both models on the held-out year", stage_validate),
    "forecast": ("Make the 48-hour exposure forecast", stage_forecast),
    "slr": ("Sea-level-rise scenarios", stage_slr),
    "traps": ("Suggested crab-trap water", stage_traps),
    "site": ("Build the static web map", stage_site),
}


def _progress():
    return Progress(TextColumn("[progress.description]{task.description}"), BarColumn(), TextColumn("{task.percentage:>3.0f}%"), TimeElapsedColumn(), console=console, transient=True)


def run(cfg, stages: list[str]) -> dict[str, str]:
    results = {}
    for name in stages:
        title, fn = STAGES[name]
        console.rule(f"[bold]{name}[/] · {title}")
        try:
            fn(cfg)
            results[name] = "done"
        except Skip as s:
            console.log(f"[yellow]Skipped: {s}")
            results[name] = f"skipped: {s}"
    return results
