"""Features for the gauge water-level forecast.

The same code builds features for training, for the held-out validation and
for the live forecast. What changes is where the *future* wind comes from:

  training     reanalysis winds (what a perfect forecast would have said)
  validation   archived forecasts, by lead time (what was really forecast)
  live         the current forecast from the configured provider

Future wind is passed as {bucket: frame}: bucket 1 serves leads 1-24 h,
bucket 2 serves leads 25-48 h. Each frame has columns u, v, p on the same
hourly index as the observations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flatcast.models.exposure import wind_stress

LAG_HOURS = (3, 6, 12, 24)
BUCKET_HOURS = 24


def bucket_of(lead: int) -> int:
    return max(1, int(np.ceil(lead / BUCKET_HOURS)))


def prepare_sources(sources: dict[int, pd.DataFrame], index: pd.DatetimeIndex) -> dict[int, dict[str, np.ndarray]]:
    """Reindex each future-wind frame and precompute cumulative sums for window means."""
    out = {}
    for b, df in sources.items():
        df = df.reindex(index)
        tx, ty = wind_stress(df["u"], df["v"])
        cols = {"u": df["u"].to_numpy(float), "v": df["v"].to_numpy(float), "tx": tx, "ty": ty, "p": df["p"].to_numpy(float)}
        prepared = {}
        for name, arr in cols.items():
            prepared[name] = arr
            finite = np.isfinite(arr)
            prepared[f"cs_{name}"] = np.concatenate([[0.0], np.cumsum(np.where(finite, arr, 0.0))])
            prepared[f"cn_{name}"] = np.concatenate([[0], np.cumsum(finite)])
        out[b] = prepared
    return out


def _window_mean(src: dict[int, dict], var: str, i: np.ndarray, lo: int, hi: int) -> np.ndarray:
    """Mean of var over leads lo+1..hi after issue index i, each lead from its own bucket."""
    total = np.zeros(len(i))
    count = np.zeros(len(i))
    n = len(next(iter(src.values()))[var])
    for b in sorted(src):
        a = max(lo, (b - 1) * BUCKET_HOURS)
        z = min(hi, b * BUCKET_HOURS) if b < max(src) else hi
        if z <= a:
            continue
        s = np.clip(i + a + 1, 0, n)  # positions a+1..z inclusive -> cumsum[s : e]
        e = np.clip(i + z + 1, 0, n)
        total += src[b][f"cs_{var}"][e] - src[b][f"cs_{var}"][s]
        count += src[b][f"cn_{var}"][e] - src[b][f"cn_{var}"][s]
    expected = hi - lo
    out = total / np.maximum(count, 1)
    out[count < 0.75 * expected] = np.nan
    return out


def _at(arr: np.ndarray, pos: np.ndarray) -> np.ndarray:
    ok = (pos >= 0) & (pos < len(arr))
    out = np.full(len(pos), np.nan)
    out[ok] = arr[pos[ok]]
    return out


def build(hourly: pd.DataFrame, src: dict[int, dict], i: np.ndarray, horizon: int) -> pd.DataFrame:
    """Feature matrix for issue positions i (into hourly.index) and one horizon in hours."""
    h = hourly["h"].to_numpy(float)
    f = {}
    f["h"] = _at(h, i)
    for lag in LAG_HOURS:
        f[f"dh{lag}"] = f["h"] - _at(h, i - lag)
    f["h_mean24"] = _at(hourly["h_mean24"].to_numpy(float), i)
    f["gulf_minus_h"] = _at(hourly["h_gulf"].to_numpy(float), i) - f["h"]
    f["south_minus_h"] = _at(hourly["h_south"].to_numpy(float), i) - f["h"]
    for col in ("u_obs", "v_obs", "u24", "v24", "tau_x", "tau_y"):
        f[col] = _at(hourly[col].to_numpy(float), i)

    tide = hourly["tide"].to_numpy(float)
    f["tide_change"] = _at(tide, i + horizon) - _at(tide, i)
    f["tide_trend"] = _at(tide, i) - _at(tide, i - 3)

    for var in ("u", "v", "tx", "ty"):
        f[f"f_{var}_all"] = _window_mean(src, var, i, 0, horizon)
        f[f"f_{var}_last12"] = _window_mean(src, var, i, max(0, horizon - 12), horizon)
    end_bucket = src[bucket_of(horizon)] if bucket_of(horizon) in src else src[max(src)]
    first_bucket = src[min(src)]
    f["f_dp"] = _at(end_bucket["p"], i + horizon) - _at(first_bucket["p"], i)

    t = hourly.index[np.clip(i + horizon, 0, len(hourly) - 1)]
    doy = 2 * np.pi * t.dayofyear.to_numpy() / 365.25
    f["season_sin"], f["season_cos"] = np.sin(doy), np.cos(doy)
    return pd.DataFrame(f)


def target(hourly: pd.DataFrame, i: np.ndarray, horizon: int) -> np.ndarray:
    h = hourly["h"].to_numpy(float)
    return _at(h, i + horizon) - _at(h, i)


def add_rolling(hourly: pd.DataFrame, setup_window: int) -> pd.DataFrame:
    from flatcast.pairing import add_wind_features

    out = add_wind_features(hourly, setup_window)
    out["h_mean24"] = out["h"].rolling(24, min_periods=12).mean()
    return out


def reanalysis_sources(hourly: pd.DataFrame) -> dict[int, pd.DataFrame]:
    re = pd.DataFrame({"u": hourly["u_re"], "v": hourly["v_re"], "p": hourly["p_re"]}, index=hourly.index)
    return {1: re, 2: re}
