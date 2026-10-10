"""Gauge water-level forecasters behind one interface (see base.WaterLevelForecaster)."""

from __future__ import annotations

from flatcast.models.waterlevel.base import ForecastInputs, WaterLevelForecaster, qcol


def model_dir(cfg):
    return cfg.paths.models / "waterlevel"


def load_forecaster(cfg) -> WaterLevelForecaster:
    name = cfg.waterlevel.model
    if name == "gbm":
        from flatcast.models.waterlevel.gbm import GBMForecaster

        return GBMForecaster.load(model_dir(cfg) / "production")
    if name == "cbi":
        from flatcast.models.waterlevel.cbi import CBIForecaster

        return CBIForecaster(cfg.stations.primary)
    raise ValueError(f"unknown water-level model {name!r}; use 'gbm' or 'cbi'")


__all__ = ["ForecastInputs", "WaterLevelForecaster", "load_forecaster", "model_dir", "qcol"]
