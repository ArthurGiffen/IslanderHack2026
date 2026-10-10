"""Download HLS scenes, mask clouds, compute MNDWI and write one wet/dry mask per date.

Two layers of caching make reruns cheap:
  data/mndwi/<scene>.tif   MNDWI on the analysis grid (clouds already NaN)
  data/masks/<scene>.tif   water / ambiguous / dry classes from the current thresholds
Changing the thresholds in config.yaml only re-classifies; nothing is re-downloaded.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import rasterio

from flatcast.grid import Grid
from flatcast.imagery import cloudmask, water_index
from flatcast.log import console
from flatcast.sources import hls


def catalog_path(cfg):
    return cfg.paths.tables / "scenes.parquet"


def load_catalog(cfg) -> pd.DataFrame:
    p = catalog_path(cfg)
    if not p.exists():
        return pd.DataFrame(columns=["key", "sensor", "time", "granules", "clear_frac", "used", "otsu"]).set_index("key")
    return pd.read_parquet(p)


def _save_catalog(cfg, records: dict) -> pd.DataFrame:
    df = pd.DataFrame(list(records.values())).set_index("key").sort_values("time")
    df.to_parquet(catalog_path(cfg))
    return df


def download_scenes(cfg, limit: int | None = None) -> pd.DataFrame:
    """Fetch every new pass: cloud mask first, and the water bands only if clear enough."""
    paths = cfg.paths.ensure()
    grid = Grid.from_config(cfg)
    icfg = cfg.imagery
    existing = load_catalog(cfg)
    records = {k: {"key": k, **row} for k, row in existing.to_dict("index").items()}

    console.log("Imagery: searching NASA CMR for HLS passes")
    scenes = hls.search(cfg)
    todo = [s for s in scenes if s.key not in records]
    if limit:
        todo = todo[:limit]
    console.log(f"Imagery: {len(scenes)} passes found, {len(existing)} already processed, {len(todo)} to fetch")
    if not todo:
        return existing

    fs = hls.login()
    lock = threading.Lock()

    def process(scene: hls.Scene) -> dict:
        fmask = hls.read_band(scene, "fmask", grid, fs)
        clear = cloudmask.clear_mask_from_config(fmask, icfg)
        frac = cloudmask.clear_fraction(clear)
        rec = {
            "key": scene.key,
            "sensor": scene.sensor,
            "time": scene.time,
            "granules": ";".join(scene.granules),
            "clear_frac": frac,
            "used": False,
            "otsu": np.nan,
        }
        if frac < icfg.min_clear_fraction:
            return rec
        green = hls.read_band(scene, "green", grid, fs)
        swir = hls.read_band(scene, "swir1", grid, fs)
        idx = water_index.mndwi(green, swir)
        idx[~clear] = np.nan
        grid.write(paths.mndwi / f"{scene.key}.tif", water_index.encode(idx), nodata=-32768, tags={"sensor": scene.sensor, "time": scene.time})
        rec.update(used=True, otsu=water_index.otsu_threshold(idx))
        return rec

    done = failed = 0
    with ThreadPoolExecutor(max_workers=int(icfg.workers)) as ex:
        futures = {ex.submit(process, s): s for s in todo}
        for fut in as_completed(futures):
            scene = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # network hiccup: leave it for the next run
                failed += 1
                console.log(f"[yellow]Imagery: {scene.key} failed ({type(exc).__name__}: {exc}); will retry next run")
                continue
            with lock:
                records[rec["key"]] = rec
                done += 1
                if done % 25 == 0 or done == len(todo) - failed:
                    used = sum(1 for r in records.values() if r["used"])
                    console.log(f"Imagery: {done}/{len(todo)} fetched, {used} clear enough so far")
                    _save_catalog(cfg, records)
    return _save_catalog(cfg, records)


def classify_all(cfg) -> pd.DataFrame:
    """(Re)write per-date masks from the cached MNDWI with the configured thresholds."""
    paths = cfg.paths.ensure()
    grid = Grid.from_config(cfg)
    th = cfg.imagery.mndwi
    tag = f"{th.water_above:.3f}/{th.dry_below:.3f}"
    cat = load_catalog(cfg)
    used = cat[cat["used"].astype(bool)]
    counts = []
    for key in used.index:
        out = paths.masks / f"{key}.tif"
        if out.exists():
            with rasterio.open(out) as src:
                if src.tags().get("thresholds") == tag:
                    cls = src.read(1)
                    counts.append(_counts(key, cls))
                    continue
        with rasterio.open(paths.mndwi / f"{key}.tif") as src:
            idx = water_index.decode(src.read(1))
        cls = water_index.classify(idx, th.water_above, th.dry_below)
        grid.write(out, cls, nodata=water_index.NODATA, tags={"thresholds": tag, "classes": "0 dry, 1 water, 2 ambiguous, 255 no data"})
        counts.append(_counts(key, cls))
    if counts:
        c = pd.DataFrame(counts).set_index("key")
        cat = cat.drop(columns=[x for x in c.columns if x in cat.columns]).join(c)
        cat.to_parquet(catalog_path(cfg))
        console.log(f"Masks: {len(counts)} wet/dry masks ready (thresholds water >= {th.water_above}, dry <= {th.dry_below})")
    return cat


def _counts(key, cls):
    n = cls.size
    return {
        "key": key,
        "frac_dry": np.count_nonzero(cls == water_index.DRY) / n,
        "frac_water": np.count_nonzero(cls == water_index.WATER) / n,
        "frac_ambiguous": np.count_nonzero(cls == water_index.AMBIGUOUS) / n,
    }


def load_stack(cfg, keys) -> np.ndarray:
    """Stack the masks for the given scene keys into a (T, H, W) uint8 cube."""
    grid = Grid.from_config(cfg)
    cube = np.empty((len(keys), *grid.shape), dtype=np.uint8)
    for i, key in enumerate(keys):
        with rasterio.open(cfg.paths.masks / f"{key}.tif") as src:
            cube[i] = src.read(1)
    return cube


def load_mndwi(cfg, key) -> np.ndarray:
    with rasterio.open(cfg.paths.mndwi / f"{key}.tif") as src:
        return water_index.decode(src.read(1))
