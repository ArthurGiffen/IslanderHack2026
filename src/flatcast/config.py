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
    root: Path  # project folder: where the base config.yaml lives

    @property
    def area_id(self) -> str:
        return self.area.get("id", "main")

    @property
    def paths(self) -> Paths:
        root = self.root

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


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None) -> Config:
    """Load a config file. An area file can start with `extends: <base.yaml>` and
    override only what differs; relative paths always resolve from the project folder."""
    p = (Path(path) if path else find_config()).resolve()
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    root = p.parent
    if "extends" in data:
        base = load_config(p.parent / data.pop("extends"))
        root = base.root
        base_data = {k: v for k, v in base.items() if k != "areas"}
        data = _merge(base_data, data)
    cfg = Config(data)
    cfg.path = p
    cfg.root = root
    return cfg


def area_configs(cfg: Config) -> list[Config]:
    """Every area listed under `areas:` in the base config (just this one if none are listed)."""
    files = cfg.get("areas") or []
    if not files:
        return [cfg]
    return [load_config(cfg.root / f) for f in files]
