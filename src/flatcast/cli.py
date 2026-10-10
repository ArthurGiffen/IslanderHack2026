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
from flatcast.config import load_config
from flatcast.log import console

app = typer.Typer(add_completion=False, help="FlatCast: forecast exposure of the Upper Laguna Madre wind-tidal flats.", no_args_is_help=True)
STAGE_NAMES = list(pipeline.STAGES)


@app.command()
def run(
    only: Optional[list[str]] = typer.Option(None, "--only", help=f"Run just these stages: {', '.join(STAGE_NAMES)}"),
    start: Optional[str] = typer.Option(None, "--from", help="Start at this stage and run the rest"),
    config: Optional[Path] = typer.Option(None, help="Path to config.yaml"),
):
    """Run the whole pipeline (or part of it). Safe to rerun: finished work is cached."""
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
    results = pipeline.run(cfg, stages)
    table = Table(title="FlatCast run", show_lines=False)
    table.add_column("Stage")
    table.add_column("Result")
    for k, v in results.items():
        table.add_row(k, f"[green]{v}" if v == "done" else f"[yellow]{v}")
    console.print(table)


@app.command()
def forecast(config: Optional[Path] = typer.Option(None, help="Path to config.yaml")):
    """Refresh just the 48-hour forecast and the web map (for a scheduled job)."""
    cfg = load_config(config)
    pipeline.run(cfg, ["forecast", "site"])


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
