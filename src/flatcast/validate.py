"""Hold-out validation.

Water level: forecasts issued every 6 h through the held-out year are scored
against the gauge at every lead, using the wind forecasts that were actually
issued at the time (Open-Meteo's archive of past runs), and compared with
two baselines that need no model at all.

Exposure: the exposure model is refitted without the held-out year. Each
held-out image is then predicted twice, from the observed gauge level (tests
the exposure model alone) and from a 24-hour-ahead water-level forecast (tests
the whole chain), and compared pixel by pixel with the real mask.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from flatcast import render
from flatcast.hydro import load_hourly
from flatcast.imagery.masks import load_stack
from flatcast.imagery.water_index import DRY, WATER
from flatcast.log import console
from flatcast.models import exposure as E
from flatcast.models.waterlevel import ForecastInputs, model_dir, qcol
from flatcast.models.waterlevel import features as F
from flatcast.models.waterlevel.gbm import GBMForecaster
from flatcast.pairing import load_pairs
from flatcast.sources.coops import to_uv
from flatcast.sources.wind import openmeteo

plt = render.plt
LEADS_REPORTED = (6, 24, 48)
Z90 = 2.5631  # width of the 10-90% band in standard deviations


def holdout_bounds(cfg):
    y = cfg.holdout_year
    return pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC")


def out_dir(cfg):
    d = cfg.paths.outputs / "validation"
    d.mkdir(parents=True, exist_ok=True)
    return d


def archived_sources(cfg, start, end, index) -> dict[int, pd.DataFrame] | None:
    """Archived wind forecasts as lead buckets, or None if the archive doesn't cover the period."""
    lon, lat = cfg.wind.point
    runs = openmeteo.previous_runs(
        lon, lat, str((start - pd.Timedelta(days=3)).date()), str((end + pd.Timedelta(days=3)).date()), cfg.paths.raw, cfg.wind.openmeteo_model
    )
    out = {}
    for n, df in runs.items():
        u, v = to_uv(df["speed"], df["direction"])
        frame = pd.DataFrame({"u": u, "v": v, "p": df["pressure"].to_numpy()}, index=df.index).reindex(index)
        out[n] = frame
    covered = out[1]["u"].loc[start:end].notna().mean()
    return out if covered > 0.5 else None


# --------------------------------------------------------------------------------------------
# Water level
# --------------------------------------------------------------------------------------------


