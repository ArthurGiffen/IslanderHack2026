"""Wind forecast providers behind one interface (see base.WindProvider)."""

from __future__ import annotations

from flatcast.sources.wind.base import WindProvider
from flatcast.sources.wind.nws import NWSProvider
from flatcast.sources.wind.openmeteo import OpenMeteoProvider


def get_provider(cfg) -> WindProvider:
    cache = cfg.paths.raw
    name = cfg.wind.provider
    if name == "openmeteo":
        return OpenMeteoProvider(model=cfg.wind.openmeteo_model, cache_dir=cache)
    if name == "nws":
        return NWSProvider(contact=cfg.project.contact, cache_dir=cache)
    raise ValueError(f"unknown wind provider {name!r}; use 'openmeteo' or 'nws'")


__all__ = ["WindProvider", "get_provider", "OpenMeteoProvider", "NWSProvider"]
