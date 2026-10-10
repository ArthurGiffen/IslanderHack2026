"""Assemble the hourly gauge + wind table that every model reads.

Columns (UTC hourly index):
  h, h_gulf, h_south   water level (m NAVD88) at the primary, Gulf and south gauges
  tide                 astronomical tide prediction at the Gulf gauge (m)
  u_obs, v_obs         observed wind vector at the primary gauge (m/s toward east/north),
                       gaps filled from the reanalysis
  u_re, v_re, p_re     reanalysis wind and pressure at the forecast point; used as
                       "the forecast" when training, so training inputs look like
                       model winds rather than one anemometer
"""

from __future__ import annotations

import pandas as pd

from flatcast.log import console
from flatcast.sources import coops
from flatcast.sources.coops import to_uv
from flatcast.sources.wind import openmeteo


def table_path(cfg):
    return cfg.paths.tables / "hourly.parquet"


def build_hourly(cfg) -> pd.DataFrame:
    paths = cfg.paths.ensure()
    cache = paths.raw / "coops"
    st = cfg.stations
    start = pd.Timestamp(st.start, tz="UTC")
    end = pd.Timestamp.now(tz="UTC").floor("h")

    console.log(f"Gauges: downloading hourly water levels {start.date()} to {end.date()}")
    h = coops.water_level_hourly(st.primary, start, cache, st.datum, end)
    h_gulf = coops.water_level_hourly(st.gulf, start, cache, st.datum, end)
    h_south = coops.water_level_hourly(st.south, start, cache, st.datum, end)

    console.log("Gauges: downloading observed wind and tide predictions")
    wind = coops.wind_hourly(st.get("wind", st.primary), start, cache, end)
    tide = coops.tide_predictions_hourly(st.gulf, start, end + pd.Timedelta(days=3), cache, st.datum)

    console.log("Wind: downloading reanalysis history from Open-Meteo")
    lon, lat = cfg.wind.point
    re = openmeteo.archive(lon, lat, str(start.date()), str(end.date()), paths.raw)
    re = re.reindex(h.index)

    df = pd.DataFrame({"h": h, "h_gulf": h_gulf, "h_south": h_south}, index=h.index)
    df["tide"] = tide.reindex(df.index)
    u_obs, v_obs = to_uv(wind["speed"], wind["direction"])
    u_re, v_re = to_uv(re["speed"], re["direction"])
    df["u_re"], df["v_re"], df["p_re"] = u_re, v_re, re["pressure"].to_numpy()
    df["wind_from_station"] = pd.notna(u_obs)
    df["u_obs"] = pd.Series(u_obs, index=df.index).fillna(pd.Series(u_re, index=df.index))
    df["v_obs"] = pd.Series(v_obs, index=df.index).fillna(pd.Series(v_re, index=df.index))
    # Short gauge gaps (a few hours) are interpolated; longer ones stay missing.
    for c in ("h", "h_gulf", "h_south"):
        df[c] = df[c].interpolate(limit=3, limit_area="inside")

    df.to_parquet(table_path(cfg))
    cov = df[["h", "h_gulf", "h_south"]].notna().mean().round(3).to_dict()
    console.log(f"Gauges: {len(df):,} hours saved; coverage {cov}; station wind {df['wind_from_station'].mean():.0%}")
    return df


def load_hourly(cfg) -> pd.DataFrame:
    p = table_path(cfg)
    if not p.exists():
        raise FileNotFoundError("hourly table missing; run `flatcast run --only gauges` first")
    return pd.read_parquet(p)
