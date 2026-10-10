# Skyline Scanner
 
**A 48-hour forecast of which wind-tidal flats in the Upper Laguna Madre will be exposed or flooded, learned from twelve years of satellite history.**
 
## Live deployment
 
**https://islanderhack2026.vercel.app/**
 
The live site is static and updates itself every 3 hours.
 
The code and command are called `flatcast` (the forecasting engine); the web app is Skyline Scanner.
 
In the Laguna Madre the astronomical tide is only a few centimetres. Wind does the real work: a norther can drain the flats and a southeasterly can bury them, and because the flats are nearly level, a few centimetres flood or expose square kilometres. Tide tables can't predict that. FlatCast learns it from the record instead:
 
1. **Satellites** (Landsat 8/9 and Sentinel-2, harmonized by NASA as HLS) have photographed the area around Bird Island Basin on about 970 passes since 2013. Each clear image becomes a 30 m wet/dry map.
2. **The NOAA gauge at S. Bird Island** (inside the area) gives the water level and wind at each overpass.
3. **For each pixel**, FlatCast fits the water level at which it goes dry. That gives, in effect, a height map of the flats built from photographs. It also learns how wind tilts the water surface away from the gauge.
4. **A water-level model** forecasts the Bird Island level 6–48 hours ahead from the wind forecast, recent levels and the Gulf.
5. **Together** they give hourly maps of the chance each pixel is dry, a walkable window for each flat, and a mobile-friendly web map.
---
 
## Pipeline
 
| # | Stage | Phase | Output |
|---|---|---|---|
| 1 | `gauges` | 1 | Hourly water level at Bird Island, Packery Channel and Baffin Bay since 2013, station wind, Packery tide predictions, reanalysis wind |
| 2 | `imagery` | 1 | For each HLS pass: Fmask cloud/shadow mask → clear fraction; if ≥ 50% clear, MNDWI on the analysis grid |
| 3 | `masks` | 1 | One GeoTIFF per date: dry / water / ambiguous / no data |
| 4 | `pairs` | 1 | Each image with gauge level, wind and wind stress at overpass time |
| 5 | `inspect` | 1 | Contact sheets (true colour · MNDWI · classes · histogram) for checking thresholds by eye |
| 6 | `waterlevel` | 2 | LightGBM quantile models, 6–48 h ahead |
| 7 | `exposure` | 2 | Per-pixel exposure model (+ elevation map and wind tilt) |
| 8 | `validate` | 2 | Held-out-year scores, error-by-lead plot, 10 lowest-water side-by-sides |
| 9 | `forecast` | 3 | 48 hourly GeoTIFF + PNG maps, `forecast.json` with walkable windows |
| 10 | `slr` | 4 | Exposure under +10/25/50 cm sea level |
| 11 | `site` | 3 | Static MapLibre web map in `site/` |
 
MNDWI ≥ `water_above` is water, ≤ `dry_below` is dry, and anything between is **ambiguous** (wet sand, algal mat, a film of water) and is left out of the fit.
 
---
 
## Outputs
 
```
outputs/
  forecast/latest/            exposure_NNh.tif (percent chance dry), png/, forecast.json
  maps/                       half_dry_level_navd88_m.tif (the learned "elevation" map), fit_status.tif
  validation/                 waterlevel_errors.csv, waterlevel_error_by_lead.png, exposure_by_date.csv,
                              low_water_events/ (predicted vs actual, 10 lowest-water days)
  inspection/                 contact sheets for the MNDWI threshold
  slr/                        exposure_fraction_slr{0,10,25,50}cm.tif, slr_panels.png, slr_summary.json
site/                         the web map
config/flats.geojson          auto-detected flats
```
 
### Named flats
 
There's no official list of named flats here, so the flats are drawn from the model: connected areas that go wet and dry within the normal range of water levels. They're named Flat A, B, C… from north to south and written to `config/flats.geojson` (`config/flats_cc_bay.geojson` for Corpus Christi Bay). FlatCast never overwrites that file, so flats can be renamed or replaced with custom outlines.
 
### Areas
 
The web map has a switch between areas. Each area is its own small config file listed under `areas:` in `config.yaml`, and it overrides only what differs from the base:
 
| Area | File | Gauge | Record |
|---|---|---|---|
| Bird Island Basin | `config.yaml` | S. Bird Island 8776139 | 2013– |
| Corpus Christi Bay (southeast shore, behind Mustang Island) | `config/areas/cc_bay.yaml` | USS Lexington 8775296, with wind from Port Aransas 8775237 | 2015– |
 
### The web map
 