def validate_waterlevel(cfg) -> dict:
    start, end = holdout_bounds(cfg)
    hourly = F.add_rolling(load_hourly(cfg), int(cfg.exposure.setup_window_hours))
    model = GBMForecaster.load(model_dir(cfg) / "validation")
    issue = hourly.index[(hourly.index >= start) & (hourly.index < end - pd.Timedelta(hours=48)) & (hourly.index.hour % 6 == 0)]
    issue = issue[hourly["h"].reindex(issue).notna().to_numpy()]

    archived = archived_sources(cfg, start, end, hourly.index)
    methods = {}
    if archived is not None:
        methods["FlatCast, forecast winds"] = model.predict(ForecastInputs(hourly, archived), issue)
    else:
        console.log("[yellow]Validation: no archived wind forecasts for the hold-out year; scoring with reanalysis winds only")
    methods["FlatCast, perfect winds"] = model.predict(ForecastInputs(hourly, F.reanalysis_sources(hourly)), issue)

    any_pred = next(iter(methods.values()))
    valid_time = any_pred["valid_time"]
    actual = hourly["h"].reindex(valid_time).to_numpy()
    h0 = hourly["h"].reindex(any_pred.index.get_level_values("issue_time")).to_numpy()
    tide = hourly["tide"]
    tide_change = tide.reindex(valid_time).to_numpy() - tide.reindex(any_pred.index.get_level_values("issue_time")).to_numpy()
    leads = any_pred.index.get_level_values("lead").to_numpy()

    median = qcol(0.5)
    preds = {name: df[median].to_numpy() for name, df in methods.items()}
    preds["Persistence (no change)"] = h0
    preds["Persistence + tide"] = h0 + tide_change

    rows = []
    for name, p in preds.items():
        for lead in range(1, 49):
            sel = (leads == lead) & np.isfinite(actual) & np.isfinite(p)
            err = p[sel] - actual[sel]
            row = {"method": name, "lead_h": lead, "n": int(sel.sum()), "mae_cm": 100 * np.mean(np.abs(err)), "rmse_cm": 100 * np.sqrt(np.mean(err**2)), "bias_cm": 100 * np.mean(err)}
            if name in methods:
                df = methods[name]
                lo, hi = df[qcol(0.1)].to_numpy()[sel], df[qcol(0.9)].to_numpy()[sel]
                row["coverage_10_90"] = float(np.mean((actual[sel] >= lo) & (actual[sel] <= hi)))
            rows.append(row)
    table = pd.DataFrame(rows)
    d = out_dir(cfg)
    table.to_csv(d / "waterlevel_errors.csv", index=False)

    # Lead-time error curve
    fig, ax = plt.subplots(figsize=(8, 4.5))
    styles = {
        "FlatCast, forecast winds": dict(color="#c9792b", lw=2.6),
        "FlatCast, perfect winds": dict(color="#c9792b", lw=1.5, ls="--"),
        "Persistence (no change)": dict(color="#6b7b86", lw=1.6),
        "Persistence + tide": dict(color="#1f6f95", lw=1.6),
    }
    for name, g in table.groupby("method", sort=False):
        ax.plot(g["lead_h"], g["mae_cm"], label=name, **styles.get(name, {}))
    ax.set_xlabel("Forecast lead (hours)")
    ax.set_ylabel("Mean absolute error (cm)")
    ax.set_title(f"Bird Island water-level forecast error, held-out {cfg.holdout_year}")
    ax.set_xticks([0, 6, 12, 18, 24, 30, 36, 42, 48])
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(d / "waterlevel_error_by_lead.png")
    plt.close(fig)

    summary = {
        name: {
            str(L): {k: round(float(v), 2) for k, v in g[g["lead_h"] == L].iloc[0][["mae_cm", "rmse_cm", "bias_cm"]].items()}
            | ({"coverage_10_90": round(float(g[g["lead_h"] == L]["coverage_10_90"].iloc[0]), 3)} if "coverage_10_90" in g and g[g["lead_h"] == L]["coverage_10_90"].notna().any() else {})
            for L in LEADS_REPORTED
        }
        for name, g in table.groupby("method", sort=False)
    }
    calibration = _band_calibration(methods.get("FlatCast, forecast winds", any_pred), actual, leads)
    (model_dir(cfg) / "calibration.json").write_text(json.dumps({"band_scale_by_lead": calibration, "holdout_year": cfg.holdout_year}))
    summary["_meta"] = {
        "holdout_year": cfg.holdout_year,
        "issue_times": int(len(issue)),
        "archived_wind_forecasts": archived is not None,
        "band_scale_24h": round(calibration[23], 3),
    }
    (d / "waterlevel_summary.json").write_text(json.dumps(summary, indent=2))
    console.log(f"Validation: water-level errors written for {len(issue)} forecasts issued in {cfg.holdout_year}")
    return summary


def _band_calibration(pred: pd.DataFrame, actual: np.ndarray, leads: np.ndarray) -> list[float]:
    """Scale for the q10-q90 band at each lead so that it holds 80% of held-out outcomes."""
    q10, q50, q90 = (pred[qcol(q)].to_numpy() for q in (0.1, 0.5, 0.9))
    below = actual < q50
    r = np.where(below, (q50 - actual) / np.maximum(q50 - q10, 1e-4), (actual - q50) / np.maximum(q90 - q50, 1e-4))
    scales = []
    for lead in range(1, 49):
        sel = (leads == lead) & np.isfinite(r)
        scales.append(float(np.quantile(r[sel], 0.8)) if sel.sum() > 30 else 1.0)
    smooth = pd.Series(scales).rolling(5, center=True, min_periods=1).median()
    return [round(float(np.clip(s, 0.5, 3.0)), 3) for s in smooth]


