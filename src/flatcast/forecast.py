"""The forecast job: 48 hourly maps of exposure probability, plus per-flat walkable windows."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd

from flatcast import flats as flats_mod
from flatcast import render
from flatcast.grid import Grid
from flatcast.hydro import load_hourly
from flatcast.log import console
from flatcast.models import exposure as E
from flatcast.models.waterlevel import ForecastInputs, load_forecaster, qcol
from flatcast.models.waterlevel import features as F
from flatcast.sources import coops
from flatcast.sources.coops import to_uv
from flatcast.sources.wind import get_provider
from flatcast.validate import Z90, apply_calibration

LOCAL_TZ = "America/Chicago"


def exposure_model_path(cfg):
    return cfg.paths.models / "exposure.npz"


def display_zone(model: E.ExposureModel) -> np.ndarray:
    """Pixels worth drawing: seen wet and dry several times, tipping within the observed range of levels."""
    lo, hi = model.level_range
    n_wet = model.n_obs - model.n_dry
    return (model.status == E.FITTED) & (model.z > lo) & (model.z < hi) & (model.n_dry >= 3) & (n_wet >= 3)


def _live_hourly(cfg, issue_pad_h: int):
    """Historical table topped up with the last three days of 6-minute data, extended into the future."""
    st = cfg.stations
    cache = cfg.paths.raw / "coops"
    hourly = load_hourly(cfg)
    for col, station in (("h", st.primary), ("h_gulf", st.gulf), ("h_south", st.south)):
        recent = coops.recent_water_level(station, cache, datum=st.datum)
        if len(recent):
            on_hour = recent[recent.index.minute == 0]
            hourly = hourly.reindex(hourly.index.union(on_hour.index))
            hourly[col] = hourly[col].fillna(on_hour.reindex(hourly.index))
    wind = coops.recent_wind(st.get("wind", st.primary), cache)
    if len(wind):
        on_hour = wind[wind.index.minute == 0]
        u, v = to_uv(on_hour["speed"], on_hour["direction"])
        hourly = hourly.reindex(hourly.index.union(on_hour.index))
        hourly["u_obs"] = hourly["u_obs"].fillna(pd.Series(u, index=on_hour.index).reindex(hourly.index))
        hourly["v_obs"] = hourly["v_obs"].fillna(pd.Series(v, index=on_hour.index).reindex(hourly.index))

    issue = hourly["h"].dropna().index.max()
    full = pd.date_range(hourly.index.min(), issue + pd.Timedelta(hours=issue_pad_h), freq="h")
    hourly = hourly.reindex(full)
    tide = coops.tide_predictions_hourly(st.gulf, issue - pd.Timedelta(days=2), issue + pd.Timedelta(hours=issue_pad_h + 2), cache, st.datum)
    hourly["tide"] = hourly["tide"].fillna(tide.reindex(hourly.index))
    return F.add_rolling(hourly, int(cfg.exposure.setup_window_hours)), issue


def _windows(times, frac, threshold, prob_hi):
    """Contiguous runs of hours when the flat counts as walkable."""
    out = []
    start = None
    for n, (t, f) in enumerate(zip(times, frac)):
        on = f >= threshold
        if on and start is None:
            start = n
        if (not on or n == len(frac) - 1) and start is not None:
            end = n if on else n - 1
            seg = slice(start, end + 1)
            out.append(
                {
                    "start": times[start].isoformat(),
                    "end": (times[end] + pd.Timedelta(hours=1)).isoformat(),
                    "hours": end - start + 1,
                    "peak_exposed": round(float(np.max(frac[seg])), 3),
                    "confidence": "high" if np.min(prob_hi[seg]) >= threshold else "moderate",
                }
            )
            start = None
    return out


def run(cfg) -> dict:
    paths = cfg.paths.ensure()
    hours = int(cfg.forecast.hours)
    hourly, issue = _live_hourly(cfg, hours + 2)
    lon, lat = cfg.wind.point
    provider = get_provider(cfg)
    fc = provider.forecast(lon, lat, hours)
    u, v = to_uv(fc["speed"], fc["direction"])
    fwind = pd.DataFrame({"u": u, "v": v, "p": fc["pressure"].to_numpy()}, index=fc.index)
    sources = {1: fwind, 2: fwind}
    forecaster = load_forecaster(cfg)
    pred = apply_calibration(forecaster.predict(ForecastInputs(hourly, sources), pd.DatetimeIndex([issue]), hours), cfg).loc[issue]

    tx, ty = E.wind_stress(fwind["u"], fwind["v"])
    window = int(cfg.exposure.setup_window_hours)
    f_tau = pd.DataFrame({"x": tx, "y": ty}, index=fwind.index).rolling(window, min_periods=1).mean()

    out_root = paths.outputs / "forecast"
    run_dir = out_root / f"{issue:%Y%m%dT%H}Z"
    run_dir.mkdir(parents=True, exist_ok=True)

    model = None
    if exposure_model_path(cfg).exists():
        model = E.ExposureModel.load(exposure_model_path(cfg))
    grid = Grid.from_config(cfg)
    zone = display_zone(model) if model is not None else None
    fc_flats = flats_mod.load_or_create(cfg, grid, zone, model.z) if model is not None else None
    flat_masks = flats_mod.masks(fc_flats, grid) if fc_flats else []
    px_km2 = (grid.res / 1000) ** 2
    p_thr = float(cfg.walkable.p_dry)

    frames = []
    flat_series = {name: {"mid": [], "hi": []} for name, _, _ in flat_masks}
    for lead, row in pred.iterrows():
        t = row["valid_time"]
        mid, lo, hi = row[qcol(0.5)], row[qcol(0.1)], row[qcol(0.9)]
        sigma = max((hi - lo) / Z90, 0.0)
        w = fwind.reindex([t]).iloc[0]
        frame = {
            "lead": int(lead),
            "time": t.isoformat(),
            "q10": round(float(lo), 3),
            "q50": round(float(mid), 3),
            "q90": round(float(hi), 3),
            "wind_ms": round(float(np.hypot(w["u"], w["v"])), 1) if np.isfinite(w["u"]) else None,
            "wind_from_deg": round(float(np.degrees(np.arctan2(-w["u"], -w["v"])) % 360)) if np.isfinite(w["u"]) else None,
        }
        # What the browser needs to redraw the map itself: level uncertainty and wind-tilt stress.
        tau = np.nan_to_num(f_tau.reindex([t]).to_numpy()[0])
        frame.update(sigma=round(float(sigma), 4), tau_x=round(float(tau[0]), 2), tau_y=round(float(tau[1]), 2))
        if model is not None:
            p = model.p_dry(mid, tau, sigma)
            # "High confidence" uses the low-water end of the band: exposed even if the water runs high.
            p_hi_water = model.p_dry(hi, tau, 0.0)
            tif = run_dir / f"exposure_{int(lead):02d}h.tif"
            grid.write(tif, np.where(np.isfinite(p), np.round(p * 100), 255).astype(np.uint8), nodata=255, tags={"valid_time": t.isoformat(), "units": "percent chance exposed"})
            png = run_dir / "png" / f"exposure_{int(lead):02d}h.png"
            render.save_png(render.exposure_rgba(p, show=zone), png)
            frame["png"] = png.name
            frame["exposed_km2"] = round(float(np.count_nonzero((p >= p_thr) & zone)) * px_km2, 2)
            for name, m, _ in flat_masks:
                flat_series[name]["mid"].append(float(np.mean(p[m] >= p_thr)))
                flat_series[name]["hi"].append(float(np.mean(p_hi_water[m] >= 0.5)))
        frames.append(frame)

    times = pd.DatetimeIndex([pd.Timestamp(f["time"]) for f in frames])
    flats_out = []
    for name, m, props in flat_masks:
        mid = np.array(flat_series[name]["mid"])
        hi = np.array(flat_series[name]["hi"])
        flats_out.append(
            {
                "name": name,
                "area_km2": props.get("area_km2"),
                "half_dry_level_m": props.get("half_dry_level_m"),
                "exposed": [round(float(x), 3) for x in mid],
                "windows": _windows(times, mid, float(cfg.walkable.min_area_fraction), hi),
            }
        )

    observed = hourly["h"].loc[issue - pd.Timedelta(hours=48) : issue].dropna()
    result = {
        "issue_time": issue.isoformat(),
        "generated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "timezone": LOCAL_TZ,
        "station": {"id": cfg.stations.primary, "name": "S. Bird Island", "datum": "NAVD88"},
        "wind_provider": provider.name,
        "waterlevel_model": forecaster.name,
        "maps": model is not None,
        "corners": grid.corners_lonlat(),
        "walkable": {"p_dry": p_thr, "min_area_fraction": float(cfg.walkable.min_area_fraction)},
        "observed": [{"time": t.isoformat(), "h": round(float(h), 3)} for t, h in observed.items()],
        "hours": frames,
        "flats": flats_out,
    }
    (run_dir / "forecast.json").write_text(json.dumps(result, indent=1))
    latest = out_root / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(run_dir, latest)
    msg = f"Forecast: issued {issue:%Y-%m-%d %H:%M} UTC, level {pred[qcol(0.5)].iloc[0]:+.2f} m -> {pred[qcol(0.5)].iloc[-1]:+.2f} m in {hours} h"
    if model is None:
        msg += " (exposure maps need the satellite stages first)"
    console.log(msg)
    return result