* **Next 48 h:** the forecast, hour by hour (slider or ▶).
* **Pick a date:** any date, hour by hour. **Past dates** use the water level the gauge measured, so you see what the flats were like. **Within 48 h** they use the forecast. **Further ahead** they show an **outlook**: how exposed each flat typically is at that hour and time of year (last 8 years of levels, calm winds). It's marked in purple and labelled as not a forecast.
* **Tap the map** anywhere for a plain answer ("72% chance dry: likely walkable. In Flat B."). A one-line summary at the top gives the next walkable window.
* **Crab traps** tab: suggested trap water (magenta) that stays covered even at low water, lies within 250 m of the flats and keeps 60 m clear of USACE-maintained channels (orange). Also the 2026–27 TPWD trap rules, a countdown to the annual February closure, and **My spots**: a catch log that stays on the user's device and can be saved and loaded as a file. The trap layer is a depth and habitat suggestion, not catch data (`src/flatcast/traps.py`).
* **Link drone** (under the logo): opens the Coastwatch beach-survey dashboard (`web/drone/`) in a full-screen panel. For now it is a **simulation** (route planning, camera view, trap and litter detection), clearly labelled "no drone connected". Hardware gets wired in there later.
* **Walkable:** highlights, in bright green, the ground with an 80%+ chance of being dry at the selected hour, and outlines the flats that have a walkable window that hour. The chart shades walkable hours.
The browser computes these maps itself from each area's trained model (`site/data/<area>/model.bin`, cropped to the flats) and its hourly gauge history (`history.bin`). It uses the same equation as the Python model: for 18 March 2025, both give 7.7 km² walkable at Bird Island.
 
---
 
## Drone simulation (Coastwatch)
 
The **Link drone** button under the logo opens the Coastwatch beach-survey dashboard (`web/drone/`) in a full-screen panel. It is currently a **simulation**, clearly labelled "no drone connected", with no hardware attached. It covers:
 
* **Route planning** for a beach survey
* **Camera view** from the simulated drone
* **Trap detection** (crab traps)
* **Litter detection**
It sits alongside the Crab traps tab, which suggests where traps can stay covered at low water. Real drone hardware gets wired into this panel later.
 
---
 
## Results so far
 
Water-level forecasts were scored on **2025**, a year left out of training. That's 1,452 forecasts, each using **the wind forecast actually issued at the time** (Open-Meteo's archive of past runs), not the wind that happened:
 
| Lead | FlatCast mean error | "No change" baseline | "No change + tide" |
|---|---|---|---|
| 6 h | **1.0 cm** | 2.6 cm | 4.6 cm |
| 24 h | **2.2 cm** | 2.7 cm | 2.9 cm |
| 48 h | **3.3 cm** | 4.4 cm | 4.8 cm |
 
The raw 10–90% band held about 73% of outcomes instead of 80%, so the forecast widens it using a per-lead calibration from the hold-out year (`calibration.json`).
 
**Wet/dry maps** (first full run, 9 Oct 2026): 973 HLS passes, 538 clear enough, 524 paired with a gauge reading. The model was scored on the 62 clear images from 2025, over the 7.8 km² of ground that switches between wet and dry within the normal range of water levels:
 
| Method | Pixels right |
|---|---|
| FlatCast, from the measured gauge level | **94.4%** |
| FlatCast, from the 24-hour water-level forecast | **94.1%** |
| Each pixel's usual state ("climatology") | 81.0% |
 
**Lesson from the first fit:** before the analysis was restricted to the lagoon, the model scored *below* climatology (72.7% vs 74.2%). In summer, the open Gulf east of Padre Island sometimes scores low on MNDWI (glint, turbid or Sargassum-covered water). The model took those flickering ocean pixels for flats. `area.lagoon_only` now limits the analysis to the lagoon (the water body around the Bird Island gauge) plus `shore_km` of shoreline, and `outputs/maps/analysis_region.tif` shows the region used. `outputs/inspection/winter_vs_summer.png` shows the effect.
 
**About this box:** around Bird Island Basin the wind-tidal flats are narrow (the lagoon shoreline and the edges of the spoil-island chain), so only ~8 km² of the 400 km² box changes state. The larger wind-tidal flats of the Upper Laguna Madre lie further south, toward Yarborough Pass and the Land Cut. Moving `area.bbox` south (and picking the nearest gauge as `stations.primary`) is the natural next step.
 
---
 
## Where this differs from the original spec, and why
 
