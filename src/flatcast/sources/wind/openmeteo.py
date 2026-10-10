"""Open-Meteo: live forecasts, ERA5-based history and archived past forecasts.

Terms (checked 2026-10): free for non-commercial use without a key, up to
600 calls/min, 5,000/hour, 10,000/day; data licensed CC BY 4.0. FlatCast makes
a few dozen calls for a full rebuild and 1 per forecast run.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from flatcast.cache import fetch_json

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_API = "https://previous-runs-api.open-meteo.com/v1/forecast"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# Archived forecasts ("previous runs") exist from about January 2024.
PREVIOUS_RUNS_START = "2024-01-15"


def _frame(hourly: dict, suffix: str = "") -> pd.DataFrame:
    idx = pd.to_datetime(hourly["time"], utc=True)
    return pd.DataFrame(
        {
            "speed": hourly.get(f"wind_speed_10m{suffix}"),
            "direction": hourly.get(f"wind_direction_10m{suffix}"),
            "pressure": hourly.get(f"pressure_msl{suffix}"),
        },
        index=idx,
    ).astype("float64")


class OpenMeteoProvider:
    name = "openmeteo"

    def __init__(self, model: str = "gfs_seamless", cache_dir: Path | None = None):
        self.model = model
        self.cache_dir = cache_dir

    def forecast(self, lon: float, lat: float, hours: int = 48) -> pd.DataFrame:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wind_speed_10m,wind_direction_10m,pressure_msl",
            "wind_speed_unit": "ms",
            "timezone": "GMT",
            "past_days": 1,
            "forecast_days": max(3, hours // 24 + 2),
            "models": self.model,
        }
        path = self.cache_dir / "openmeteo" / "forecast_latest.json" if self.cache_dir else None
        data = fetch_json(FORECAST_API, params, path, max_age_hours=0.5)
        return _frame(data["hourly"])


def archive(lon: float, lat: float, start: str, end: str, cache_dir: Path) -> pd.DataFrame:
    """Hourly reanalysis-based wind history (ERA5 / ECMWF IFS), one request per year."""
    frames = []
    for year in range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1):
        b = max(pd.Timestamp(start), pd.Timestamp(f"{year}-01-01"))
        e = min(pd.Timestamp(end), pd.Timestamp(f"{year}-12-31"))
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": b.strftime("%Y-%m-%d"),
            "end_date": e.strftime("%Y-%m-%d"),
            "hourly": "wind_speed_10m,wind_direction_10m,pressure_msl",
            "wind_speed_unit": "ms",
            "timezone": "GMT",
        }
        recent = (pd.Timestamp.now() - e).days < 30
        path = cache_dir / "openmeteo" / "archive" / f"{params['start_date']}_{params['end_date']}.json"
        data = fetch_json(ARCHIVE_API, params, path, max_age_hours=12 if recent else None, pause=0.5)
        frames.append(_frame(data["hourly"]))
    df = pd.concat(frames)
    return df[~df.index.duplicated()]


def previous_runs(lon: float, lat: float, start: str, end: str, cache_dir: Path, model: str, days=(1, 2)) -> dict[int, pd.DataFrame]:
    """Archived forecasts: for each lag N, the value forecast N days before the valid time.

    Returns {N: frame}. Used to score the water-level model with the wind
    forecasts that were actually available, instead of the winds that happened.
    """
    start = max(pd.Timestamp(start), pd.Timestamp(PREVIOUS_RUNS_START))
    variables = [f"{v}_previous_day{n}" for n in days for v in ("wind_speed_10m", "wind_direction_10m", "pressure_msl")]
    out: dict[int, list[pd.DataFrame]] = {n: [] for n in days}
    for period in pd.period_range(start.to_period("Q"), pd.Timestamp(end).to_period("Q"), freq="Q"):
        b = max(start, period.start_time)
        e = min(pd.Timestamp(end), period.end_time.normalize())
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": b.strftime("%Y-%m-%d"),
            "end_date": e.strftime("%Y-%m-%d"),
            "hourly": ",".join(variables),
            "wind_speed_unit": "ms",
            "timezone": "GMT",
            "models": model,
        }
        recent = (pd.Timestamp.now() - e).days < 10
        path = cache_dir / "openmeteo" / "previous_runs" / model / f"{params['start_date']}_{params['end_date']}.json"
        data = fetch_json(PREVIOUS_RUNS_API, params, path, max_age_hours=12 if recent else None, pause=0.5)
        for n in days:
            out[n].append(_frame(data["hourly"], f"_previous_day{n}"))
    result = {}
    for n, frames in out.items():
        df = pd.concat(frames)
        result[n] = df[~df.index.duplicated()]
    return result
