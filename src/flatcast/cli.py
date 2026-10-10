"""Command line: `flatcast run` reruns the whole pipeline."""

from __future__ import annotations

import functools
import http.server
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from flatcast import pipeline
from flatcast.config import area_configs, load_config
from flatcast.log import console

app = typer.Typer(add_completion=False, help="FlatCast: forecast exposure of the Upper Laguna Madre wind-tidal flats.", no_args_is_help=True)
STAGE_NAMES = list(pipeline.STAGES)


@app.command()
def run(
    only: Optional[list[str]] = typer.Option(None, "--only", help=f"Run just these stages: {', '.join(STAGE_NAMES)}"),
    start: Optional[str] = typer.Option(None, "--from", help="Start at this stage and run the rest"),
    area: Optional[str] = typer.Option(None, "--area", help="Only this area id (default: every area in config.yaml)"),
    config: Optional[Path] = typer.Option(None, help="Path to config.yaml"),
):
    """Run the whole pipeline (or part of it) for every area. Safe to rerun: finished work is cached."""
    cfg = load_config(config)
    if only:
        bad = [s for s in only if s not in STAGE_NAMES]
        if bad:
            raise typer.BadParameter(f"unknown stage(s) {bad}; choose from {STAGE_NAMES}")
        stages = [s for s in STAGE_NAMES if s in only]
    elif start:
        stages = STAGE_NAMES[STAGE_NAMES.index(start) :]
    else:
        stages = STAGE_NAMES
    _run_areas(cfg, stages, area)


def _run_areas(cfg, stages: list[str], area: str | None = None) -> None:
    areas = area_configs(cfg)
    if area:
        areas = [a for a in areas if a.area_id == area]
        if not areas:
            raise typer.BadParameter(f"no area {area!r}; choose from {[a.area_id for a in area_configs(cfg)]}")
    table = Table(title="FlatCast run", show_lines=False)
    table.add_column("Area")
    table.add_column("Stage")
    table.add_column("Result")
    area_stages = [s for s in stages if s != "site"]
    for a in areas:
        if area_stages:
            console.rule(f"[bold]{a.area.get('name', a.area_id)}")
            for k, v in pipeline.run(a, area_stages).items():
                table.add_row(a.area_id, k, f"[green]{v}" if v == "done" else f"[yellow]{v}")
    if "site" in stages:
        for k, v in pipeline.run(cfg, ["site"]).items():
            table.add_row("all", k, f"[green]{v}" if v == "done" else f"[yellow]{v}")
    console.print(table)


TRAINED_FILES = ["exposure.npz", "region.npy", "waterlevel/calibration.json"]
TRAINED_DIRS = ["waterlevel/production"]
VALIDATION_FILES = ["waterlevel_summary.json", "exposure_summary.json", "waterlevel_error_by_lead.png"]


@app.command("export-models")
def export_models(
    dest: Path = typer.Option(Path("trained"), help="Folder to write into (committed to git)"),
    config: Optional[Path] = typer.Option(None),
):
    """Copy each area's trained models and validation results into trained/<area>/ for deployment."""
    import shutil

    cfg = load_config(config)
    for a in area_configs(cfg):
        src, out = a.paths.models, cfg.root / dest / a.area_id
        if not (src / "waterlevel" / "production" / "meta.json").exists():
            console.log(f"[yellow]{a.area_id}: no trained models yet, skipped")
            continue
        for f in TRAINED_FILES:
            if (src / f).exists():
                (out / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / f, out / f)
        for d in TRAINED_DIRS:
            shutil.copytree(src / d, out / d, dirs_exist_ok=True)
        val = a.paths.outputs / "validation"
        for f in [*(val / v for v in VALIDATION_FILES), *sorted((val / "low_water_events").glob("*.png"))[:10]]:
            if f.exists():
                (out / "validation").mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out / "validation" / f.name)
        size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
        console.log(f"{a.area_id}: trained models written to {out} ({size:.1f} MB)")


@app.command("import-models")
def import_models(
    src: Path = typer.Option(Path("trained"), help="Folder written by export-models"),
    config: Optional[Path] = typer.Option(None),
):
    """Put committed models back where the pipeline expects them (used by the deployment job)."""
    import shutil

    cfg = load_config(config)
    for a in area_configs(cfg):
        inp = cfg.root / src / a.area_id
        if not inp.exists():
            console.log(f"[yellow]{a.area_id}: nothing in {inp}, skipped")
            continue
        a.paths.ensure()
        for item in inp.iterdir():
            if item.name == "validation":
                val = a.paths.outputs / "validation"
                (val / "low_water_events").mkdir(parents=True, exist_ok=True)
                for f in item.iterdir():
                    target = val / "low_water_events" / f.name if f.name[:2].isdigit() else val / f.name
                    shutil.copy2(f, target)
            elif item.is_dir():
                shutil.copytree(item, a.paths.models / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, a.paths.models / item.name)
        console.log(f"{a.area_id}: models imported from {inp}")


@app.command()
def forecast(
    area: Optional[str] = typer.Option(None, "--area", help="Only this area id"),
    config: Optional[Path] = typer.Option(None, help="Path to config.yaml"),
):
    """Refresh just the 48-hour forecasts and the web map (for a scheduled job)."""
    _run_areas(load_config(config), ["forecast", "site"], area)


@app.command()
def serve(port: int = 8000, config: Optional[Path] = typer.Option(None), open_browser: bool = True):
    """Preview the web map at http://127.0.0.1:PORT."""
    cfg = load_config(config)
    site = cfg.paths.site
    if not (site / "index.html").exists():
        raise typer.Exit("The site hasn't been built yet: run `flatcast run` first.")
    class NoCache(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

    handler = functools.partial(NoCache, directory=str(site))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}"
        console.print(f"Serving FlatCast at [bold]{url}[/] (Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(url)
        httpd.serve_forever()


@app.command()
def status(config: Optional[Path] = typer.Option(None)):
    """Show what has been downloaded and built."""
    import pandas as pd

    from flatcast.imagery.masks import load_catalog
    from flatcast.sources.hls import have_credentials

    cfg = load_config(config)
    p = cfg.paths
    t = Table(title="FlatCast status")
    t.add_column("Item")
    t.add_column("State")
    t.add_row("Earthdata login", "[green]set" if have_credentials() else "[yellow]not set")
    hourly = p.tables / "hourly.parquet"
    if hourly.exists():
        df = pd.read_parquet(hourly, columns=["h"])
        t.add_row("Gauge record", f"{df.index.min():%Y-%m-%d} to {df['h'].dropna().index.max():%Y-%m-%d %H:%M} UTC")
    else:
        t.add_row("Gauge record", "[yellow]not downloaded")
    cat = load_catalog(cfg)
    t.add_row("Satellite passes processed", f"{len(cat)} ({int(cat['used'].astype(bool).sum()) if len(cat) else 0} clear enough)")
    for label, path in (
        ("Water-level model", p.models / "waterlevel" / "production" / "meta.json"),
        ("Exposure model", p.models / "exposure.npz"),
        ("Latest forecast", p.outputs / "forecast" / "latest" / "forecast.json"),
        ("Web map", p.site / "index.html"),
        ("Named flats", p.flats),
    ):
        t.add_row(label, "[green]ready" if path.exists() else "[yellow]not yet")
    t.add_row("Data folder", str(p.data))
    console.print(t)


if __name__ == "__main__":
    app()
