"""Pair every clear image with the gauge level and wind at its overpass time."""

from __future__ import annotations

import numpy as np
import pandas as pd

from flatcast.hydro import load_hourly
from flatcast.imagery.masks import load_catalog
from flatcast.log import console
from flatcast.models.exposure import wind_stress


def pairs_path(cfg):
    return cfg.paths.tables / "pairs.parquet"


def add_wind_features(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """Rolling wind features on an hourly frame with u_obs / v_obs columns."""
    out = df.copy()
    tx, ty = wind_stress(out["u_obs"], out["v_obs"])
    out["tau_x"] = pd.Series(tx, index=out.index).rolling(window, min_periods=max(1, window // 2)).mean()
    out["tau_y"] = pd.Series(ty, index=out.index).rolling(window, min_periods=max(1, window // 2)).mean()
    out["u24"] = out["u_obs"].rolling(24, min_periods=12).mean()
    out["v24"] = out["v_obs"].rolling(24, min_periods=12).mean()
    return out


def _interp(series: pd.Series, times: pd.DatetimeIndex) -> np.ndarray:
    """Linear interpolation between the hours either side; NaN if either is missing."""
    t0 = times.floor("h")
    f = ((times - t0) / pd.Timedelta(hours=1)).to_numpy()
    a = series.reindex(t0).to_numpy()
    b = series.reindex(t0 + pd.Timedelta(hours=1)).to_numpy()
    return a * (1 - f) + b * f


def build_pairs(cfg) -> pd.DataFrame:
    hourly = add_wind_features(load_hourly(cfg), int(cfg.exposure.setup_window_hours))
    cat = load_catalog(cfg)
    cat = cat[cat["used"].astype(bool)].copy()
    times = pd.DatetimeIndex(pd.to_datetime(cat["time"], utc=True))
    out = cat.copy()
    for col in ("h", "h_gulf", "h_south"):
        out[col] = _interp(hourly[col], times)
    hour = times.floor("h")
    for col in ("u_obs", "v_obs", "tau_x", "tau_y", "u24", "v24"):
        out[col] = hourly[col].reindex(hour).to_numpy()
    out["speed"] = np.hypot(out["u_obs"], out["v_obs"])
    out["valid"] = out[["h", "tau_x", "tau_y"]].notna().all(axis=1)
    out.to_parquet(pairs_path(cfg))
    console.log(f"Pairing: {int(out['valid'].sum())} of {len(out)} clear images have a gauge reading at overpass time")
    return out


def load_pairs(cfg) -> pd.DataFrame:
    return pd.read_parquet(pairs_path(cfg))
