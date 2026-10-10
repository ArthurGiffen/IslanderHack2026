"""Disk cache for HTTP JSON downloads, with polite retries."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from flatcast import __version__

USER_AGENT = f"FlatCast/{__version__}"
_session = requests.Session()


def fetch_json(
    url: str,
    params: dict | None,
    cache_path: Path | None,
    *,
    max_age_hours: float | None = None,
    headers: dict | None = None,
    retries: int = 4,
    pause: float = 0.0,
) -> dict:
    """GET a JSON document, reusing a cached copy when it exists and is fresh enough.

    max_age_hours=None means the cached copy never expires (historical data).
    """
    if cache_path is not None and cache_path.exists():
        age_h = (time.time() - cache_path.stat().st_mtime) / 3600
        if max_age_hours is None or age_h < max_age_hours:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            r = _session.get(url, params=params, headers=hdrs, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} from {r.url}")
            r.raise_for_status()
            data = r.json()
            break
        except (requests.RequestException, ValueError):
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp.replace(cache_path)
    if pause:
        time.sleep(pause)
    return data
