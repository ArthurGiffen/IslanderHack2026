"""NOAA CO-OPS data API client (https://api.tidesandcurrents.noaa.gov/api/prod/).

Limits used here: hourly products are requested a year at a time, 6-minute
products a month at a time, matching the API's documented maximum ranges.
Completed periods are cached forever; anything within the last 60 days is
re-downloaded after a few hours because preliminary data still changes.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from flatcast.cache import fetch_json

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").floor("h")


def _fetch(cache_dir: Path, station: str, product: str, begin: dt.date, end: dt.date, **extra) -> list[dict]:
    params = {
        "product": product,
        "station": station,
        "begin_date": begin.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "time_zone": "gmt",
        "units": "metric",
        "format": "json",
        "application": "FlatCast",
        **extra,
    }
    recent = (dt.date.today() - end).days < 60
    path = cache_dir / station / product / f"{params['begin_date']}_{params['end_date']}.json"
    data = fetch_json(API, params, path, max_age_hours=3 if recent else None, pause=0.2)
    if "error" in data:
        return []
    return data.get("data") or data.get("predictions") or []


def _years(start: pd.Timestamp, end: pd.Timestamp):
    for y in range(start.year, end.year + 1):
        b = max(dt.date(y, 1, 1), start.date())
        e = min(dt.date(y, 12, 31), end.date())
        if b <= e:
            yield b, e


def _months(start: pd.Timestamp, end: pd.Timestamp):
    for m in pd.period_range(start.to_period("M"), end.to_period("M"), freq="M"):
        b = max(m.start_time.date(), start.date())
        e = min(m.end_time.date(), end.date())
        yield b, e


def _series(rows: list[dict], field: str = "v") -> pd.Series:
    if not rows:
        return pd.Series(dtype="float64")
    t = pd.to_datetime([r["t"] for r in rows], utc=True)
    v = pd.to_numeric(pd.Series([r.get(field) for r in rows]), errors="coerce").to_numpy()
    return pd.Series(v, index=t).groupby(level=0).first()


def water_level_hourly(
    station: str, start: str | pd.Timestamp, cache_dir: Path, datum: str = "NAVD", end: pd.Timestamp | None = None
) -> pd.Series:
    """Hourly water level (m) on the hour, UTC.

    Uses verified hourly heights where they exist and fills any month that is
    missing or incomplete (including the most recent, not-yet-verified months)
    from the 6-minute record.
    """
    start = utc(start)
    end = end or _now()
    parts = [_series(_fetch(cache_dir, station, "hourly_height", b, e, datum=datum)) for b, e in _years(start, end)]
    s = pd.concat([p for p in parts if len(p)]) if any(len(p) for p in parts) else pd.Series(dtype="float64")
    idx = pd.date_range(start, end, freq="h")
    s = s[~s.index.duplicated()].reindex(idx)

    coverage = s.notna().groupby(s.index.tz_localize(None).to_period("M")).mean()
    for period, frac in coverage.items():
        if frac >= 0.9:
            continue
        b = max(period.start_time.tz_localize("UTC"), start)
        e = min(period.end_time.tz_localize("UTC"), end)
        six = _series(_fetch(cache_dir, station, "water_level", b.date(), e.date(), datum=datum))
        if len(six):
            on_hour = six[six.index.minute == 0]
            s = s.fillna(on_hour.reindex(s.index))
    s.name = station
    return s


def wind_hourly(station: str, start: str | pd.Timestamp, cache_dir: Path, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Hourly observed wind: speed (m/s), direction the wind blows FROM (deg), gust (m/s)."""
    start = utc(start)
    end = end or _now()
    frames = []
    for b, e in _years(start, end):
        rows = _fetch(cache_dir, station, "wind", b, e, interval="h")
        if rows:
            frames.append(pd.DataFrame({"speed": _series(rows, "s"), "direction": _series(rows, "d"), "gust": _series(rows, "g")}))
    idx = pd.date_range(start, end, freq="h")
    if not frames:
        return pd.DataFrame(index=idx, columns=["speed", "direction", "gust"], dtype="float64")
    df = pd.concat(frames)
    return df[~df.index.duplicated()].reindex(idx)


def recent_water_level(station: str, cache_dir: Path, days: int = 3, datum: str = "NAVD") -> pd.Series:
    """Latest 6-minute water levels, always fresh (for the live forecast)."""
    end = _now() + pd.Timedelta(hours=1)
    begin = end - pd.Timedelta(days=days)
    params = dict(datum=datum)
    rows = _fetch_fresh(cache_dir, station, "water_level", begin, end, **params)
    return _series(rows)


def recent_wind(station: str, cache_dir: Path, days: int = 3) -> pd.DataFrame:
    end = _now() + pd.Timedelta(hours=1)
    begin = end - pd.Timedelta(days=days)
    rows = _fetch_fresh(cache_dir, station, "wind", begin, end)
    return pd.DataFrame({"speed": _series(rows, "s"), "direction": _series(rows, "d")})


def _fetch_fresh(cache_dir: Path, station: str, product: str, begin: pd.Timestamp, end: pd.Timestamp, **extra) -> list[dict]:
    params = {
        "product": product,
        "station": station,
        "begin_date": begin.strftime("%Y%m%d %H:%M"),
        "end_date": end.strftime("%Y%m%d %H:%M"),
        "time_zone": "gmt",
        "units": "metric",
        "format": "json",
        "application": "FlatCast",
        **extra,
    }
    path = cache_dir / station / product / "latest.json"
    data = fetch_json(API, params, path, max_age_hours=0.25)
    if "error" in data:
        return []
    return data.get("data") or []


def tide_predictions_hourly(
    station: str, start: str | pd.Timestamp, end: str | pd.Timestamp, cache_dir: Path, datum: str = "NAVD"
) -> pd.Series:
    """Astronomical tide predictions (m), hourly. Works for future dates too."""
    start = utc(start)
    end = utc(end)
    parts = []
    for b, e in _years(start, end):
        rows = _fetch(cache_dir, station, "predictions", b, e, datum=datum, interval="h")
        parts.append(_series(rows))
    s = pd.concat([p for p in parts if len(p)]) if any(len(p) for p in parts) else pd.Series(dtype="float64")
    s = s[~s.index.duplicated()]
    return s.reindex(pd.date_range(start.floor("h"), end.floor("h"), freq="h")).interpolate(limit=3)


def to_uv(speed: np.ndarray | pd.Series, direction_from: np.ndarray | pd.Series):
    """Wind vector (m/s) the air moves TOWARD: u east, v north."""
    rad = np.deg2rad(np.asarray(direction_from, dtype="float64"))
    s = np.asarray(speed, dtype="float64")
    return -s * np.sin(rad), -s * np.cos(rad)


def utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def station_lonlat(station: str, cache_dir: Path) -> tuple[float, float]:
    """Station coordinates from the CO-OPS metadata API."""
    url = f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{station}.json"
    data = fetch_json(url, None, cache_dir / station / "metadata.json", max_age_hours=24 * 90)
    s = data["stations"][0]
    return float(s["lng"]), float(s["lat"])
