"""US National Weather Service API (api.weather.gov).

Terms: free, no key; NWS asks every client to send a User-Agent that
identifies the application and a contact. There is no archive of past
forecasts, so NWS can be used for live forecasts only.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from flatcast.cache import USER_AGENT, fetch_json

POINTS_API = "https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
ATTRIBUTION = "Forecast: US National Weather Service"

CARDINALS = {
    name: i * 22.5
    for i, name in enumerate("N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())
}
MPH = 0.44704


def parse_speed(text: str) -> float:
    """'10 mph' -> 4.47; '10 to 15 mph' -> mean of the range, in m/s."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text or "")]
    if not nums:
        return np.nan
    value = sum(nums) / len(nums)
    return value * (MPH if "mph" in text else 1 / 3.6 if "km" in text else 1.0)


class NWSProvider:
    name = "nws"

    def __init__(self, contact: str = "", cache_dir: Path | None = None):
        self.headers = {"User-Agent": f"{USER_AGENT} ({contact})" if contact else USER_AGENT, "Accept": "application/geo+json"}
        self.cache_dir = cache_dir

    def forecast(self, lon: float, lat: float, hours: int = 48) -> pd.DataFrame:
        cache = self.cache_dir / "nws" if self.cache_dir else None
        point = fetch_json(
            POINTS_API.format(lat=lat, lon=lon), None, cache / "point.json" if cache else None, max_age_hours=24 * 30, headers=self.headers
        )
        url = point["properties"]["forecastHourly"]
        data = fetch_json(url, None, cache / "hourly_latest.json" if cache else None, max_age_hours=0.5, headers=self.headers)
        periods = data["properties"]["periods"]
        idx = pd.to_datetime([p["startTime"] for p in periods], utc=True)
        df = pd.DataFrame(
            {
                "speed": [parse_speed(p.get("windSpeed", "")) for p in periods],
                "direction": [CARDINALS.get(p.get("windDirection", ""), np.nan) for p in periods],
                "pressure": np.nan,
            },
            index=idx,
        )
        return df.iloc[: hours + 6]