def apply_calibration(pred: pd.DataFrame, cfg) -> pd.DataFrame:
    """Widen (or narrow) the forecast band using the hold-out calibration, if one exists."""
    path = model_dir(cfg) / "calibration.json"
    if not path.exists():
        return pred
    scale = np.array(json.loads(path.read_text())["band_scale_by_lead"])
    s = scale[np.clip(pred.index.get_level_values("lead").to_numpy() - 1, 0, len(scale) - 1)]
    out = pred.copy()
    mid = out[qcol(0.5)]
    out[qcol(0.1)] = mid - s * (mid - out[qcol(0.1)])
    out[qcol(0.9)] = mid + s * (out[qcol(0.9)] - mid)
    return out


# --------------------------------------------------------------------------------------------
# Exposure
# --------------------------------------------------------------------------------------------


def flats_zone(model: E.ExposureModel, levels: np.ndarray) -> np.ndarray:
    """Pixels that actually change state within the normal range of water levels."""
    lo, hi = np.nanpercentile(levels, [1, 99])
    return (model.status == E.FITTED) & (model.z > lo) & (model.z < hi)


def _scores(actual, p, zone, clim):
    valid = zone & ((actual == DRY) | (actual == WATER)) & np.isfinite(p)
    if valid.sum() < 50:
        return None
    y = (actual[valid] == DRY).astype(float)
    pp = p[valid]
    pred = pp >= 0.5
    tp = np.sum(pred & (y == 1))
    tn = np.sum(~pred & (y == 0))
    pos, neg = np.sum(y == 1), np.sum(y == 0)
    bal = 0.5 * ((tp / pos if pos else 1) + (tn / neg if neg else 1))
    return {
        "pixels": int(valid.sum()),
        "accuracy": float(np.mean(pred == (y == 1))),
        "balanced_accuracy": float(bal),
        "brier": float(np.mean((pp - y) ** 2)),
        "climatology_accuracy": float(np.mean((clim[valid] >= 0.5) == (y == 1))),
        "observed_dry_fraction": float(y.mean()),
    }


