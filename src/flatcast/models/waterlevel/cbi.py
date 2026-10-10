"""Adapter slot for the Conrad Blucher Institute (Texas A&M University-Corpus Christi) forecasts.

CBI has run neural-network water-level forecasts for the Texas coast for two
decades, built on its TCOON gauge network and developed with the Corpus
Christi NWS office. Those forecasts are the natural replacement for FlatCast's
gradient-boosting baseline.

To plug them in:
  1. Implement fetch() to download CBI's latest forecast for the primary
     station (or a nearby one) as hourly levels.
  2. Convert to the station datum FlatCast uses (NAVD88, metres).
  3. If CBI publishes only a central forecast, derive q10/q90 from its
     historical error at each lead (validate.py computes those errors).
  4. Set `waterlevel.model: cbi` in config.yaml.

Nothing else in FlatCast needs to change: the forecast job only calls
predict() and reads q10 / q50 / q90.
"""

from __future__ import annotations

import pandas as pd

from flatcast.models.waterlevel.base import ForecastInputs


class CBIForecaster:
    name = "cbi"
    quantiles = [0.1, 0.5, 0.9]

    def __init__(self, station: str):
        self.station = station

    def fetch(self) -> pd.DataFrame:
        raise NotImplementedError(
            "The CBI adapter is a placeholder. See flatcast/models/waterlevel/cbi.py for the steps to connect it."
        )

    def predict(self, inputs: ForecastInputs, issue_times, hours: int = 48) -> pd.DataFrame:
        return self.fetch()