| Spec | What FlatCast does | Why |
|---|---|---|
| Probability dry as a function of gauge level | Gauge level **plus a wind-tilt term** (a 2×2 matrix shared by the area) | Wind tilts the water surface. Away from the gauge, the local level can differ by many cm in a norther. Monotonic in level is kept. |
| Water / dry | Water / **ambiguous** / dry | At 30 m, wet sand, algal mats and centimetres of water are hard to tell apart. Forcing them into a class adds noise. |
| "Thousands of dates" | ~970 passes, about half clear enough | HLS starts in 2013 (Landsat 8) and 2015 (Sentinel-2), and clouds remove much of the rest. That's plenty for 2 parameters per pixel. |
| Train water level on wind | Train on reanalysis winds, **score on archived forecasts** | Training on measured winds and running on forecasts overstates accuracy. Archived forecasts only exist from Jan 2024. |
| "Walkable" | "Exposed" with a safety note | Satellites show dry ground, not firm footing. Soft mud, channels narrower than 30 m and fast-rising water don't show up. |
| Older Landsat (1980s) | Not used for the exposure model | The Bird Island gauge starts in 2003 and the hourly CO-OPS record in 2013, so older images can't be paired with a level. They could still show decades-scale change in the extent of the flats (a possible addition via Microsoft Planetary Computer's free Landsat Collection 2 catalogue). |
| Sea-level rise | Shift the recent 5-year level distribution; "permanent" = dry < 5% of the time | A single-level cut ignores how often water is low. This is still a "bathtub" view: flats can build up or migrate. |
 
---
 
## Data sources and terms (checked October 2026)
 
| Source | Use | Terms / limits |
|---|---|---|
| [NASA HLS v2.0](https://lpdaac.usgs.gov/products/hlss30v002/) via `earthaccess` | Imagery 2013– | NASA open data. Free Earthdata login. Read as windowed requests to cloud-optimized GeoTIFFs: only the ~20 km box is fetched. |
| [NOAA CO-OPS API](https://api.tidesandcurrents.noaa.gov/api/prod/) | Water level, wind, tide predictions | Public. Hourly products ≤ 1 year per request, 6-minute ≤ 31 days. FlatCast caches every response and pauses between requests. |
| [Open-Meteo](https://open-meteo.com) | Forecast, reanalysis and archived-forecast winds | Free for **non-commercial** use without a key. 10,000 calls/day, 5,000/hour, 600/min. CC BY 4.0. A full rebuild uses a few dozen calls. |
| [NWS API](https://www.weather.gov/documentation/services-web-api) | Alternative live wind forecast | Free. Asks for an identifying User-Agent (`project.contact` in config). No forecast archive. |
| [EOX Sentinel-2 cloudless](https://s2maps.eu) | Basemap | CC BY-NC-SA 4.0 (non-commercial). |
 
**Stations:** S. Bird Island **8776139** (primary, inside the area, since 2003), Packery Channel **8775792** (Gulf connection, tide predictions), Baffin Bay **8776604** (southern Laguna). Bob Hall Pier (8775870) is no longer in CO-OPS's list of active water-level stations.
 
---
 
## Credit: Conrad Blucher Institute
 
Water-level forecasting on the Texas coast owes a great deal to the **[Conrad Blucher Institute for Surveying and Science](https://www.cbi.tamucc.edu/)** at Texas A&M University–Corpus Christi. Its Texas Coastal Ocean Observation Network (TCOON) built and runs many of the gauges FlatCast depends on, including Bird Island, Packery Channel and Baffin Bay. Its researchers, working with the Corpus Christi National Weather Service office, developed neural-network forecasts of meteorologically driven water levels up to 48 hours ahead, and later deep-learning models for the same problem. FlatCast's gradient-boosting model is a simple baseline in that tradition. The code is built so that **CBI's forecast can replace it**: `src/flatcast/models/waterlevel/cbi.py` is the adapter slot and lists the steps.
 
## Project layout
 
```
config.yaml                  every setting: area, stations, thresholds, weights, providers
src/flatcast/
  sources/                   hls.py (NASA), coops.py (NOAA), wind/ (Open-Meteo, NWS)
  imagery/                   cloudmask.py, water_index.py, masks.py
  models/exposure.py         per-pixel monotonic logistic fit with wind tilt and recency weights
  models/waterlevel/         features, LightGBM baseline, CBI adapter
  pairing.py, hydro.py       image ↔ gauge ↔ wind tables
  validate.py                hold-out-year scoring and figures
  forecast.py, flats.py      48-h maps and walkable windows
  slr.py                     sea-level-rise view
  publish.py, pipeline.py, cli.py
web/                         site template (MapLibre, no build step)
notebooks/                   01_masks_vs_gauge.ipynb (Phase 1 check)
tests/                       cloud mask bits, MNDWI, per-pixel fit, forecast features, area configs
```
 
**Model size:** the exposure model is two numbers per pixel (a few MB). The water-level models are 24 small tree ensembles (~11 MB as text files). The forecast runs in about 10 seconds on a laptop.
 
> **Not a safety tool.** FlatCast estimates where a satellite would see dry ground. Always judge conditions on the water yourself.
 