def validate_exposure(cfg, model: E.ExposureModel, pairs: pd.DataFrame) -> dict:
    start, end = holdout_bounds(cfg)
    times = pd.to_datetime(pairs["time"], utc=True)
    hold = pairs[(times >= start) & (times < end) & pairs["valid"]].copy()
    if hold.empty:
        console.log("[yellow]Validation: no clear images in the hold-out year")
        return {}
    train_pairs = pairs[(times < start) & pairs["valid"]]
    zone = flats_zone(model, train_pairs["h"].to_numpy())
    clim = model.n_dry / np.maximum(model.n_obs, 1)

    # 24-hour-ahead water-level forecasts for each held-out image
    hourly = F.add_rolling(load_hourly(cfg), int(cfg.exposure.setup_window_hours))
    wl = GBMForecaster.load(model_dir(cfg) / "validation")
    archived = archived_sources(cfg, start, end, hourly.index) or F.reanalysis_sources(hourly)
    issue = pd.DatetimeIndex(pd.to_datetime(hold["time"], utc=True)).floor("h") - pd.Timedelta(hours=24)
    ok = hourly["h"].reindex(issue).notna().to_numpy()
    fc = apply_calibration(wl.predict(ForecastInputs(hourly, archived), issue[ok].unique()), cfg) if ok.any() else None
    window = int(cfg.exposure.setup_window_hours)
    d1 = archived[1]
    fx, fy = E.wind_stress(d1["u"], d1["v"])
    f_tau = pd.DataFrame({"x": fx, "y": fy}, index=d1.index).rolling(window, min_periods=1).mean()

    masks = load_stack(cfg, list(hold.index))
    per_date = []
    preds = {}
    for n, (key, row) in enumerate(hold.iterrows()):
        actual = masks[n]
        p_obs = model.p_dry(row["h"], (row["tau_x"], row["tau_y"]))
        rec = {"key": key, "time": str(row["time"]), "h": float(row["h"]), "wind_ms": float(row["speed"])}
        s = _scores(actual, p_obs, zone, clim)
        if s is None:
            continue
        rec.update({f"obs_{k}": v for k, v in s.items()})
        p_fc = None
        if fc is not None and ok[n]:
            g = fc.loc[(issue[n], 24)]
            sigma = max((g[qcol(0.9)] - g[qcol(0.1)]) / Z90, 0.0)
            vt = issue[n] + pd.Timedelta(hours=24)
            tau = f_tau.reindex([vt]).to_numpy()[0]
            p_fc = model.p_dry(g[qcol(0.5)], np.nan_to_num(tau), sigma)
            sf = _scores(actual, p_fc, zone, clim)
            if sf:
                rec.update({f"fc_{k}": v for k, v in sf.items()})
                rec["fc_h"] = float(g[qcol(0.5)])
        per_date.append(rec)
        preds[key] = (actual, p_obs, p_fc)

    table = pd.DataFrame(per_date)
    d = out_dir(cfg)
    table.to_csv(d / "exposure_by_date.csv", index=False)

    def pooled(prefix):
        cols = [f"{prefix}_{m}" for m in ("accuracy", "balanced_accuracy", "brier", "climatology_accuracy")]
        if cols[0] not in table:
            return None
        t = table.dropna(subset=cols)
        w = t[f"{prefix}_pixels"]
        return {c.split("_", 1)[1]: round(float(np.average(t[c], weights=w)), 4) for c in cols} | {"images": int(len(t))}

    summary = {
        "holdout_year": cfg.holdout_year,
        "flats_zone_km2": round(float(zone.sum()) * (cfg.area.resolution_m / 1000) ** 2, 2),
        "from_observed_gauge": pooled("obs"),
        "from_24h_forecast": pooled("fc"),
    }
    (d / "exposure_summary.json").write_text(json.dumps(summary, indent=2))
    _side_by_side(cfg, hold, preds, zone)
    console.log(f"Validation: exposure scored on {len(table)} held-out images; flats zone {summary['flats_zone_km2']} km2")
    return summary


def _side_by_side(cfg, hold, preds, zone, n_events: int = 10):
    d = out_dir(cfg) / "low_water_events"
    d.mkdir(parents=True, exist_ok=True)
    lowest = hold[hold.index.isin(list(preds))].nsmallest(n_events, "h")
    for rank, (key, row) in enumerate(lowest.iterrows(), start=1):
        actual, p_obs, p_fc = preds[key]
        panels = [("Satellite: actual", render.mask_rgb(actual), None), ("Predicted from gauge", None, p_obs)]
        if p_fc is not None:
            panels.append(("Predicted 24 h ahead", None, p_fc))
        panels.append(("Agreement (gauge-based)", render.agreement_rgb(actual, p_obs, zone), None))
        fig, axes = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 4.6))
        for ax, (title, rgb, p) in zip(axes, panels):
            if rgb is not None:
                ax.imshow(rgb, interpolation="nearest")
            else:
                ax.imshow(p, cmap=render.EXPOSURE_CMAP, vmin=0, vmax=1, interpolation="nearest")
            ax.set_title(title, fontsize=11)
            render.axes_off(ax)
        t = pd.Timestamp(row["time"]).tz_convert("America/Chicago")
        fig.suptitle(
            f"#{rank} lowest water of {cfg.holdout_year}: {t:%d %b %Y %H:%M} CT   gauge {row['h']:+.2f} m NAVD88   wind {row['speed']:.1f} m/s",
            fontsize=12,
            fontweight="bold",
        )
        fig.text(
            0.5,
            0.01,
            "Sand = dry / exposed   Teal = water   Grey-green = ambiguous   |   Agreement: orange = missed exposure, purple = false exposure",
            ha="center",
            fontsize=8.5,
            color="#555",
        )
        fig.tight_layout(rect=(0, 0.03, 1, 0.95))
        fig.savefig(d / f"{rank:02d}_{key}.png")
        plt.close(fig)
