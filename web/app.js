/* FlatCast web map. Static: everything comes from ./data/. The exposure map for any hour is
   computed here, in the browser, from each area's trained model (data/<area>/model.bin). */
(() => {
  "use strict";

  const TZ = "America/Chicago";
  const STOPS = [[0, [20, 92, 122]], [0.2, [63, 143, 168]], [0.5, [239, 230, 207]], [0.8, [224, 178, 92]], [1, [201, 121, 43]]];
  const WALK = [198, 255, 61];
  const OUTLOOK_YEARS = 8;
  const NA16 = -32768;
  const $ = (id) => document.getElementById(id);
  const fmt = {
    when: new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric", hour: "numeric" }),
    hour: new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric" }),
    day: new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short" }),
    dayhour: new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", hour: "numeric" }),
    date: new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "long", month: "long", day: "numeric", year: "numeric" }),
    full: new Intl.DateTimeFormat("en-US", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }),
    parts: new Intl.DateTimeFormat("en-US", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" }),
  };
  const state = {
    areas: [], attribution: [], area: null, map: null, mapReady: false, markers: [],
    mode: "forecast", tab: "flats", series: [], hour: 0, playing: null, walk: true, slrIndex: 0, cache: new Map(),
    spotMarkers: [], addingSpot: false,
  };

  const getJSON = (url) => fetch(url, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const getBin = (url) => fetch(url, { cache: "no-cache" }).then((r) => (r.ok ? r.arrayBuffer() : null)).catch(() => null);
  const lut = (() => {
    const out = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) {
      const p = i / 255;
      let j = 1;
      while (j < STOPS.length - 1 && p > STOPS[j][0]) j++;
      const [a, ca] = STOPS[j - 1], [b, cb] = STOPS[j];
      const t = Math.min(1, Math.max(0, (p - a) / (b - a)));
      for (let c = 0; c < 3; c++) out[i * 3 + c] = ca[c] + (cb[c] - ca[c]) * t;
    }
    return out;
  })();
  const color = (p) => {
    if (p == null || Number.isNaN(p)) return "transparent";
    const i = Math.round(Math.min(1, Math.max(0, p)) * 255) * 3;
    return `rgb(${lut[i]},${lut[i + 1]},${lut[i + 2]})`;
  };
  const compass = (deg) => (deg == null ? "" : ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][Math.round(deg / 22.5) % 16]);
  const cm = (m) => `${m >= 0 ? "+" : "−"}${Math.abs(m * 100).toFixed(0)} cm`;
  const sidePadding = () => {
    const w = window.innerWidth, h = window.innerHeight;
    if (w > 760) return { top: 70, bottom: 30, left: 30, right: Math.min(450, Math.max(30, w - 300)) };
    return { top: 110, bottom: Math.round(h * 0.47), left: 12, right: 12 };
  };

  // ------------------------------------------------------------------------------- time helpers
  function ctParts(ms) {
    const o = {};
    for (const p of fmt.parts.formatToParts(new Date(ms))) o[p.type] = p.value;
    return { y: +o.year, m: +o.month, d: +o.day, h: +o.hour };
  }
  /** UTC milliseconds of local (Central) midnight on y-m-d. */
  function ctMidnight(y, m, d) {
    let guess = Date.UTC(y, m - 1, d, 6);
    for (let i = 0; i < 3; i++) {
      const p = ctParts(guess);
      const diff = (Date.UTC(p.y, p.m - 1, p.d, p.h) - Date.UTC(y, m - 1, d, 0)) / 3600e3;
      if (diff === 0) break;
      guess -= diff * 3600e3;
    }
    return guess;
  }
  const isoDate = (ms) => { const p = ctParts(ms); return `${p.y}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`; };

  // ------------------------------------------------------------------------------- area data
  async function loadArea(id) {
    if (state.cache.has(id)) return state.cache.get(id);
    const base = `data/${id}/`;
    const [meta, fc, flats, slr, model, hist] = await Promise.all([
      getJSON(base + "meta.json"), getJSON(base + "forecast.json"), getJSON(base + "flats.geojson"),
      getJSON(base + "slr/slr_summary.json"), getBin(base + "model.bin"), getBin(base + "history.bin"),
    ]);
    const area = { id, base, meta, fc, flats, slr };
    if (meta && meta.has_model && model) {
      const M = meta.model, N = M.width * M.height;
      const z16 = new Int16Array(model, 0, N), k16 = new Int16Array(model, N * 2, N), fid = new Uint8Array(model, N * 4, N);
      const idx = [];
      for (let i = 0; i < N; i++) if (z16[i] !== NA16) idx.push(i);
      const n = idx.length, A = M.A;
      const px = { idx: Int32Array.from(idx), z: new Float32Array(n), k: new Float32Array(n), dx: new Float32Array(n), dy: new Float32Array(n), flat: new Uint8Array(n) };
      for (let j = 0; j < n; j++) {
        const i = idx[j], col = i % M.width, row = (i / M.width) | 0;
        px.z[j] = z16[i] * M.z_scale; px.k[j] = k16[i] * M.k_scale; px.flat[j] = fid[i];
        px.dx[j] = (col + 0.5 - M.gauge_col) * M.res_m / 1000;
        px.dy[j] = -(row + 0.5 - M.gauge_row) * M.res_m / 1000;
      }
      const flatPx = new Int32Array(M.flat_names.length + 1);
      for (let j = 0; j < n; j++) flatPx[px.flat[j]]++;
      area.px = px; area.A = A; area.flatPx = flatPx;
      area.canvas = document.createElement("canvas"); area.canvas.width = M.width; area.canvas.height = M.height;
      area.walkCanvas = document.createElement("canvas"); area.walkCanvas.width = M.width; area.walkCanvas.height = M.height;
    }
    if (meta && hist) {
      const H = meta.history.hours;
      area.hist = { start: Date.parse(meta.history.start), h: new Int16Array(hist, 0, H), tx: new Int16Array(hist, H * 2, H), ty: new Int16Array(hist, H * 4, H), meta: meta.history };
    }
    state.cache.set(id, area);
    return area;
  }

  // ------------------------------------------------------------------------------- the model, in the browser
  /** Probability dry for every flats pixel at gauge level h (m), wind stress tau, level uncertainty sigma (m). */
  function pDry(area, h, tx, ty, sigma) {
    const { px, A } = area, n = px.z.length, out = new Float32Array(n);
    const sx = A[0][0] * tx + A[0][1] * ty, sy = A[1][0] * tx + A[1][1] * ty;
    const blur = (Math.PI / 8) * sigma * sigma;
    for (let j = 0; j < n; j++) {
      const k = px.k[j];
      let a = k * (px.z[j] - (h + px.dx[j] * sx + px.dy[j] * sy));
      if (blur > 0) a /= Math.sqrt(1 + blur * k * k);
      out[j] = 1 / (1 + Math.exp(-a));
    }
    return out;
  }
  /** Long-range outlook: average over the typical levels for this time of year (calm winds). */
  function pOutlook(area, levels) {
    const { px } = area, n = px.z.length, out = new Float32Array(n), w = 1 / levels.length;
    for (const l of levels) for (let j = 0; j < n; j++) out[j] += w / (1 + Math.exp(-px.k[j] * (px.z[j] - l)));
    return out;
  }
  function probsFor(area, s) {
    if (!area.px) return null;
    if (!s.p) s.p = s.kind === "outlook" ? pOutlook(area, s.levels) : pDry(area, s.mid, s.tx || 0, s.ty || 0, s.sigma || 0);
    return s.p;
  }
  function flatShares(area, p, thr) {
    const counts = new Int32Array(area.flatPx.length);
    for (let j = 0; j < p.length; j++) if (p[j] >= thr) counts[area.px.flat[j]]++;
    return Array.from(counts, (c, i) => (area.flatPx[i] ? c / area.flatPx[i] : 0));
  }

  // ------------------------------------------------------------------------------- building the hourly series
  function histAt(area, ms) {
    const H = area.hist;
    if (!H) return null;
    const i = Math.round((ms - H.start) / 3600e3);
    if (i < 0 || i >= H.h.length || H.h[i] === NA16) return null;
    return { mid: H.h[i] * 0.001, tx: H.tx[i] === NA16 ? 0 : H.tx[i] * 0.1, ty: H.ty[i] === NA16 ? 0 : H.ty[i] * 0.1 };
  }
  function forecastSeries(area) {
    const fc = area.fc;
    if (!fc) return [];
    return fc.hours.map((d) => ({ t: Date.parse(d.time), kind: "forecast", mid: d.q50, lo: d.q10, hi: d.q90, sigma: d.sigma || 0, tx: d.tau_x || 0, ty: d.tau_y || 0, wind: d.wind_ms, windDir: d.wind_from_deg }));
  }
  function outlookLevels(area, ms) {
    const H = area.hist;
    if (!H) return [];
    const target = new Date(ms), hourUTC = target.getUTCHours();
    const doy = (t) => (Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()) - Date.UTC(t.getUTCFullYear(), 0, 0)) / 864e5;
    const td = doy(target), lastYear = new Date(H.start + (H.h.length - 1) * 3600e3).getUTCFullYear();
    const out = [];
    for (let i = hourUTC - new Date(H.start).getUTCHours(); i < H.h.length; i += 24) {
      if (i < 0 || H.h[i] === NA16) continue;
      const t = new Date(H.start + i * 3600e3);
      if (t.getUTCFullYear() <= lastYear - OUTLOOK_YEARS) continue;
      const dd = Math.abs(doy(t) - td);
      if (Math.min(dd, 365 - dd) <= 15) out.push(H.h[i] * 0.001);
    }
    return out;
  }
  const quantile = (arr, q) => { const s = [...arr].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))]; };
  function daySeries(area, y, m, d) {
    const start = ctMidnight(y, m, d), fcs = forecastSeries(area), out = [];
    const issue = area.fc ? Date.parse(area.fc.issue_time) : 0;
    const observed = new Map(((area.fc && area.fc.observed) || []).map((o) => [Date.parse(o.time), o.h]));
    for (let k = 0; k < 24; k++) {
      const t = start + k * 3600e3;
      const f = fcs.find((s) => s.t === t);
      const hist = histAt(area, t);
      if (hist && t <= issue) out.push({ t, kind: "observed", mid: hist.mid, lo: hist.mid, hi: hist.mid, sigma: 0, tx: hist.tx, ty: hist.ty });
      else if (observed.has(t) && t <= issue) out.push({ t, kind: "observed", mid: observed.get(t), lo: observed.get(t), hi: observed.get(t), sigma: 0, tx: 0, ty: 0 });
      else if (f) out.push({ ...f });
      else if (t > issue) {
        const levels = outlookLevels(area, t);
        if (levels.length) out.push({ t, kind: "outlook", levels, mid: quantile(levels, 0.5), lo: quantile(levels, 0.1), hi: quantile(levels, 0.9) });
        else out.push({ t, kind: "none" });
      } else out.push({ t, kind: "none" });
    }
    return out;
  }
  function annotateFlats(area) {
    if (!area.px) return;
    const thr = area.meta.walkable.p_dry;
    for (const s of state.series) if (s.kind !== "none") s.shares = flatShares(area, probsFor(area, s), thr);
  }

  // ------------------------------------------------------------------------------- map
  function initMap() {
    const map = new maplibregl.Map({
      container: "map", center: [-97.3, 27.6], zoom: 10, minZoom: 7, maxZoom: 16, attributionControl: { compact: true },
      style: {
        version: 8,
        sources: {
          s2: {
            type: "raster", tiles: ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"], tileSize: 256, maxzoom: 15,
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
  const blank = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
  // Image updates are coalesced to one per animation frame: rapid slider moves otherwise
  // start overlapping loads that cancel each other.
  const pendingImages = new Map();
  function setImage(id, url, corners, paint, before) {
    if (!state.mapReady) return;
    const first = !pendingImages.size;
    pendingImages.set(id, { url, corners, paint, before });
    if (first) requestAnimationFrame(flushImages);
  }
  function flushImages() {
    const map = state.map;
    for (const [id, { url, corners, paint, before }] of pendingImages) {
      const src = map.getSource(id);
      if (src) {
        if (src._flatUrl !== url || JSON.stringify(src._flatCorners) !== JSON.stringify(corners)) src.updateImage({ url: url || blank, coordinates: corners });
      } else {
        map.addSource(id, { type: "image", url: url || blank, coordinates: corners });
        map.addLayer({ id, type: "raster", source: id, paint }, before && map.getLayer(before) ? before : undefined);
      }
      const s = map.getSource(id);
      s._flatUrl = url;
      s._flatCorners = corners;
      map.setLayoutProperty(id, "visibility", url ? "visible" : "none");
      if (paint && paint["raster-opacity"] != null) map.setPaintProperty(id, "raster-opacity", paint["raster-opacity"]);
    }
    pendingImages.clear();
    showTrapLayers();
  }
  function drawMap() {
    const area = state.area, s = state.series[state.hour];
    if (!state.mapReady || !area) return;
    if (state.tab === "slr") {
      setImage("walk", null, area.meta.corners);
      const sc = area.slr && area.slr.scenarios[state.slrIndex];
      setImage("exposure", sc ? `${area.base}slr/${sc.png}` : null, area.meta.corners, { "raster-opacity": 0.92, "raster-fade-duration": 0 }, "flats-fill");
      return;
    }
    if (!area.px || !s || s.kind === "none") { setImage("exposure", null, area.meta.corners); setImage("walk", null, area.meta.corners); return; }
    const p = probsFor(area, s), M = area.meta.model, thr = area.meta.walkable.p_dry;
    const ctx = area.canvas.getContext("2d"), img = ctx.createImageData(M.width, M.height);
    const wctx = area.walkCanvas.getContext("2d"), wimg = wctx.createImageData(M.width, M.height);
    const { idx } = area.px;
    for (let j = 0; j < p.length; j++) {
      const o = idx[j] * 4, li = Math.round(p[j] * 255) * 3;
      img.data[o] = lut[li]; img.data[o + 1] = lut[li + 1]; img.data[o + 2] = lut[li + 2];
      img.data[o + 3] = p[j] < 0.2 ? 120 : 215;
      if (p[j] >= thr) { wimg.data[o] = WALK[0]; wimg.data[o + 1] = WALK[1]; wimg.data[o + 2] = WALK[2]; wimg.data[o + 3] = 235; }
    }
    ctx.putImageData(img, 0, 0); wctx.putImageData(wimg, 0, 0);
    setImage("exposure", area.canvas.toDataURL(), M.corners, { "raster-opacity": state.walk ? 0.55 : 0.92, "raster-fade-duration": 0, "raster-resampling": "nearest" }, "flats-fill");
    setImage("walk", state.walk ? area.walkCanvas.toDataURL() : null, M.corners, { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "nearest" }, "flats-fill");
    // flats that are walkable this hour light up
    if (state.map.getSource("flats") && s.shares) {
      (area.flats ? area.flats.features : []).forEach((f, i) => {
        const share = s.shares[i + 1] || 0;
        state.map.setFeatureState({ source: "flats", id: i }, { walk: state.walk && share >= area.meta.walkable.min_area_fraction });
      });
    }
  }
  function setFlatsLayer(area) {
    const map = state.map;
    state.markers.forEach((m) => m.remove());
    state.markers = [];
    const data = area.flats || { type: "FeatureCollection", features: [] };
    data.features.forEach((f, i) => (f.id = i));
    if (map.getSource("flats")) map.getSource("flats").setData(data);
    else {
      map.addSource("flats", { type: "geojson", data });
      map.addLayer({ id: "flats-fill", type: "fill", source: "flats", paint: { "fill-color": "#c6ff3d", "fill-opacity": ["case", ["boolean", ["feature-state", "walk"], false], 0.18, 0] } });
      map.addLayer({
        id: "flats-line", type: "line", source: "flats",
        paint: {
          "line-color": ["case", ["boolean", ["feature-state", "walk"], false], "#c6ff3d", "#ffffff"],
          "line-width": ["case", ["boolean", ["feature-state", "walk"], false], 3, 1.3],
          "line-opacity": 0.85,
        },
      });
    }
    for (const f of data.features) {
      const el = document.createElement("div");
      el.className = "flat-label";
      el.textContent = f.properties.name;
      state.markers.push(new maplibregl.Marker({ element: el }).setLngLat(centroid(f.geometry)).addTo(map));
    }
    const st = area.meta.station, pin = document.createElement("div");
    pin.className = "station-pin";
    state.markers.push(new maplibregl.Marker({ element: pin }).setLngLat([st.lon, st.lat]).setPopup(new maplibregl.Popup({ offset: 12 }).setText(`NOAA gauge ${st.id}`)).addTo(map));
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

  // ------------------------------------------------------------------------------- chart
  const niceStep = (raw) => { const p = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 2.5, 5, 10].map((k) => k * p).find((s) => s >= raw) || raw; };
  function drawChart() {
    const svg = $("chart"), area = state.area, series = state.series.filter((s) => s.kind !== "none");
    if (!area || !series.length) { svg.innerHTML = ""; return; }
    const W = svg.clientWidth || 360, H = svg.clientHeight || 132, m = { l: 34, r: 8, t: 12, b: 20 };
    let pre = [];
    if (state.mode === "forecast" && area.fc) pre = area.fc.observed.map((o) => ({ t: Date.parse(o.time), mid: o.h, kind: "observed" }));
    const all = [...pre, ...series];
    const t0 = all[0].t, t1 = all[all.length - 1].t + (state.mode === "date" ? 3600e3 : 0);
    const vals = all.flatMap((s) => [s.lo ?? s.mid, s.hi ?? s.mid]).filter(Number.isFinite);
    let y0 = Math.min(...vals), y1 = Math.max(...vals);
    const pad = Math.max(0.03, (y1 - y0) * 0.12); y0 -= pad; y1 += pad;
    const X = (t) => m.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - m.l - m.r);
    const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
    const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
    let out = "";
    // walkable hours: any flat walkable
    const thr = area.meta.walkable.min_area_fraction;
    for (const s of series) if (s.shares && s.shares.slice(1).some((v) => v >= thr)) {
      out += `<rect class="walk" x="${X(s.t) - (X(s.t + 3600e3) - X(s.t)) / 2}" y="${m.t}" width="${X(s.t + 3600e3) - X(s.t)}" height="${H - m.t - m.b}"/>`;
    }
    const step = niceStep((y1 - y0) / 3);
    out += '<g class="grid axis">';
    for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) out += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${m.l - 5}" y="${Y(v) + 3.5}" text-anchor="end">${v.toFixed(2)}</text>`;
    for (let t = Math.ceil(t0 / 3600e3) * 3600e3; t <= t1; t += 3600e3) {
      const hr = fmt.hour.format(new Date(t));
      if (state.mode === "forecast" ? hr === "12 AM" : ["6 AM", "12 PM", "6 PM"].includes(hr)) {
        out += `<text x="${X(t)}" y="${H - 5}" text-anchor="middle">${state.mode === "forecast" ? fmt.day.format(new Date(t)) : hr}</text>`;
      }
    }
    out += "</g>";
    for (const kind of ["outlook", "forecast"]) {
      const seg = series.filter((s) => s.kind === kind);
      if (seg.length > 1) {
        const band = [...seg.map((d) => [X(d.t), Y(d.hi)]), ...seg.slice().reverse().map((d) => [X(d.t), Y(d.lo)])];
        out += `<path class="band ${kind}" d="${path(band)}Z"/><path class="median ${kind}" d="${path(seg.map((d) => [X(d.t), Y(d.mid)]))}"/>`;
      }
    }
    const obs = [...pre, ...series.filter((s) => s.kind === "observed")];
    if (obs.length > 1) out += `<path class="obs" d="${path(obs.map((d) => [X(d.t), Y(d.mid)]))}"/>`;
    if (state.mode === "forecast" && area.fc) {
      const issue = Date.parse(area.fc.issue_time);
      out += `<line class="now-line" x1="${X(issue)}" x2="${X(issue)}" y1="${m.t}" y2="${H - m.b}"/><text class="label" x="${X(issue) - 4}" y="${m.t + 8}" text-anchor="end">observed</text><text class="label" x="${X(issue) + 4}" y="${m.t + 8}">forecast</text>`;
    }
    const cur = state.series[state.hour];
    if (cur && cur.kind !== "none") out += `<line class="cursor" x1="${X(cur.t)}" x2="${X(cur.t)}" y1="${m.t}" y2="${H - m.b}"/><circle class="cursor-dot" cx="${X(cur.t)}" cy="${Y(cur.mid)}" r="5"/>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = out;
  }

  // ------------------------------------------------------------------------------- panels
  const KIND_TEXT = {
    observed: "Measured water level at the gauge. This shows what the flats were like.",
    forecast: "Forecast from the wind forecast and recent water levels.",
    outlook: "Beyond the 48-hour forecast. This is the typical range for this time of year and hour (last 8 years, calm winds). Not a forecast.",
  };
  function setHour(i) {
    state.hour = Math.max(0, Math.min(state.series.length - 1, i));
    $("slider").value = state.hour;
    const s = state.series[state.hour], area = state.area;
    if (!s) return;
    $("when").textContent = fmt.when.format(new Date(s.t));
    const kindLabel = s.kind === "forecast" && area.fc
      ? `+${Math.round((s.t - Date.parse(area.fc.issue_time)) / 3600e3)} h`
      : { observed: "measured", outlook: "outlook", none: "no data" }[s.kind] || "";
    $("lead").textContent = kindLabel;
    if (s.kind === "none") {
      ["level", "wind", "exposed"].forEach((id) => ($(id).textContent = "–"));
      $("levelband").textContent = "no gauge reading"; $("winddir").textContent = "";
    } else {
      $("level").textContent = cm(s.mid);
      $("levelband").textContent = s.kind === "observed" ? "measured" : `likely ${Math.round(s.lo * 100)} to ${Math.round(s.hi * 100)} cm`;
      $("wind").textContent = s.wind != null ? `${(s.wind * 2.237).toFixed(0)} mph` : s.kind === "observed" ? `${Math.round(Math.sqrt(Math.hypot(s.tx, s.ty)) * 2.237)} mph` : "–";
      $("winddir").textContent = s.windDir != null ? `from ${compass(s.windDir)}` : s.kind === "observed" ? "6-h average" : "";
      const p = area.px ? probsFor(area, s) : null;
      if (p) {
        let c = 0;
        for (let j = 0; j < p.length; j++) if (p[j] >= area.meta.walkable.p_dry) c++;
        $("exposed").textContent = `${(c * (area.meta.model.res_m / 1000) ** 2).toFixed(1)} km²`;
      } else $("exposed").textContent = "–";
    }
    const note = $("kindnote");
    note.hidden = state.mode !== "date";
    note.className = `kindnote ${s.kind}`;
    note.textContent = KIND_TEXT[s.kind] || "No gauge reading for this hour.";
    drawMap();
    drawChart();
    document.querySelectorAll(".strip").forEach((st) => st.querySelectorAll("i").forEach((c, j) => c.classList.toggle("cur", j === state.hour)));
  }

  function windowsFor(area, fi) {
    const thr = area.meta.walkable.min_area_fraction, out = [];
    let start = null;
    state.series.forEach((s, i) => {
      const on = s.shares && s.shares[fi] >= thr;
      if (on && start === null) start = i;
      if ((!on || i === state.series.length - 1) && start !== null) {
        const end = on ? i : i - 1;
        out.push({ start: state.series[start].t, end: state.series[end].t + 3600e3, hours: end - start + 1, kind: state.series[start].kind });
        start = null;
      }
    });
    return out;
  }
  function renderFlats() {
    const list = $("flatlist"), area = state.area;
    $("minfrac").textContent = `${Math.round(area.meta.walkable.min_area_fraction * 100)}%`;
    $("pdry").textContent = `${Math.round(area.meta.walkable.p_dry * 100)}%`;
    if (!area.px || !area.flats || !area.flats.features.length) {
      list.innerHTML = `<div class="empty">Flat-by-flat windows appear once this area's satellite history has been processed (<code>flatcast run</code>).</div>`;
      return;
    }
    const span = state.mode === "forecast" ? "in the next 48 h" : "on this day";
    list.innerHTML = "";
    area.flats.features.forEach((f, i) => {
      const fi = i + 1, wins = windowsFor(area, fi), next = wins[0];
      const sameDay = next && fmt.day.format(new Date(next.start)) === fmt.day.format(new Date(next.end - 1));
      const windowText = next
        ? `<b>${next.hours >= 24 && state.mode === "date" ? "All day" : `${fmt.dayhour.format(new Date(next.start))} – ${(sameDay ? fmt.hour : fmt.dayhour).format(new Date(next.end))}`}</b> · ${next.hours} h${next.kind === "outlook" ? ' <span class="badge moderate">typical</span>' : ""}${wins.length > 1 ? ` <span class="flat-meta">+${wins.length - 1} more</span>` : ""}`
        : `No walkable window ${span}`;
      const el = document.createElement("article");
      el.className = "flat" + (next ? " has-window" : "");
      el.tabIndex = 0;
      el.innerHTML = `
        <div class="flat-head"><span class="flat-name">${f.properties.name}</span><span class="flat-meta">${f.properties.area_km2 ?? "?"} km² · half-dry at ${f.properties.half_dry_level_m != null ? cm(f.properties.half_dry_level_m) : "?"}</span></div>
        <div class="strip" style="grid-template-columns:repeat(${state.series.length},1fr)">${state.series.map((s, j) => `<i style="background:${s.shares ? color(s.shares[fi]) : "var(--line)"}" title="${fmt.dayhour.format(new Date(s.t))}: ${s.shares ? Math.round(s.shares[fi] * 100) + "% walkable" : "no data"}"></i>`).join("")}</div>
        <div class="flat-window">${windowText}</div>`;
      el.addEventListener("click", () => zoomToFlat(i));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") zoomToFlat(i); });
      list.appendChild(el);
    });
  }
  function zoomToFlat(i) {
    const feat = state.area.flats.features[i];
    if (feat) state.map.fitBounds(bboxOf(feat.geometry), { padding: sidePadding(), duration: 900 });
    if (window.innerWidth <= 760) $("sheet").classList.remove("expanded");
  }

  function renderSLR() {
    const s = state.area.slr, box = $("slrbuttons");
    if (!s) { box.innerHTML = ""; $("slrstats").innerHTML = `<div class="empty" style="grid-column:1/-1">The sea-level view appears once this area's exposure model has been fitted.</div>`; return; }
    box.innerHTML = s.scenarios.map((sc, i) => `<button aria-pressed="${i === state.slrIndex}" data-i="${i}">${sc.rise_cm ? "+" + sc.rise_cm + " cm" : "Today"}</button>`).join("");
    box.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { state.slrIndex = +b.dataset.i; renderSLR(); drawMap(); }));
    const sc = s.scenarios[state.slrIndex];
    const rows = (sc.flats || []).map((f) => `<div class="row"><span>${f.name}</span><div class="bar"><span style="width:${Math.round(f.share_lost * 100)}%"></span></div><span>${Math.round(f.share_lost * 100)}%</span></div>`).join("");
    $("slrstats").innerHTML = `
      <div class="stat"><span class="label">Exposed today</span><span class="value">${s.exposed_today_km2} km²</span><span class="sub">at least 5% of the time</span></div>
      <div class="stat"><span class="label">Lost</span><span class="value">${sc.permanently_flooded_km2} km²</span><span class="sub">${Math.round(sc.share_of_today_lost * 100)}% of today's flats</span></div>
      ${rows ? `<div class="lostbar">${rows}</div>` : ""}`;
  }

  async function renderAccuracy() {
    const area = state.area, base = area.base;
    const [wl, ex] = await Promise.all([getJSON(base + "validation/waterlevel_summary.json"), getJSON(base + "validation/exposure_summary.json")]);
    const box = $("accuracy");
    if (state.area !== area) return;
    if (!wl) { box.innerHTML = `<div class="empty">Validation results for this area appear after <code>flatcast run</code>.</div>`; return; }
    const method = wl["FlatCast, forecast winds"] ? "FlatCast, forecast winds" : "FlatCast, perfect winds";
    const year = wl._meta.holdout_year;
    const cell = (L) => `<div class="stat"><span class="label">${L} h ahead</span><span class="value">±${wl[method][L].mae_cm.toFixed(1)} cm</span><span class="sub">no-change guess ±${wl["Persistence (no change)"][L].mae_cm.toFixed(1)}</span></div>`;
    let html = `<div class="acc"><p class="hint">Scored on <b>${year}</b>, a full year the models never saw while training${wl._meta.archived_wind_forecasts ? ", using the wind forecasts that were actually issued at the time" : ""}.</p>
      <h3>Water level at the gauge</h3><div class="acc-grid">${cell("6")}${cell("24")}${cell("48")}</div>
      <img src="${base}validation/waterlevel_error_by_lead.png" alt="Forecast error by lead time" loading="lazy">`;
    if (ex && ex.from_observed_gauge) {
      const o = ex.from_observed_gauge, f = ex.from_24h_forecast;
      html += `<h3>Wet / dry maps</h3><p class="hint">Scored on the ${ex.flats_zone_km2} km² of ground that changes between wet and dry, on ${o.images} held-out images.</p>
        <div class="acc-grid">
          <div class="stat"><span class="label">From gauge</span><span class="value">${(o.accuracy * 100).toFixed(0)}%</span><span class="sub">pixels right</span></div>
          <div class="stat"><span class="label">24 h ahead</span><span class="value">${f ? (f.accuracy * 100).toFixed(0) + "%" : "–"}</span><span class="sub">pixels right</span></div>
          <div class="stat"><span class="label">Guessing</span><span class="value">${(o.climatology_accuracy * 100).toFixed(0)}%</span><span class="sub">"usual state"</span></div>
        </div>`;
    }
    const events = area.meta.low_water_events || [];
    if (events.length) html += `<h3>The ${events.length} lowest-water days of ${year}</h3><div class="gallery">${events.map((e) => `<figure><img src="${base}validation/${e}" alt="Predicted versus actual" loading="lazy"><figcaption>${e.replace(/^\d+_/, "").replace(".png", "")}</figcaption></figure>`).join("")}</div>`;
    box.innerHTML = html + "</div>";
  }

  // ------------------------------------------------------------------------------- plain-language summary
  function renderSummary() {
    const area = state.area, box = $("summary");
    if (!area || !area.px || !area.flats || !state.series.length) { box.hidden = true; return; }
    box.hidden = false;
    const names = area.flats.features.map((f) => f.properties.name);
    const wins = names.map((n, i) => ({ name: n, w: windowsFor(area, i + 1) })).filter((x) => x.w.length);
    const range = (w) => {
      if (w.hours >= 24 && state.mode === "date") return `all day ${fmt.day.format(new Date(w.start))}`;
      const same = fmt.day.format(new Date(w.start)) === fmt.day.format(new Date(w.end - 1));
      return `${fmt.dayhour.format(new Date(w.start))} – ${(same ? fmt.hour : fmt.dayhour).format(new Date(w.end))}`;
    };
    if (wins.length) {
      wins.sort((a, b) => a.w[0].start - b.w[0].start || b.w[0].hours - a.w[0].hours);
      const first = wins[0], more = wins.length - 1;
      const typical = first.w[0].kind === "outlook" ? " (typical for this time of year)" : "";
      box.className = "summary good";
      box.innerHTML = `<b>${state.mode === "forecast" ? "Next walkable" : "Walkable"}:</b> ${first.name}, ${range(first.w[0])}${typical}${more ? ` · plus ${more} other flat${more > 1 ? "s" : ""}` : ""}. <a href="#" id="jump">Show me</a>`;
      $("jump").addEventListener("click", (e) => { e.preventDefault(); setHour(state.series.findIndex((s) => s.t === first.w[0].start)); });
    } else {
      const levels = area.flats.features.map((f) => f.properties.half_dry_level_m).filter(Number.isFinite).sort((a, b) => a - b);
      const need = levels.length ? levels[Math.floor(levels.length / 2)] - 0.05 : null;
      const s = state.series.find((x) => x.kind !== "none");
      box.className = "summary bad";
      box.innerHTML = `<b>No walkable flats ${state.mode === "forecast" ? "in the next 48 h" : "on this day"}.</b>${s && need != null ? ` Water is around ${cm(s.mid)}; most flats here need about ${cm(need)} or lower.` : ""}`;
    }
  }

  // ------------------------------------------------------------------------------- tap the map for an answer
  function lonlatToPixel(corners, w, h, lng, lat) {
    const [tl, tr, , bl] = corners;
    const ux = tr[0] - tl[0], uy = tr[1] - tl[1], vx = bl[0] - tl[0], vy = bl[1] - tl[1];
    const px = lng - tl[0], py = lat - tl[1], det = ux * vy - uy * vx;
    const a = (px * vy - py * vx) / det, b = (ux * py - uy * px) / det;
    return { col: Math.floor(a * w), row: Math.floor(b * h), inside: a >= 0 && a < 1 && b >= 0 && b < 1 };
  }
  function pixelLookup(area) {
    if (area.lookup) return area.lookup;
    const M = area.meta.model, pos = new Int32Array(M.width * M.height).fill(-1);
    area.px.idx.forEach((i, j) => (pos[i] = j));
    return (area.lookup = pos);
  }
  async function trapMask(area) {
    if (area.trapMask !== undefined) return area.trapMask;
    area.trapMask = null;
    if (!area.meta.traps) return null;
    const img = new Image();
    img.src = `${area.base}traps/trap_water.png`;
    try { await img.decode(); } catch (e) { return null; }
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    area.trapMask = { w: img.width, h: img.height, data: ctx.getImageData(0, 0, img.width, img.height).data };
    return area.trapMask;
  }
  async function explainPoint(e) {
    const area = state.area, s = state.series[state.hour];
    if (!area) return;
    const { lng, lat } = e.lngLat;
    const lines = [];
    if (area.px && s && s.kind !== "none") {
      const M = area.meta.model, p = lonlatToPixel(M.corners, M.width, M.height, lng, lat);
      const j = p.inside ? pixelLookup(area)[p.row * M.width + p.col] : -1;
      if (j >= 0) {
        const pr = probsFor(area, s)[j], flat = area.px.flat[j];
        const verdict = pr >= area.meta.walkable.p_dry ? "<b class='ok'>likely walkable</b>" : pr >= 0.2 ? "<b class='maybe'>could go either way</b>" : "<b class='no'>likely under water</b>";
        lines.push(`<div class="pop-when">${fmt.when.format(new Date(s.t))}${s.kind === "outlook" ? " · typical" : ""}</div>`);
        lines.push(`<div class="pop-big">${Math.round(pr * 100)}% chance dry: ${verdict}</div>`);
        lines.push(`<div>${flat ? `In <b>${M.flat_names[flat - 1]}</b>. ` : ""}This spot is dry half the time when the gauge reads <b>${cm(area.px.z[j])}</b>.</div>`);
      }
    }
    if (state.tab === "traps") {
      const tm = await trapMask(area);
      if (tm) {
        const p = lonlatToPixel(area.meta.corners, tm.w, tm.h, lng, lat);
        if (p.inside && tm.data[(p.row * tm.w + p.col) * 4 + 3] > 0) lines.push(`<div class="pop-trap">Suggested trap water: stays covered even at low water, close to the flats.</div>`);
      }
    }
    if (!lines.length) lines.push(`<div>Open water, channel or dry land. FlatCast only forecasts the flats that go wet and dry.</div>`);
    new maplibregl.Popup({ maxWidth: "280px" }).setLngLat([lng, lat]).setHTML(lines.join("")).addTo(state.map);
  }

  // ------------------------------------------------------------------------------- crab traps
  function nextClosure(now = new Date()) {
    for (let y = now.getFullYear(); y <= now.getFullYear() + 1; y++) {
      const feb1 = new Date(y, 1, 1), firstFri = 1 + ((5 - feb1.getDay() + 7) % 7);
      const start = new Date(y, 1, firstFri + 14), end = new Date(y, 1, firstFri + 23, 23, 59);
      if (end >= now) return { start, end, active: now >= start };
    }
  }
  function renderClosure() {
    const c = nextClosure(), box = $("closure");
    const d = (x) => x.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    const days = Math.ceil((c.start - Date.now()) / 864e5);
    if (c.active) { box.className = "closure now"; box.innerHTML = `<b>Crab traps are closed now</b> (${d(c.start)} – ${d(c.end)}). Traps left in the water are treated as abandoned.`; }
    else if (days <= 45) { box.className = "closure soon"; box.innerHTML = `<b>Closure in ${days} days:</b> pull your traps before ${d(c.start)} (closed through ${d(c.end)}).`; }
    else { box.className = "closure"; box.innerHTML = `Traps are open. Next annual closure: <b>${d(c.start)} – ${d(c.end)}</b>.`; }
  }
  function setTrapLayers(area) {
    const map = state.map;
    const t = area.meta.traps;
    setImage("traps", t ? `${area.base}traps/trap_water.png` : null, area.meta.corners, { "raster-opacity": 0.9, "raster-fade-duration": 0, "raster-resampling": "nearest" }, "flats-fill");
    const data = t ? `${area.base}traps/channels.geojson` : { type: "FeatureCollection", features: [] };
    if (map.getSource("channels")) map.getSource("channels").setData(data);
    else {
      map.addSource("channels", { type: "geojson", data });
      map.addLayer({ id: "channels", type: "line", source: "channels", paint: { "line-color": "#ff9f1c", "line-width": 2, "line-dasharray": [3, 2], "line-opacity": 0.95 } });
    }
    $("trapinfo").textContent = t
      ? `${t.area_km2} km² suggested in this area. Low water here is about ${cm(t.low_water_m)}.${t.channels.length ? " Channels kept clear: " + t.channels.join(", ") + "." : ""}`
      : "Suggested trap water appears once this area's exposure model has been fitted.";
    showTrapLayers();
  }
  function showTrapLayers() {
    if (!state.mapReady) return;
    const on = state.tab === "traps" && $("traptoggle").checked;
    for (const id of ["traps", "channels"]) if (state.map.getLayer(id)) state.map.setLayoutProperty(id, "visibility", on && (id !== "traps" || state.area.meta.traps) ? "visible" : "none");
    if (on && state.map.getLayer("traps") && state.map.getLayer("flats-fill")) state.map.moveLayer("traps", "flats-fill");
    if (on && state.map.getLayer("channels")) state.map.moveLayer("channels");
  }

  // My spots: kept in this browser only
  const spotKey = () => `flatcast.spots.${state.area.id}`;
  const loadSpots = () => { try { return JSON.parse(localStorage.getItem(spotKey()) || "[]"); } catch (e) { return []; } };
  const saveSpots = (s) => { try { localStorage.setItem(spotKey(), JSON.stringify(s)); } catch (e) { /* storage unavailable */ } };
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  function renderSpots() {
    state.spotMarkers.forEach((m) => m.remove());
    state.spotMarkers = [];
    const spots = loadSpots(), list = $("spotlist");
    list.innerHTML = spots.length ? "" : `<div class="empty">No spots yet. Tap “+ Add a spot”, then tap the map.</div>`;
    spots.sort((a, b) => (b.date || "").localeCompare(a.date || "")).forEach((s) => {
      const el = document.createElement("div");
      el.className = "spot";
      el.innerHTML = `<div><b>${esc(s.name) || "Spot"}</b> <span class="flat-meta">${esc(s.date)}${s.caught !== "" && s.caught != null ? ` · ${esc(s.caught)} crabs` : ""}</span>${s.notes ? `<div class="spot-notes">${esc(s.notes)}</div>` : ""}</div><button class="x" aria-label="Delete spot">×</button>`;
      el.querySelector(".x").addEventListener("click", () => { saveSpots(loadSpots().filter((x) => x.id !== s.id)); renderSpots(); });
      el.addEventListener("click", (ev) => { if (!ev.target.classList.contains("x")) state.map.flyTo({ center: [s.lng, s.lat], zoom: 14 }); });
      list.appendChild(el);
      const dot = document.createElement("div");
      dot.className = "spot-pin";
      state.spotMarkers.push(new maplibregl.Marker({ element: dot }).setLngLat([s.lng, s.lat]).setPopup(new maplibregl.Popup({ offset: 10 }).setHTML(`<b>${esc(s.name) || "Spot"}</b><br>${esc(s.date)}${s.caught !== "" && s.caught != null ? ` · ${esc(s.caught)} crabs` : ""}${s.notes ? `<br>${esc(s.notes)}` : ""}`)).addTo(state.map));
    });
  }
  function startAddSpot() {
    state.addingSpot = true;
    state.map.getCanvas().style.cursor = "crosshair";
    $("addspot").textContent = "Tap the map…";
    if (window.innerWidth <= 760) $("sheet").classList.remove("expanded");
  }
  function spotForm(lng, lat) {
    state.addingSpot = false;
    state.map.getCanvas().style.cursor = "";
    $("addspot").textContent = "+ Add a spot";
    const today = isoDate(Date.now());
    const form = document.createElement("form");
    form.className = "spotform";
    form.innerHTML = `<label>Name<input name="name" placeholder="e.g. North spoil island" maxlength="60"></label>
      <div class="row2"><label>Date<input name="date" type="date" value="${today}"></label><label>Crabs caught<input name="caught" type="number" min="0" inputmode="numeric"></label></div>
      <label>Notes<input name="notes" placeholder="bait, depth, keepers…" maxlength="200"></label>
      <div class="row2"><button class="btn" type="submit">Save spot</button><button class="btn ghost" type="button" data-cancel>Cancel</button></div>`;
    $("spotlist").prepend(form);
    selectTab("traps");
    form.querySelector("input").focus();
    form.querySelector("[data-cancel]").addEventListener("click", () => form.remove());
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const f = new FormData(form);
      const spots = loadSpots();
      spots.push({ id: Date.now().toString(36), lng: +lng.toFixed(6), lat: +lat.toFixed(6), name: f.get("name"), date: f.get("date"), caught: f.get("caught"), notes: f.get("notes") });
      saveSpots(spots);
      renderSpots();
    });
  }
  function exportSpots() {
    const blob = new Blob([JSON.stringify({ area: state.area.id, spots: loadSpots() }, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `flatcast-spots-${state.area.id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  async function importSpots(file) {
    try {
      const data = JSON.parse(await file.text());
      const incoming = (data.spots || []).filter((s) => Number.isFinite(s.lng) && Number.isFinite(s.lat));
      const have = loadSpots(), ids = new Set(have.map((s) => s.id));
      saveSpots([...have, ...incoming.filter((s) => !ids.has(s.id))]);
      renderSpots();
    } catch (e) { alert("That file isn't a FlatCast spots log."); }
  }

  // ------------------------------------------------------------------------------- modes and controls
  function rebuildSeries(keepTime) {
    const area = state.area, prevT = keepTime && state.series[state.hour] ? state.series[state.hour].t : null;
    if (state.mode === "forecast") state.series = forecastSeries(area);
    else {
      const [y, m, d] = $("datepick").value.split("-").map(Number);
      state.series = daySeries(area, y, m, d);
    }
    annotateFlats(area);
    $("slider").max = Math.max(0, state.series.length - 1);
    if (!state.series.length) {
      $("when").textContent = state.mode === "forecast" ? "No forecast for this area yet" : "No data for this date";
      $("lead").textContent = "";
      ["level", "wind", "exposed"].forEach((id) => ($(id).textContent = "–"));
      $("levelband").textContent = state.mode === "forecast" ? "try Pick a date" : "";
      $("winddir").textContent = "";
      $("summary").hidden = true;
      renderFlats();
      drawChart();
      drawMap();
      return;
    }
    let i = state.mode === "date" ? state.series.findIndex((s) => fmt.hour.format(new Date(s.t)) === "8 AM") : 0;
    if (prevT != null) { const j = state.series.findIndex((s) => s.t === prevT); if (j >= 0) i = j; }
    renderFlats();
    renderSummary();
    setHour(Math.max(0, i));
  }
  function setMode(mode) {
    state.mode = mode;
    $("mode-forecast").setAttribute("aria-pressed", String(mode === "forecast"));
    $("mode-date").setAttribute("aria-pressed", String(mode === "date"));
    $("datepick").hidden = mode !== "date";
    if (state.playing) play();
    rebuildSeries(false);
  }
  function play() {
    const btn = $("play");
    if (state.playing) { clearInterval(state.playing); state.playing = null; btn.classList.remove("playing"); return; }
    btn.classList.add("playing");
    if (state.tab === "slr") selectTab("flats");
    state.playing = setInterval(() => setHour(state.hour >= state.series.length - 1 ? 0 : state.hour + 1), 450);
  }
  function selectTab(name) {
    state.tab = name;
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    document.querySelectorAll(".pane").forEach((p) => (p.hidden = p.id !== `pane-${name}`));
    if (name === "slr" && state.playing) play();
    drawMap();
    showTrapLayers();
  }

  async function selectArea(id, fly = true) {
    const area = await loadArea(id);
    state.area = area;
    try { localStorage.setItem("flatcast.area", id); } catch (e) { /* storage unavailable */ }
    document.querySelectorAll("#areas button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === id)));
    $("areaname").textContent = area.meta ? area.meta.name : id;
    const fc = area.fc;
    $("issued").textContent = fc ? `${fmt.full.format(new Date(fc.issue_time))} CT (${fc.wind_provider} winds)` : "–";
    const n = $("notice");
    n.hidden = true;
    if (!area.px) { n.hidden = false; n.textContent = "Maps for this area appear once its satellite history has been processed. The water-level chart below is live."; }
    if (fc && (Date.now() - Date.parse(fc.issue_time)) / 3600e3 > 12) { n.hidden = false; n.textContent = `This forecast is ${Math.round((Date.now() - Date.parse(fc.issue_time)) / 3600e3)} hours old. Run "flatcast forecast" to refresh it.`; }
    const dp = $("datepick");
    if (area.hist) {
      dp.min = isoDate(area.hist.start);
      dp.max = isoDate(Date.now() + 365 * 864e5);
    }
    if (!dp.value) dp.value = isoDate(Date.now());
    if (state.mapReady) {
      setFlatsLayer(area);
      setTrapLayers(area);
      renderSpots();
      if (fly && area.meta) {
        const [w, s, e, nn] = area.meta.bbox;
        state.map.fitBounds([[w, s], [e, nn]], { padding: sidePadding(), duration: 1100 });
      }
    }
    state.slrIndex = 0;
    renderSLR();
    renderAccuracy();
    rebuildSeries(false);
  }

  async function boot() {
    const idx = await getJSON("data/areas.json");
    state.areas = (idx && idx.areas) || [];
    $("attrib").innerHTML = ((idx && idx.attribution) || []).map((a) => `<li>${a}</li>`).join("");
    state.map = initMap();
    const nav = $("areas");
    nav.innerHTML = state.areas.map((a) => `<button data-id="${a.id}" aria-pressed="false">${a.name}</button>`).join("");
    nav.hidden = state.areas.length < 2;
    nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => selectArea(b.dataset.id)));

    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
    $("slider").addEventListener("input", (e) => { if (state.tab === "slr") selectTab("flats"); setHour(+e.target.value); });
    $("play").addEventListener("click", play);
    $("grabber").addEventListener("click", () => $("sheet").classList.toggle("expanded"));
    $("mode-forecast").addEventListener("click", () => setMode("forecast"));
    $("mode-date").addEventListener("click", () => setMode("date"));
    $("datepick").addEventListener("change", () => { if ($("datepick").value) rebuildSeries(false); });
    $("walktoggle").addEventListener("change", (e) => { state.walk = e.target.checked; drawMap(); });
    $("traptoggle").addEventListener("change", showTrapLayers);
    $("addspot").addEventListener("click", startAddSpot);
    $("exportspots").addEventListener("click", exportSpots);
    $("importspots").addEventListener("change", (e) => { if (e.target.files[0]) importSpots(e.target.files[0]); e.target.value = ""; });
    $("tips-close").addEventListener("click", () => { $("tips").hidden = true; try { localStorage.setItem("flatcast.tips", "1"); } catch (e) { /* storage unavailable */ } });
    try { $("tips").hidden = localStorage.getItem("flatcast.tips") === "1"; } catch (e) { $("tips").hidden = false; }
    renderClosure();
    window.addEventListener("resize", () => { drawChart(); state.map.resize(); });
    new ResizeObserver(() => state.map.resize()).observe($("map"));
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT") return;
      if (e.key === "ArrowRight") setHour(state.hour + 1);
      if (e.key === "ArrowLeft") setHour(state.hour - 1);
      if (e.key === " ") { e.preventDefault(); play(); }
    });

    if (!state.areas.length) { $("when").textContent = "No data yet"; const n = $("notice"); n.hidden = false; n.textContent = "Run `flatcast run` to build the first forecast."; return; }
    let first = state.areas[0].id;
    try { const saved = localStorage.getItem("flatcast.area"); if (state.areas.some((a) => a.id === saved)) first = saved; } catch (e) { /* storage unavailable */ }
    state.map.on("click", (e) => { if (state.addingSpot) spotForm(e.lngLat.lng, e.lngLat.lat); else explainPoint(e); });
    state.map.on("load", async () => {
      state.mapReady = true;
      await selectArea(state.area ? state.area.id : first, true);
    });
    await selectArea(first, false);
  }

  boot();
})();
