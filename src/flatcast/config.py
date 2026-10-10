"""Configuration loading and project paths."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class Section(dict):
    """A dict that also allows attribute access: cfg.area.bbox."""

    def __getattr__(self, name: str) -> Any:
        try:
            value = self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
        return Section(value) if isinstance(value, dict) else value


@dataclass
class Paths:
    root: Path
    data: Path
    outputs: Path
    site: Path
    flats: Path

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def mndwi(self) -> Path:
        return self.data / "mndwi"

    @property
    def masks(self) -> Path:
        return self.data / "masks"

    @property
    def models(self) -> Path:
        return self.data / "models"

    @property
    def tables(self) -> Path:
        return self.data / "tables"

    def ensure(self) -> "Paths":
        for p in (self.data, self.raw, self.mndwi, self.masks, self.models, self.tables, self.outputs):
            p.mkdir(parents=True, exist_ok=True)
        return self


class Config(Section):
    path: Path

    @property
    def paths(self) -> Paths:
        root = self.path.parent

        def resolve(p: str) -> Path:
            q = Path(p).expanduser()
            return q if q.is_absolute() else root / q

        proj = self.project
        return Paths(
            root=root,
            data=resolve(proj.data_dir),
            outputs=resolve(proj.output_dir),
            site=resolve(proj.site_dir),
            flats=resolve(proj.flats_file),
        )

    @property
    def holdout_year(self) -> int:
        value = self.exposure.holdout_year
        if value in (None, "auto"):
            return dt.date.today().year - 1
        return int(value)


def find_config(start: Path | None = None) -> Path:
    """Look for config.yaml in the given directory and its parents."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / "config.yaml").exists():
            return d / "config.yaml"
    raise FileNotFoundError("config.yaml not found; run flatcast from the project folder or pass --config")


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else find_config()
    with open(p, encoding="utf-8") as f:
        cfg = Config(yaml.safe_load(f))
    cfg.path = p.resolve()
    return cfg
