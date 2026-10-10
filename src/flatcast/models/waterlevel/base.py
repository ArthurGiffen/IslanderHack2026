"""The water-level forecaster interface.

FlatCast's exposure maps only need one thing from a water-level model: hourly
quantiles of the primary gauge level for the next 48 hours. Anything that can
produce that, including the Conrad Blucher Institute's operational forecasts,
can replace the built-in gradient-boosting baseline (see cbi.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


@dataclass
class ForecastInputs:
    """Everything a forecaster may use.

    hourly:  hourly observations (h, h_gulf, h_south, tide, u_obs, v_obs and the
             rolling features from features.add_rolling), extended with empty rows
             through the end of the forecast window
    sources: future wind by lead bucket: {1: leads 1-24 h, 2: leads 25-48 h},
             each a frame with columns u, v (m/s toward east/north) and p (hPa)
    """

    hourly: pd.DataFrame
    sources: dict[int, pd.DataFrame]


class WaterLevelForecaster(Protocol):
    name: str
    quantiles: list[float]

    def predict(self, inputs: ForecastInputs, issue_times: pd.DatetimeIndex, hours: int = 48) -> pd.DataFrame:
        """Return a frame indexed by (issue_time, lead) with columns q10, q50, q90 (m, gauge datum)."""
        ...


def qcol(q: float) -> str:
    return f"q{int(round(q * 100)):02d}"
