/* FlatCast web map. Static: everything comes from ./data/*.json and ./data/frames/*.png */
(() => {
  "use strict";

  const TZ = "America/Chicago";
  const STOPS = [[0, [20, 92, 122]], [0.2, [63, 143, 168]], [0.5, [239, 230, 207]], [0.8, [224, 178, 92]], [1, [201, 121, 43]]];
  const $ = (id) => document.getElementById(id);
  const fmt = {
    when: new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric", hour: "numeric" }),
    hour: new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric" }),
    day: new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short" }),
    dayhour: new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", hour: "numeric" }),
    full: new Intl.DateTimeFormat("en-US", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }),
  };
  const state = { fc: null, meta: null, flats: null, slr: null, hour: 1, playing: null, mode: "forecast", slrIndex: 0, map: null, images: {} };

  const getJSON = (url) => fetch(url, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const color = (p) => {
    if (p == null || Number.isNaN(p)) return "transparent";
    for (let i = 1; i < STOPS.length; i++) {
      if (p <= STOPS[i][0]) {
        const [a, ca] = STOPS[i - 1], [b, cb] = STOPS[i];
        const t = (p - a) / (b - a);
        return `rgb(${ca.map((c, k) => Math.round(c + (cb[k] - c) * t)).join(",")})`;
      }
    }
    return `rgb(${STOPS[STOPS.length - 1][1].join(",")})`;
  };
  const compass = (deg) => (deg == null ? "" : ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][Math.round(deg / 22.5) % 16]);
  // Keep the area of interest clear of the panel: side panel on desktop, bottom sheet on phones.
  const sidePadding = () => {
    const w = window.innerWidth, h = window.innerHeight;
    if (w > 760) return { top: 30, bottom: 30, left: 30, right: Math.min(450, Math.max(30, w - 300)) };
    return { top: 70, bottom: Math.round(h * 0.47), left: 12, right: 12 };
  };
  const cm = (m) => `${m >= 0 ? "+" : "−"}${Math.abs(m * 100).toFixed(0)} cm`;

  // ---------------------------------------------------------------------------------- map
  function initMap(center) {
    const map = new maplibregl.Map({
      container: "map",
      center,
      zoom: 11.6,
      minZoom: 8,
      maxZoom: 16,
      attributionControl: { compact: true },
      style: {
        version: 8,
        sources: {
          s2: {
            type: "raster",
            tiles: ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"],
            tileSize: 256,
            maxzoom: 15,
            attribution: '<a href="https://s2maps.eu" target="_blank" rel="noopener">Sentinel-2 cloudless</a> by EOX (CC BY-NC-SA 4.0) · NOAA CO-OPS · NASA HLS · Open-Meteo',
          },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#0f1a1f" } },
          { id: "s2", type: "raster", source: "s2", paint: { "raster-saturation": -0.35, "raster-brightness-max": 0.82, "raster-contrast": 0.05 } },
        ],
      },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
    map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true }, trackUserLocation: true }), "top-left");
    return map;
  }

  function setOverlay(url) {
    const map = state.map;
    if (!map || !state.meta || !state.mapReady) return;
    const src = map.getSource("overlay");
    if (!url) {
      if (map.getLayer("overlay")) map.setLayoutProperty("overlay", "visibility", "none");
      return;
    }
    if (src) {
      src.updateImage({ url, coordinates: state.meta.corners });
      map.setLayoutProperty("overlay", "visibility", "visible");
    } else {
      map.addSource("overlay", { type: "image", url, coordinates: state.meta.corners });
      map.addLayer({ id: "overlay", type: "raster", source: "overlay", paint: { "raster-opacity": 0.92, "raster-fade-duration": 0, "raster-resampling": "linear" } }, map.getLayer("flats-line") ? "flats-line" : undefined);
    }
  }

  function addFlats() {
    const map = state.map;
    if (!state.flats || !state.flats.features.length) return;
    map.addSource("flats", { type: "geojson", data: state.flats });
    map.addLayer({ id: "flats-line", type: "line", source: "flats", paint: { "line-color": "#fff", "line-opacity": 0.75, "line-width": 1.4, "line-dasharray": [2, 1.5] } });
    for (const f of state.flats.features) {
      const c = centroid(f.geometry);
      const el = document.createElement("div");
      el.className = "flat-label";
      el.textContent = f.properties.name;
      new maplibregl.Marker({ element: el }).setLngLat(c).addTo(map);
    }
  }

  function centroid(geom) {
    const rings = geom.type === "Polygon" ? [geom.coordinates[0]] : geom.coordinates.map((p) => p[0]);
    let best = rings[0];
    for (const r of rings) if (r.length > best.length) best = r;
    let x = 0, y = 0;
    for (const [a, b] of best) { x += a; y += b; }
    return [x / best.length, y / best.length];
  }

  function bboxOf(geom) {
    const pts = (geom.type === "Polygon" ? geom.coordinates : geom.coordinates.flat()).flat();
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
  }

  // ---------------------------------------------------------------------------------- chart
  function drawChart() {
    const svg = $("chart");
    const fc = state.fc;
    if (!fc) return;
    const W = svg.clientWidth || 360, H = svg.clientHeight || 132;
    const m = { l: 34, r: 8, t: 10, b: 20 };
    const obs = fc.observed.map((d) => ({ t: Date.parse(d.time), h: d.h }));
    const hrs = fc.hours.map((d) => ({ t: Date.parse(d.time), lo: d.q10, mid: d.q50, hi: d.q90 }));
    const issue = Date.parse(fc.issue_time);
    const t0 = obs.length ? Math.min(obs[0].t, issue - 24 * 3600e3) : issue - 24 * 3600e3;
    const t1 = hrs.length ? hrs[hrs.length - 1].t : issue + 48 * 3600e3;
    const vals = [...obs.map((d) => d.h), ...hrs.flatMap((d) => [d.lo, d.hi])];
    let y0 = Math.min(...vals), y1 = Math.max(...vals);
    const pad = Math.max(0.03, (y1 - y0) * 0.12);
    y0 -= pad; y1 += pad;
    const X = (t) => m.l + ((t - t0) / (t1 - t0)) * (W - m.l - m.r);
    const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
    const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");

    let out = "";
    // walkable shading: any flat walkable
    if (fc.flats && fc.flats.length) {
      for (const f of fc.flats) for (const w of f.windows) {
        out += `<rect class="walk" x="${X(Date.parse(w.start))}" y="${m.t}" width="${Math.max(1, X(Date.parse(w.end)) - X(Date.parse(w.start)))}" height="${H - m.t - m.b}"/>`;
      }
    }
    // grid + y labels
    const step = niceStep((y1 - y0) / 3);
    out += '<g class="grid axis">';
    for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) {
      out += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${m.l - 5}" y="${Y(v) + 3.5}" text-anchor="end">${v.toFixed(2)}</text>`;
    }
    // day ticks at local midnight
    for (let t = t0; t <= t1; t += 3600e3) {
      if (fmt.hour.format(new Date(t)) === "12 AM") out += `<text x="${X(t)}" y="${H - 5}" text-anchor="middle">${fmt.day.format(new Date(t))}</text>`;
    }
    out += "</g>";
    if (hrs.length) {
      const band = [...hrs.map((d) => [X(d.t), Y(d.hi)]), ...hrs.slice().reverse().map((d) => [X(d.t), Y(d.lo)])];
      out += `<path class="band" d="${path(band)}Z"/>`;
      out += `<path class="median" d="${path([[X(issue), Y(obs.length ? obs[obs.length - 1].h : hrs[0].mid)], ...hrs.map((d) => [X(d.t), Y(d.mid)])])}"/>`;
    }
    if (obs.length) out += `<path class="obs" d="${path(obs.map((d) => [X(d.t), Y(d.h)]))}"/>`;
    out += `<line class="now-line" x1="${X(issue)}" x2="${X(issue)}" y1="${m.t}" y2="${H - m.b}"/>`;
    out += `<text class="label" x="${X(issue) - 4}" y="${m.t + 9}" text-anchor="end">observed</text><text class="label" x="${X(issue) + 4}" y="${m.t + 9}">forecast</text>`;
    const cur = hrs[state.hour - 1];
    if (cur) {
      out += `<line class="cursor" x1="${X(cur.t)}" x2="${X(cur.t)}" y1="${m.t}" y2="${H - m.b}"/><circle class="cursor-dot" cx="${X(cur.t)}" cy="${Y(cur.mid)}" r="5"/>`;
    }
    out += `<text class="axis" x="2" y="${m.t + 2}" font-size="9" fill="currentColor"></text>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = out;
  }
  const niceStep = (raw) => { const p = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) || raw; };

  // ---------------------------------------------------------------------------------- state
  function setHour(h) {
    state.hour = h;
    $("slider").value = h;
    const fc = state.fc;
    const d = fc && fc.hours[h - 1];
    if (!d) return;
    $("when").textContent = fmt.when.format(new Date(d.time));
    $("lead").textContent = `+${d.lead} h`;
    $("level").textContent = cm(d.q50);
    $("levelband").textContent = `likely ${Math.round(d.q10 * 100)} to ${Math.round(d.q90 * 100)} cm`;
    $("wind").textContent = d.wind_ms == null ? "–" : `${(d.wind_ms * 2.237).toFixed(0)} mph`;
    $("winddir").textContent = d.wind_from_deg == null ? "" : `from ${compass(d.wind_from_deg)}`;
    $("exposed").textContent = d.exposed_km2 == null ? "–" : `${d.exposed_km2.toFixed(1)} km²`;
    if (state.mode === "forecast" && d.png) setOverlay(`data/frames/${d.png}`);
    drawChart();
    document.querySelectorAll(".strip").forEach((s) => s.querySelectorAll("i").forEach((c, i) => c.classList.toggle("cur", i === h - 1)));
  }

  function play() {
    const btn = $("play");
    if (state.playing) { clearInterval(state.playing); state.playing = null; btn.classList.remove("playing"); return; }
    btn.classList.add("playing");
    if (state.mode !== "forecast") selectTab("flats");
    state.playing = setInterval(() => setHour(state.hour >= 48 ? 1 : state.hour + 1), 450);
  }

  function preload() {
    for (const d of state.fc.hours) if (d.png) { const img = new Image(); img.src = `data/frames/${d.png}`; state.images[d.lead] = img; }
  }

  // ---------------------------------------------------------------------------------- panels
  function renderFlats() {
    const list = $("flatlist");
    const fc = state.fc;
    $("minfrac").textContent = `${Math.round(fc.walkable.min_area_fraction * 100)}%`;
    $("pdry").textContent = `${Math.round(fc.walkable.p_dry * 100)}%`;
    if (!fc.maps || !fc.flats.length) {
      list.innerHTML = `<div class="empty">Flat-by-flat windows appear once the satellite history has been processed (<code>flatcast run</code> with an Earthdata login). The water-level forecast above is already live.</div>`;
      return;
    }
    list.innerHTML = "";
    for (const f of fc.flats) {
      const el = document.createElement("article");
      el.className = "flat";
      el.tabIndex = 0;
      const next = f.windows[0];
      const windowText = next
        ? `<b>${fmt.dayhour.format(new Date(next.start))} – ${(fmt.day.format(new Date(next.start)) === fmt.day.format(new Date(next.end)) ? fmt.hour : fmt.dayhour).format(new Date(next.end))}</b> · ${next.hours} h <span class="badge ${next.confidence}">${next.confidence}</span>${f.windows.length > 1 ? ` <span class="flat-meta">+${f.windows.length - 1} more</span>` : ""}`
        : "No walkable window in the next 48 h";
      el.innerHTML = `
        <div class="flat-head"><span class="flat-name">${f.name}</span><span class="flat-meta">${f.area_km2 ?? "?"} km² · half-dry at ${f.half_dry_level_m != null ? cm(f.half_dry_level_m) : "?"}</span></div>
        <div class="strip">${f.exposed.map((p, i) => `<i style="background:${color(p)}" title="${fmt.dayhour.format(new Date(fc.hours[i].time))}: ${Math.round(p * 100)}% exposed"></i>`).join("")}</div>
        <div class="flat-window">${windowText}</div>`;
      el.addEventListener("click", () => zoomToFlat(f.name));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") zoomToFlat(f.name); });
      list.appendChild(el);
    }
  }

  function zoomToFlat(name) {
    const feat = state.flats && state.flats.features.find((f) => f.properties.name === name);
    if (feat) state.map.fitBounds(bboxOf(feat.geometry), { padding: sidePadding(), duration: 900 });
    if (window.innerWidth <= 760) $("sheet").classList.remove("expanded");
  }

  function renderSLR() {
    const s = state.slr;
    const box = $("slrbuttons");
    if (!s) {
      box.innerHTML = "";
      $("slrstats").innerHTML = `<div class="empty" style="grid-column:1/-1">The sea-level view appears once the exposure model has been fitted.</div>`;
      return;
    }
    box.innerHTML = s.scenarios.map((sc, i) => `<button aria-pressed="${i === state.slrIndex}" data-i="${i}">${sc.rise_cm ? "+" + sc.rise_cm + " cm" : "Today"}</button>`).join("");
    box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { state.slrIndex = +b.dataset.i; renderSLR(); showSLR(); }));
    const sc = s.scenarios[state.slrIndex];
    const flatsRows = (sc.flats || []).map((f) => `<div class="row"><span>${f.name}</span><div class="bar"><span style="width:${Math.round(f.share_lost * 100)}%"></span></div><span>${Math.round(f.share_lost * 100)}%</span></div>`).join("");
    $("slrstats").innerHTML = `
      <div class="stat"><span class="label">Exposed today</span><span class="value">${s.exposed_today_km2} km²</span><span class="sub">at least 5% of the time</span></div>
      <div class="stat"><span class="label">Lost</span><span class="value">${sc.permanently_flooded_km2} km²</span><span class="sub">${Math.round(sc.share_of_today_lost * 100)}% of today's flats</span></div>
      ${flatsRows ? `<div class="lostbar">${flatsRows}</div>` : ""}`;
  }

  function showSLR() {
    const s = state.slr;
    if (!s) return setOverlay(null);
    setOverlay(`data/slr/${s.scenarios[state.slrIndex].png}`);
  }

  async function renderAccuracy() {
    const [wl, ex] = await Promise.all([getJSON("data/validation/waterlevel_summary.json"), getJSON("data/validation/exposure_summary.json")]);
    const box = $("accuracy");
    if (!wl) { box.innerHTML = `<div class="empty">Validation results appear after <code>flatcast run</code>.</div>`; return; }
    const method = wl["FlatCast, forecast winds"] ? "FlatCast, forecast winds" : "FlatCast, perfect winds";
    const year = wl._meta.holdout_year;
    const cell = (L) => `<div class="stat"><span class="label">${L} h ahead</span><span class="value">±${wl[method][L].mae_cm.toFixed(1)} cm</span><span class="sub">no-change guess ±${wl["Persistence (no change)"][L].mae_cm.toFixed(1)}</span></div>`;
    let html = `<div class="acc"><p class="hint">Everything here was scored on <b>${year}</b>, a full year the models never saw while training${wl._meta.archived_wind_forecasts ? ", using the wind forecasts that were actually issued at the time" : ""}.</p>
      <h3>Water level at Bird Island</h3><div class="acc-grid">${cell("6")}${cell("24")}${cell("48")}</div>
      <img src="data/validation/waterlevel_error_by_lead.png" alt="Forecast error by lead time" loading="lazy">`;
    if (ex && ex.from_observed_gauge) {
      const o = ex.from_observed_gauge, f = ex.from_24h_forecast;
      html += `<h3>Wet / dry maps</h3><p class="hint">Scored on the ${ex.flats_zone_km2} km² of ground that changes between wet and dry, on ${o.images} held-out images.</p>
        <div class="acc-grid">
          <div class="stat"><span class="label">From gauge</span><span class="value">${(o.accuracy * 100).toFixed(0)}%</span><span class="sub">pixels right</span></div>
          <div class="stat"><span class="label">24 h ahead</span><span class="value">${f ? (f.accuracy * 100).toFixed(0) + "%" : "–"}</span><span class="sub">pixels right</span></div>
          <div class="stat"><span class="label">Guessing</span><span class="value">${(o.climatology_accuracy * 100).toFixed(0)}%</span><span class="sub">"usual state"</span></div>
        </div>`;
    }
    const events = (state.meta && state.meta.low_water_events) || [];
    if (events.length) {
      html += `<h3>The ${events.length} lowest-water days of ${year}</h3><div class="gallery">${events.map((e) => `<figure><img src="data/validation/${e}" alt="Predicted versus actual" loading="lazy"><figcaption>${e.replace(/^\d+_/, "").replace(".png", "")}</figcaption></figure>`).join("")}</div>`;
    }
    box.innerHTML = html + "</div>";
  }

  function selectTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    document.querySelectorAll(".pane").forEach((p) => (p.hidden = p.id !== `pane-${name}`));
    const wasSLR = state.mode === "slr";
    state.mode = name === "slr" ? "slr" : "forecast";
    if (state.mode === "slr") { if (state.playing) play(); showSLR(); }
    else if (wasSLR) setHour(state.hour);
  }

  // ---------------------------------------------------------------------------------- boot
  async function boot() {
    const [meta, fc, flats, slr] = await Promise.all([getJSON("data/meta.json"), getJSON("data/forecast.json"), getJSON("data/flats.geojson"), getJSON("data/slr/slr_summary.json")]);
    Object.assign(state, { meta, fc, flats, slr });
    const center = meta ? [(meta.bbox[0] + meta.bbox[2]) / 2, (meta.bbox[1] + meta.bbox[3]) / 2] : [-97.32, 27.48];
    state.map = initMap(center);
    $("attrib").innerHTML = ((meta && meta.attribution) || []).map((a) => `<li>${a}</li>`).join("");

    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
    $("slider").addEventListener("input", (e) => { if (state.mode !== "forecast") selectTab("flats"); setHour(+e.target.value); });
    $("play").addEventListener("click", play);
    $("grabber").addEventListener("click", () => $("sheet").classList.toggle("expanded"));
    window.addEventListener("resize", () => { drawChart(); state.map.resize(); });
    new ResizeObserver(() => state.map.resize()).observe($("map"));
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT") return;
      if (e.key === "ArrowRight") setHour(Math.min(48, state.hour + 1));
      if (e.key === "ArrowLeft") setHour(Math.max(1, state.hour - 1));
      if (e.key === " ") { e.preventDefault(); play(); }
    });

    if (!fc) {
      $("when").textContent = "No forecast yet";
      const n = $("notice"); n.hidden = false; n.textContent = "Run `flatcast run` to build the first forecast.";
      return;
    }
    $("slider").max = fc.hours.length;
    $("issued").textContent = `${fmt.full.format(new Date(fc.issue_time))} CT (${fc.wind_provider} winds)`;
    if (!fc.maps) {
      const n = $("notice"); n.hidden = false;
      n.innerHTML = "Exposure maps appear once the satellite history has been processed. The <b>water-level forecast</b> below is live now.";
    }
    const ageH = (Date.now() - Date.parse(fc.issue_time)) / 3600e3;
    if (ageH > 12) { const n = $("notice"); n.hidden = false; n.textContent = `This forecast is ${Math.round(ageH)} hours old. Rerun \`flatcast forecast\` to refresh it.`; }

    renderFlats();
    renderSLR();
    renderAccuracy();
    state.map.on("load", () => {
      state.mapReady = true;
      if (meta && meta.station) {
        const el = document.createElement("div"); el.className = "station-pin"; el.title = `NOAA gauge ${meta.station.id} ${meta.station.name}`;
        new maplibregl.Marker({ element: el }).setLngLat([meta.station.lon, meta.station.lat]).setPopup(new maplibregl.Popup({ offset: 12 }).setText(`NOAA gauge ${meta.station.id} · ${meta.station.name}`)).addTo(state.map);
      }
      if (meta) state.map.fitBounds([[meta.bbox[0], meta.bbox[1]], [meta.bbox[2], meta.bbox[3]]], { padding: sidePadding(), duration: 0 });
      addFlats();
      if (fc.maps) preload();
      setHour(1);
    });
    setHour(1);
  }

  boot();
})();
