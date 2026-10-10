"""Baseline water-level forecaster: LightGBM quantile models, one per horizon and quantile.

Each model predicts the *change* in gauge level from the issue time to the
horizon. Leads between the trained horizons are interpolated. 24 small models
(8 horizons x 3 quantiles), about 11 MB on disk in total.
"""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from flatcast.models.waterlevel import features as F
from flatcast.models.waterlevel.base import ForecastInputs, qcol

PARAMS = dict(
    objective="quantile",
    learning_rate=0.05,
    n_estimators=300,
    num_leaves=15,
    min_child_samples=50,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.9,
    verbose=-1,
)


class GBMForecaster:
    name = "gbm"

    def __init__(self, horizons: list[int], quantiles: list[float], models: dict | None = None, meta: dict | None = None):
        self.horizons = list(horizons)
        self.quantiles = list(quantiles)
        self.models: dict[tuple[int, float], lgb.Booster] = models or {}
        self.meta = meta or {}

    # ---- training -------------------------------------------------------------------------
    @classmethod
    def train(cls, hourly: pd.DataFrame, horizons, quantiles, *, end: pd.Timestamp, stride: int = 3, progress=None) -> "GBMForecaster":
        """Train on issue times before `end`, using reanalysis winds as the future wind."""
        src = F.prepare_sources(F.reanalysis_sources(hourly), hourly.index)
        last = hourly.index.searchsorted(end) - 1
        model = cls(horizons, quantiles)
        n_rows = {}
        for H in horizons:
            i = np.arange(24, last - H, stride)
            X = F.build(hourly, src, i, H)
            y = F.target(hourly, i, H)
            ok = np.isfinite(y) & np.isfinite(X["h"].to_numpy())
            X, y = X[ok], y[ok]
            n_rows[H] = int(len(y))
            for q in quantiles:
                reg = lgb.LGBMRegressor(alpha=q, **PARAMS)
                reg.fit(X, y)
                model.models[(H, q)] = reg.booster_
                if progress:
                    progress(H, q)
        model.meta = {"trained_until": str(end), "rows": n_rows, "features": list(X.columns)}
        return model

    # ---- prediction -----------------------------------------------------------------------
    def predict(self, inputs: ForecastInputs, issue_times, hours: int = 48) -> pd.DataFrame:
        hourly = inputs.hourly
        src = F.prepare_sources(inputs.sources, hourly.index)
        i = hourly.index.get_indexer(pd.DatetimeIndex(issue_times))
        if (i < 0).any():
            raise ValueError("issue time outside the hourly table")
        h0 = hourly["h"].to_numpy(float)[i]
        # change at each trained horizon: (issue, horizon, quantile)
        change = np.zeros((len(i), len(self.horizons) + 1, len(self.quantiles)))
        for hi, H in enumerate(self.horizons, start=1):
            X = F.build(hourly, src, i, H)
            for qi, q in enumerate(self.quantiles):
                change[:, hi, qi] = self.models[(H, q)].predict(X)
        change.sort(axis=2)  # quantiles must not cross
        leads = np.arange(1, hours + 1)
        knots = np.array([0, *self.horizons], dtype=float)
        rows = []
        for qi, q in enumerate(self.quantiles):
            interp = np.stack([np.interp(leads, knots, change[n, :, qi]) for n in range(len(i))])
            rows.append(h0[:, None] + interp)
        issue_idx = pd.DatetimeIndex(issue_times)
        index = pd.MultiIndex.from_product([issue_idx, leads], names=["issue_time", "lead"])
        out = pd.DataFrame({qcol(q): rows[qi].reshape(-1) for qi, q in enumerate(self.quantiles)}, index=index)
        out["valid_time"] = index.get_level_values(0) + pd.to_timedelta(index.get_level_values(1), unit="h")
        return out

    # ---- persistence ----------------------------------------------------------------------
    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        for (H, q), booster in self.models.items():
            booster.save_model(str(folder / f"h{H:02d}_{qcol(q)}.txt"))
        (folder / "meta.json").write_text(json.dumps({"horizons": self.horizons, "quantiles": self.quantiles, **self.meta}, indent=2))
        return folder

    @classmethod
    def load(cls, folder: Path) -> "GBMForecaster":
        meta = json.loads((folder / "meta.json").read_text())
        models = {
            (H, q): lgb.Booster(model_file=str(folder / f"h{H:02d}_{qcol(q)}.txt"))
            for H in meta["horizons"]
            for q in meta["quantiles"]
        }
        return cls(meta["horizons"], meta["quantiles"], models, meta)
