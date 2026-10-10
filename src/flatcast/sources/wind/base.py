"""The wind-forecast interface. Any provider that returns this frame can be swapped in."""

from __future__ import annotations

from typing import Protocol

import pandas as pd

COLUMNS = ["speed", "direction", "pressure"]


class WindProvider(Protocol):
    """A source of hourly wind forecasts at a point.

    forecast() returns a DataFrame indexed by UTC hour with columns:
      speed      m/s at 10 m
      direction  degrees the wind blows FROM
      pressure   mean sea-level pressure, hPa (NaN if the provider has none)
    covering at least the next `hours` hours.
    """

    name: str

    def forecast(self, lon: float, lat: float, hours: int = 48) -> pd.DataFrame: ...
