(() => {
  // DOM helpers and input validation. Speeds are physical m/s; simrate scales time.
  const root = document.getElementById('beachsim'),
    q = (id) => root.querySelector('#' + id);
  if (typeof THREE === 'undefined') {
    q('telemetry').textContent = '3D library unavailable. Reload to retry.';
    return;
  }
  const speedSettings = { speed: 10, inspectSpeed: 8, descentSpeed: 2.025, ascentSpeed: 2.7 };
  function speedValue(id) {
    const v = Number(q(id).value),
      max = id === 'speed' || id === 'inspectSpeed' ? 100 : 20;
    if (q(id).value.trim() !== '' && Number.isFinite(v) && v >= 0.1 && v <= max)
      speedSettings[id] = v;
    return speedSettings[id];
  }
  for (const id of Object.keys(speedSettings)) {
    q(id).oninput = () => {
      const v = Number(q(id).value),
        max = id === 'speed' || id === 'inspectSpeed' ? 100 : 20,
        valid = q(id).value.trim() !== '' && Number.isFinite(v) && v >= 0.1 && v <= max;
      q(id).setAttribute('aria-invalid', String(!valid));
      speedValue(id);
      if (id === 'speed') updateRouteInfo();
    };
    q(id).onchange = () => {
      q(id).value = String(speedValue(id));
      q(id).setAttribute('aria-invalid', 'false');
      if (id === 'speed') updateRouteInfo();
    };
  }
  // Scene setup: beach, lighting, shoreline, and environmental props.
  const T = THREE,
    scene = new T.Scene(),
    css = getComputedStyle(root),
    col = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  scene.background = new T.Color(col('--background', '#dae8eb'));
  scene.add(new T.HemisphereLight(0xe5f3ff, 0x89734e, 2));
  let sun = new T.DirectionalLight(0xfff3dc, 3);
  sun.position.set(-25, 60, 30);
  scene.add(sun);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.camera.left = -65;
  sun.shadow.camera.right = 65;
  sun.shadow.camera.top = 65;
  sun.shadow.camera.bottom = -65;
  sun.shadow.camera.far = 180;
  function mesh(geo, color, x, y, z) {
    let m = new T.Mesh(geo, new T.MeshStandardMaterial({ color, roughness: 0.9 }));
    m.position.set(x, y, z);
    m.receiveShadow = true;
    m.castShadow = true;
    scene.add(m);
    return m;
  }
  mesh(new T.BoxGeometry(112, 0.8, 80), 0xcbb88a, 0, -0.45, 0);
  mesh(new T.BoxGeometry(112, 0.06, 20), 0x9f9470, 0, 0, 27);
  mesh(new T.BoxGeometry(112, 0.04, 45), 0x438d9f, 0, 0.02, 59.5);
  for (let i = 0; i < 90; i++) {
    let z = 20 + Math.random() * 12,
      x = Math.random() * 108 - 54;
    let weed = mesh(new T.IcosahedronGeometry(0.15 + Math.random() * 0.3, 0), 0x596141, x, 0.06, z);
    weed.scale.set(2, 0.25, 1);
    weed.rotation.y = Math.random() * 6;
  }
  for (let i = 0; i < 65; i++) {
    let x = Math.random() * 104 - 52,
      z = Math.random() * 68 - 36;
    let m = mesh(
      new T.DodecahedronGeometry(0.3 + Math.random() * 0.5),
      i % 3 ? 0x72786c : 0x575c39,
      x,
      0.15,
      z,
    );
    m.scale.y = 0.35;
  }
  const waves = [];
  for (let i = 0; i < 12; i++) {
    let wave = mesh(new T.BoxGeometry(110, 0.015, 0.08), 0x96bac0, 0, 0.06, 39 + i * 3);
    waves.push(wave);
  }
  // Ground truth objects stay separate from the drone’s detected tracks.
  let traps = [],
    tracks = [],
    trapGroup = new T.Group();
  scene.add(trapGroup);
  let next = 0,
    running = false,
    distance = 0,
    acc = 0;
  let actualHeight = 8,
    phase = 'survey',
    missionTime = 0,
    queue = [],
    target = null,
    hoverTime = 0,
    inspected = 0,
    selectedId = null,
    pendingCrops = [],
    travelPos = null;
  let trail = [],
    surveyed = new Uint8Array(56 * 40),
    surveyCount = 0;
  let path = [],
    lengths = [],
    total = 0,
    laneCount = 0,
    laneSpacing = 0,
    plannedAspect = 0;
  const route = new T.Line(
    new T.BufferGeometry().setFromPoints(path.map((p) => new T.Vector3(p.x, 0.09, p.z))),
    new T.LineBasicMaterial({ color: 0x728d85, transparent: true, opacity: 0.5 }),
  );
  scene.add(route);

  // Simple procedural geometry for the six object classes. Dimensions are in metres.
  const litterTypes = ['Bottle', 'Can', 'Plastic bag', 'Tire', 'Foam box'];
  const dimensions = {
    'Crab trap': 1.5,
    Bottle: 0.34,
    Can: 0.16,
    'Plastic bag': 0.6,
    Tire: 0.75,
    'Foam box': 0.55,
  };
  function confirmed(t) {
    return t.n >= 3 && t.conf >= 0.6;
  }
  function litterModel(p) {
    const g = new T.Group();
    function part(geo, color, x, y, z) {
      const m = new T.Mesh(geo, new T.MeshStandardMaterial({ color, roughness: 0.65 }));
      m.position.set(x, y, z);
      m.castShadow = true;
      m.receiveShadow = true;
      g.add(m);
      return m;
    }
    if (p.type === 'Bottle') {
      const b = new T.Group();
      g.add(b);
      const body = part(new T.CylinderGeometry(0.048, 0.06, 0.23, 12), 0x79b4a1, 0, 0, 0);
      g.remove(body);
      b.add(body);
      const neck = part(new T.CylinderGeometry(0.023, 0.035, 0.065, 12), 0x79b4a1, 0, 0.145, 0);
      g.remove(neck);
      b.add(neck);
      const cap = part(new T.CylinderGeometry(0.026, 0.026, 0.022, 12), 0x236590, 0, 0.185, 0);
      g.remove(cap);
      b.add(cap);
      const label = part(new T.CylinderGeometry(0.061, 0.061, 0.06, 12), 0xdce4d1, 0, -0.025, 0);
      g.remove(label);
      b.add(label);
      b.rotation.z = Math.PI / 2;
      b.position.y = 0.063;
    }
    if (p.type === 'Can') {
      const b = part(new T.CylinderGeometry(0.034, 0.034, 0.12, 12), 0xba483c, 0, 0.035, 0);
      b.rotation.z = Math.PI / 2;
      const rim = part(new T.TorusGeometry(0.033, 0.004, 5, 16), 0xc6c9c2, 0.063, 0.035, 0);
      rim.rotation.y = Math.PI / 2;
    }
    if (p.type === 'Plastic bag') {
      const geo = new T.PlaneGeometry(0.5, 0.65, 7, 8),
        a = geo.attributes.position;
      for (let i = 0; i < a.count; i++)
        a.setZ(i, 0.015 + Math.sin(a.getX(i) * 35) * Math.cos(a.getY(i) * 28) * 0.035);
      geo.computeVertexNormals();
      const bag = part(geo, 0xe0d9c9, 0, 0.06, 0);
      bag.rotation.x = -Math.PI / 2;
      bag.material.side = T.DoubleSide;
      for (let x of [-0.13, 0.13]) {
        const handle = part(new T.TorusGeometry(0.065, 0.015, 5, 14), 0xe0d9c9, x, 0.07, -0.36);
        handle.rotation.x = -Math.PI / 2;
      }
    }
    if (p.type === 'Tire') {
      const tire = part(new T.TorusGeometry(0.25, 0.105, 10, 24), 0x292f2c, 0, 0.11, 0);
      tire.rotation.x = Math.PI / 2;
      for (let i = 0; i < 24; i++) {
        const a = (i / 24) * Math.PI * 2,
          t = part(
            new T.BoxGeometry(0.045, 0.045, 0.11),
            0x41463e,
            Math.cos(a) * 0.342,
            0.105,
            Math.sin(a) * 0.342,
          );
        t.rotation.y = -a;
      }
    }
    if (p.type === 'Foam box') {
      part(new T.BoxGeometry(0.52, 0.08, 0.38), 0xece6cc, 0, 0.04, 0);
      part(new T.BoxGeometry(0.52, 0.17, 0.025), 0xece6cc, 0, 0.13, -0.18);
      part(new T.BoxGeometry(0.52, 0.17, 0.025), 0xece6cc, 0, 0.13, 0.18);
      for (let x of [-0.25, 0.25]) part(new T.BoxGeometry(0.025, 0.17, 0.38), 0xece6cc, x, 0.13, 0);
      const lid = part(new T.BoxGeometry(0.5, 0.025, 0.36), 0xd6d0ba, 0.08, 0.28, -0.08);
      lid.rotation.z = 0.2;
    }
    g.scale.setScalar(p.scale);
    g.position.set(p.x, -p.burial * (p.type === 'Tire' ? 0.15 : 0.07), p.z);
    g.rotation.y = p.rot;
    trapGroup.add(g);
  }

  function trapModel(p, i) {
    if (p.type !== 'Crab trap') {
      litterModel(p);
      return;
    }
    let g = new T.Group(),
      geo = i % 2 ? new T.CylinderGeometry(0.75, 0.75, 0.7, 12) : new T.BoxGeometry(1.5, 0.8, 1.2),
      edges = new T.LineSegments(
        new T.EdgesGeometry(geo),
        new T.LineBasicMaterial({ color: 0x513e28 }),
      );
    edges.position.y = 0.45;
    g.add(edges);
    let grid = new T.LineBasicMaterial({ color: 0x725637 });
    for (let a = -0.6; a <= 0.6; a += 0.15) {
      g.add(
        new T.Line(
          new T.BufferGeometry().setFromPoints([
            new T.Vector3(a, 0.85, -0.55),
            new T.Vector3(a, 0.85, 0.55),
          ]),
          grid,
        ),
      );
      g.add(
        new T.Line(
          new T.BufferGeometry().setFromPoints([
            new T.Vector3(-0.65, 0.85, a),
            new T.Vector3(0.65, 0.85, a),
          ]),
          grid,
        ),
      );
    }
    g.position.set(p.x, -p.burial * 0.5, p.z);
    g.rotation.y = p.rot;
    trapGroup.add(g);
  }
  // A fresh mission clears the drone’s knowledge while preserving the beach layout.
  function reset() {
    phase = 'survey';
    actualHeight = height;
    missionTime = 0;
    queue = [];
    target = null;
    hoverTime = 0;
    inspected = 0;
    selectedId = null;
    pendingCrops = [];
    travelPos = null;
    q('detail').textContent = 'Select an object track or a detected map marker.';
    q('crop').hidden = true;
    running = false;
    distance = 0;
    acc = 0;
    tracks = [];
    trail = [];
    surveyed.fill(0);
    surveyCount = 0;
    q('flight').textContent = 'Start survey';
    q('knownstatus').textContent = '0% observed · 0 detections';
  }
  function rebuild() {
    trapGroup.clear();
    traps.forEach((p) => {
      p.burial = (Number(q('burial').value) / 100) * p.buryFactor;
    });
    traps.forEach(trapModel);
    reset();
  }
  function randomize() {
    traps = Array.from({ length: 30 }, (_, i) => ({
      type: i < 10 ? 'Crab trap' : litterTypes[Math.floor((i - 10) / 4)],
      x: Math.random() * 84 - 42,
      z: Math.random() * 62 - 32,
      rot: Math.random() * Math.PI * 2,
      scale: 0.8 + Math.random() * 0.4,
      burial: 0,
      buryFactor: 0.3 + Math.random() * 0.7,
    }));
    next = 0;
    rebuild();
  }
  // Aircraft, downward camera, and the visual camera-footprint pyramid.
  const rotors = [];
  const drone = new T.Group();
  scene.add(drone);
  let body = new T.Mesh(
    new T.BoxGeometry(1, 0.35, 1),
    new T.MeshStandardMaterial({ color: 0xeee9db }),
  );
  drone.add(body);
  for (let x of [-1, 1])
    for (let z of [-1, 1]) {
      let arm = new T.Mesh(
        new T.BoxGeometry(2.8, 0.1, 0.13),
        new T.MeshStandardMaterial({ color: 0x383f40 }),
      );
      arm.rotation.y = (x * z * Math.PI) / 4;
      drone.add(arm);
      let rotor = new T.Mesh(
        new T.CylinderGeometry(0.55, 0.55, 0.025, 24),
        new T.MeshStandardMaterial({ color: 0x30393e, transparent: true, opacity: 0.65 }),
      );
      rotor.position.set(x, 0.2, z);
      drone.add(rotor);
      const blade = new T.Mesh(
        new T.BoxGeometry(1.1, 0.03, 0.12),
        new T.MeshStandardMaterial({ color: 0xc0c8c6 }),
      );
      blade.position.copy(rotor.position);
      drone.add(blade);
      rotors.push(blade);
    }
  const overview = new T.OrthographicCamera(-40, 40, 40, -40, 0.1, 400);
  const cameraHousing = new T.Mesh(
    new T.BoxGeometry(0.55, 0.4, 0.55),
    new T.MeshStandardMaterial({ color: 0x383f40 }),
  );
  cameraHousing.position.y = -0.38;
  drone.add(cameraHousing);
  const lens = new T.Mesh(
    new T.CylinderGeometry(0.18, 0.18, 0.14, 16),
    new T.MeshStandardMaterial({ color: 0x438d9f, metalness: 0.5, roughness: 0.2 }),
  );
  lens.position.y = -0.65;
  drone.add(lens);
  const scanGroup = new T.Group();
  scene.add(scanGroup);
  const scanColor = new T.Color(col('--viz-series-1', '#287c73'));
  const scanGeo = new T.BufferGeometry();
  scanGeo.setAttribute('position', new T.BufferAttribute(new Float32Array(36), 3));
  const scanSides = new T.Mesh(
    scanGeo,
    new T.MeshBasicMaterial({
      color: scanColor,
      transparent: true,
      opacity: 0.09,
      side: T.DoubleSide,
      depthWrite: false,
    }),
  );
  scanGroup.add(scanSides);
  const scanLineGeo = new T.BufferGeometry();
  scanLineGeo.setAttribute('position', new T.BufferAttribute(new Float32Array(48), 3));
  scanGroup.add(
    new T.LineSegments(
      scanLineGeo,
      new T.LineBasicMaterial({ color: scanColor, transparent: true, opacity: 0.8 }),
    ),
  );
  const footprint = new T.Mesh(
    new T.PlaneGeometry(1, 1),
    new T.MeshBasicMaterial({
      color: scanColor,
      transparent: true,
      opacity: 0.15,
      side: T.DoubleSide,
      depthWrite: false,
    }),
  );
  footprint.rotation.x = -Math.PI / 2;
  scanGroup.add(footprint);
  function updateScan(p) {
    let hz = actualHeight * Math.tan(T.MathUtils.degToRad(camera.fov / 2)),
      hx = hz * camera.aspect,
      apex = new T.Vector3(p.x, actualHeight - 0.65, p.z),
      corners = [
        new T.Vector3(p.x - hx, 0.12, p.z - hz),
        new T.Vector3(p.x + hx, 0.12, p.z - hz),
        new T.Vector3(p.x + hx, 0.12, p.z + hz),
        new T.Vector3(p.x - hx, 0.12, p.z + hz),
      ],
      tri = [],
      lines = [];
    for (let i = 0; i < 4; i++) {
      let a = corners[i],
        b = corners[(i + 1) % 4];
      tri.push(...apex.toArray(), ...a.toArray(), ...b.toArray());
      lines.push(...apex.toArray(), ...a.toArray(), ...a.toArray(), ...b.toArray());
    }
    scanGeo.attributes.position.array.set(tri);
    scanGeo.attributes.position.needsUpdate = true;
    scanGeo.computeBoundingSphere();
    scanLineGeo.attributes.position.array.set(lines);
    scanLineGeo.attributes.position.needsUpdate = true;
    scanLineGeo.computeBoundingSphere();
    footprint.position.set(p.x, 0.11, p.z);
    footprint.scale.set(hx * 2, hz * 2, 1);
    overview.position.set(p.x + 90, actualHeight * 0.35 + 90, p.z + 90);
    overview.lookAt(p.x, actualHeight * 0.35, p.z);
  }
  const camera = new T.PerspectiveCamera(65, 1, 0.1, 200);
  camera.up.set(0, 0, -1);
  let renderers = [];
  try {
    for (let id of ['world', 'pov']) {
      let r = new T.WebGLRenderer({ antialias: true });
      r.setPixelRatio(Math.min(devicePixelRatio, 2));
      r.shadowMap.enabled = true;
      r.shadowMap.type = T.PCFSoftShadowMap;
      r.toneMapping = T.ACESFilmicToneMapping;
      q(id).prepend(r.domElement);
      renderers.push(r);
    }
  } catch (e) {
    q('telemetry').textContent = 'WebGL is unavailable on this device.';
    return;
  }
  const box = q('boxes'),
    ctx = box.getContext('2d'),
    map = q('map'),
    mc = map.getContext('2d');
  let height = 8;
  const knowledge = q('knowledge'),
    kc = knowledge.getContext('2d');
  knowledge.style.width = '100%';
  knowledge.style.height = '230px';
  knowledge.style.display = 'block';
  // Mark only cells actually inside the camera footprint and append the flown trail.
  function recordSurvey(p = drone.position) {
    let lastPoint = trail[trail.length - 1];
    if (!lastPoint || Math.hypot(lastPoint.x - p.x, lastPoint.z - p.z) > 0.5)
      trail.push({ x: p.x, z: p.z });
    let hz = actualHeight * Math.tan(T.MathUtils.degToRad(camera.fov / 2)) * 0.94,
      hx = hz * camera.aspect;
    for (let row = 0; row < 40; row++) {
      let z = -39 + row * 2;
      if (Math.abs(z - p.z) > hz) continue;
      for (let column = 0; column < 56; column++) {
        let x = -55 + column * 2,
          idx = row * 56 + column;
        if (Math.abs(x - p.x) <= hx && !surveyed[idx]) {
          surveyed[idx] = 1;
          surveyCount++;
        }
      }
    }
  }
  // This view uses observations only, never unseen ground-truth object locations.

  // Cage markers identify fishing gear independently of confirmation state.
  function cageMarker(context, x, y, size) {
    context.rect(x - size, y - size, size * 2, size * 2);
    for (const fraction of [-1 / 3, 1 / 3]) {
      context.moveTo(x + size * fraction, y - size);
      context.lineTo(x + size * fraction, y + size);
      context.moveTo(x - size, y + size * fraction);
      context.lineTo(x + size, y + size * fraction);
    }
  }
  function trackRows(items, emptyMessage) {
    return items.length
      ? items
          .slice()
          .sort((a, b) => a.id - b.id)
          .map(
            (t) =>
              `<tr aria-selected="${selectedId === t.id}"><td><button class="btn" data-track="${t.id}">${t.type === 'Crab trap' ? '▦ ' : ''}O${t.id + 1} · ${t.type}</button></td><td>${confirmed(t) ? 'Confirmed' : 'Candidate'}</td><td>${Math.round(t.conf * 100)}%</td><td>${t.x.toFixed(1)} / ${(-t.z).toFixed(1)}</td><td>±${t.sigma.toFixed(2)}</td><td>${t.n}</td></tr>`,
          )
          .join('')
      : '<tr><td colspan="6">' + emptyMessage + '</td></tr>';
  }

  function knowledgeDraw() {
    let w = knowledge.width,
      h = knowledge.height,
      dpr = devicePixelRatio,
      c = getComputedStyle(root),
      fg = c.getPropertyValue('--foreground').trim() || '#333',
      border = c.getPropertyValue('--border').trim() || '#888',
      active = c.getPropertyValue('--viz-series-1').trim() || '#287c73';
    const xy = (x, z) => [((x + 56) / 112) * w, ((z + 40) / 80) * h];
    kc.clearRect(0, 0, w, h);
    kc.strokeStyle = border;
    kc.lineWidth = dpr;
    kc.strokeRect(dpr / 2, dpr / 2, w - dpr, h - dpr);
    kc.fillStyle = active;
    kc.globalAlpha = 0.18;
    surveyed.forEach((seen, i) => {
      if (seen)
        kc.fillRect(((i % 56) * w) / 56, (Math.floor(i / 56) * h) / 40, w / 56 + 0.5, h / 40 + 0.5);
    });
    kc.globalAlpha = 1;
    kc.strokeStyle = fg;
    kc.lineWidth = 1.5 * dpr;
    kc.beginPath();
    trail.forEach((p, i) => {
      let a = xy(p.x, p.z);
      i ? kc.lineTo(...a) : kc.moveTo(...a);
    });
    if (trail.length) kc.lineTo(...xy(drone.position.x, drone.position.z));
    kc.stroke();
    kc.font = 12 * dpr + 'px sans-serif';
    tracks.forEach((t) => {
      let a = xy(t.x, t.z),
        s = 4 * dpr;
      kc.strokeStyle = t.type === 'Crab trap' ? '#e7bd72' : active;
      kc.setLineDash(confirmed(t) ? [] : [2 * dpr, 2 * dpr]);
      kc.beginPath();
      if (t.type === 'Crab trap') cageMarker(kc, a[0], a[1], 6 * dpr);
      else if (confirmed(t)) kc.rect(a[0] - s, a[1] - s, 2 * s, 2 * s);
      else kc.arc(...a, s, 0, Math.PI * 2);
      kc.stroke();
      kc.setLineDash([]);
      kc.fillStyle = fg;
      kc.fillText(
        'O' + (t.id + 1),
        Math.min(w - 28 * dpr, a[0] + 6 * dpr),
        Math.max(14 * dpr, a[1] - 6 * dpr),
      );
    });
    let a = xy(drone.position.x, drone.position.z);
    kc.fillStyle = fg;
    kc.beginPath();
    kc.moveTo(a[0], a[1] - 7 * dpr);
    kc.lineTo(a[0] - 5 * dpr, a[1] + 5 * dpr);
    kc.lineTo(a[0] + 5 * dpr, a[1] + 5 * dpr);
    kc.fill();
  }
  function resize() {
    renderers.forEach((r, i) => {
      let el = q(i ? 'pov' : 'world');
      r.setSize(el.clientWidth, el.clientHeight);
      let aspect = el.clientWidth / el.clientHeight,
        c = i ? camera : overview;
      if (i) c.aspect = aspect;
      else {
        c.left = -32 * aspect;
        c.right = 32 * aspect;
        c.top = 32;
        c.bottom = -32;
      }
      c.updateProjectionMatrix();
    });
    box.width = q('pov').clientWidth;
    box.height = q('pov').clientHeight;
    map.width = map.clientWidth * devicePixelRatio;
    map.height = 230 * devicePixelRatio;
    knowledge.width = knowledge.clientWidth * devicePixelRatio;
    knowledge.height = 230 * devicePixelRatio;
    if (Math.abs(plannedAspect - camera.aspect) > 0.001) planRoute();
  }

  // Plan overlapping survey lanes from the altitude and camera field of view.
  function planRoute() {
    const hz = height * Math.tan(T.MathUtils.degToRad(camera.fov / 2)) * 0.94,
      hx = hz * camera.aspect;
    const xmin = -56 + Math.min(hx * 0.7, 55),
      xmax = 56 - Math.min(hx * 0.7, 55),
      zmin = -40 + Math.min(hz * 0.7, 39),
      zmax = 40 - Math.min(hz * 0.7, 39);
    laneCount = Math.max(2, Math.ceil((zmax - zmin) / (2 * hz * 0.7)) + 1);
    laneSpacing = (zmax - zmin) / (laneCount - 1);
    path = [];
    for (let i = 0; i < laneCount; i++) {
      const z = zmin + i * laneSpacing;
      path.push(new T.Vector3(i % 2 ? xmax : xmin, 0, z), new T.Vector3(i % 2 ? xmin : xmax, 0, z));
    }
    lengths = [];
    total = path.slice(1).reduce((sum, p, i) => {
      let d = p.distanceTo(path[i]);
      lengths.push(d);
      return sum + d;
    }, 0);
    route.geometry.dispose();
    route.geometry = new T.BufferGeometry().setFromPoints(
      path.map((p) => new T.Vector3(p.x, 0.09, p.z)),
    );
    plannedAspect = camera.aspect;
    reset();
    updateRouteInfo();
  }
  function updateRouteInfo() {
    q('routeinfo').textContent =
      laneCount +
      ' lanes · ' +
      laneSpacing.toFixed(1) +
      ' m spacing · ≥30% side overlap · ' +
      total.toFixed(0) +
      ' m route · ' +
      (total / speedValue('speed') / 60).toFixed(1) +
      ' min · 112 × 80 m beach · altitude changes restart survey';
  }

  new ResizeObserver(resize).observe(root);
  function position(at = distance) {
    let d = at;
    for (let i = 0; i < lengths.length; i++) {
      if (d <= lengths[i]) return path[i].clone().lerp(path[i + 1], d / lengths[i]);
      d -= lengths[i];
    }
    return path[path.length - 1].clone();
  }
  function visible(p) {
    let v = new T.Vector3(p.x, 0.45, p.z).project(camera);
    return v.z < 1 && v.z > -1 && Math.abs(v.x) < 0.94 && Math.abs(v.y) < 0.94;
  }
  // Heuristic sensing: visibility, object size, burial, glare, and motion affect detection.
  // Fuse position observations using inverse-variance weights; keep the strongest class evidence.
  function observe() {
    pendingCrops = [];
    traps.forEach((p, i) => {
      if (!visible(p)) return;
      const pixels =
          (1080 * (dimensions[p.type] * p.scale)) /
          (2 * actualHeight * Math.tan(T.MathUtils.degToRad(camera.fov / 2))),
        off = Math.hypot(p.x - drone.position.x, p.z - drone.position.z) / actualHeight,
        wind = Number(q('wind').value),
        glare = Number(q('glare').value) / 100,
        sunAngle = Number(q('sun').value),
        wet = p.z > 17 ? 1 : 0.2,
        flightSpeed =
          phase === 'hover' || phase === 'descend' || phase === 'ascend'
            ? 0
            : phase === 'survey'
              ? speedValue('speed')
              : speedValue('inspectSpeed'),
        quality = Math.min(
          0.98,
          Math.max(
            0.05,
            ((pixels / 18) * (1 - p.burial * 0.8) * (1 - glare * wet * 0.5)) /
              (1 + off * 0.35 + flightSpeed * 0.02 + wind * 0.018 + (90 - sunAngle) * 0.0015),
          ),
        );
      if (pixels < 3 || Math.random() > quality) return;
      let t = tracks.find((t) => t.id === i),
        jitter = 0.12 + actualHeight * 0.025 + off * 0.12 + wind * 0.015,
        nx = p.x + (Math.random() - 0.5) * jitter * 2,
        nz = p.z + (Math.random() - 0.5) * jitter * 2;
      if (!t) {
        t = {
          id: i,
          n: 0,
          x: 0,
          z: 0,
          conf: 0,
          type: 'Unknown debris',
          sigma: 2,
          weight: 0,
          history: [],
          lastSeen: 0,
          inspected: false,
        };
        tracks.push(t);
      }
      const w = 1 / (jitter * jitter);
      t.x = (t.x * t.weight + nx * w) / (t.weight + w);
      t.z = (t.z * t.weight + nz * w) / (t.weight + w);
      t.weight += w;
      t.conf = t.n ? Math.max(t.conf, t.conf * 0.7 + quality * 0.3) : quality;
      t.n++;
      t.sigma = Math.max(0.1, Math.sqrt(1 / t.weight));
      if (quality >= 0.6) t.type = p.type;
      t.lastSeen = missionTime;
      t.history.push({ time: missionTime, confidence: t.conf, alt: actualHeight });
      if (t.history.length > 50) t.history.shift();
      pendingCrops.push(i);
    });
  }

  // Operator-only ground-truth map for placement and evaluation.
  function mapDraw() {
    const w = map.width,
      h = map.height;
    mc.clearRect(0, 0, w, h);
    let c = getComputedStyle(root),
      fg = c.getPropertyValue('--foreground').trim() || '#333',
      active = c.getPropertyValue('--viz-series-1').trim() || '#287c73';
    const xy = (x, z) => [((x + 56) / 112) * w, ((z + 40) / 80) * h];
    mc.fillStyle = 'rgba(165,145,95,.18)';
    mc.fillRect(0, 0, w, h);
    mc.fillStyle = 'rgba(60,140,160,.3)';
    mc.fillRect(0, h * 0.94, w, h * 0.06);
    mc.strokeStyle = c.getPropertyValue('--border').trim() || '#888';
    mc.lineWidth = devicePixelRatio;
    mc.beginPath();
    path.forEach((p, i) => {
      let a = xy(p.x, p.z);
      i ? mc.lineTo(...a) : mc.moveTo(...a);
    });
    mc.stroke();
    traps.forEach((p, i) => {
      let t = tracks.find((t) => t.id === i && confirmed(t)),
        a = xy(t ? t.x : p.x, t ? t.z : p.z);
      mc.strokeStyle = p.type === 'Crab trap' ? '#e7bd72' : t ? active : fg;
      mc.beginPath();
      if (p.type === 'Crab trap') cageMarker(mc, a[0], a[1], 6 * devicePixelRatio);
      else if (t)
        mc.rect(
          a[0] - 4 * devicePixelRatio,
          a[1] - 4 * devicePixelRatio,
          8 * devicePixelRatio,
          8 * devicePixelRatio,
        );
      else mc.arc(...a, 3 * devicePixelRatio, 0, Math.PI * 2);
      mc.stroke();
    });
    let a = xy(drone.position.x, drone.position.z);
    mc.fillStyle = fg;
    mc.beginPath();
    mc.moveTo(a[0], a[1] - 7 * devicePixelRatio);
    mc.lineTo(a[0] - 5 * devicePixelRatio, a[1] + 5 * devicePixelRatio);
    mc.lineTo(a[0] + 5 * devicePixelRatio, a[1] + 5 * devicePixelRatio);
    mc.fill();
  }
  function overlay() {
    ctx.clearRect(0, 0, box.width, box.height);
    ctx.strokeStyle = getComputedStyle(root).getPropertyValue('--viz-series-1').trim() || '#287c73';
    ctx.fillStyle = ctx.strokeStyle;
    ctx.font = '12px sans-serif';
    traps.forEach((p, i) => {
      let t = tracks.find((t) => t.id === i);
      if (!t || !visible(p)) return;
      let v = new T.Vector3(p.x, 0.45, p.z).project(camera),
        x = ((v.x + 1) / 2) * box.width,
        y = ((1 - v.y) / 2) * box.height,
        s = Math.max(12, (box.height * 1.8) / actualHeight);
      ctx.strokeRect(x - s / 2, y - s / 2, s, s);
      ctx.fillText(
        'O' + (i + 1) + ' ' + t.type + ' · ' + Math.round(t.conf * 100) + '%',
        Math.max(2, Math.min(x - s / 2, box.width - 120)),
        Math.max(14, y - s / 2 - 4),
      );
    });
  }

  // Queue known, uncertain tracks. Unseen ground-truth objects cannot become targets.
  function beginInspection() {
    queue = tracks
      .filter(
        (t) =>
          !t.inspected &&
          (t.conf < 0.85 || t.sigma > 0.35 || t.n < 5 || t.type === 'Unknown debris'),
      )
      .map((t) => t.id);
    travelPos = drone.position.clone();
    target = null;
    if (!queue.length) {
      phase = 'done';
      running = false;
      q('flight').textContent = 'Restart mission';
      return;
    }
    phase = 'transit';
    running = true;
    q('flight').textContent = 'Pause mission';
    nextTarget();
  }
  // Visit the nearest remaining estimated track, rather than its exact simulator position.
  function nextTarget() {
    if (target) {
      target.inspected = true;
      inspected++;
    }
    if (!queue.length) {
      target = null;
      phase = 'done';
      running = false;
      q('flight').textContent = 'Restart mission';
      return;
    }
    queue.sort((a, b) => {
      const x = tracks.find((t) => t.id === a),
        y = tracks.find((t) => t.id === b);
      return (
        Math.hypot(x.x - travelPos.x, x.z - travelPos.z) -
        Math.hypot(y.x - travelPos.x, y.z - travelPos.z)
      );
    });
    const nextId = queue.shift();
    target = tracks.find((t) => t.id === nextId);
    phase = 'transit';
    hoverTime = 0;
    selectedId = target.id;
  }
  // Inspection state machine: transit → descend → hover for 8 s → ascend → next target.
  function tickInspection(dt) {
    const low = Math.min(3, height * 0.5);
    if (phase === 'transit') {
      actualHeight = Math.min(height, actualHeight + dt * speedValue('ascentSpeed'));
      if (actualHeight < height - 0.01) return travelPos;
      const dest = new T.Vector3(target.x, 0, target.z),
        delta = dest.clone().sub(new T.Vector3(travelPos.x, 0, travelPos.z)),
        step = dt * speedValue('inspectSpeed');
      if (delta.length() <= step) {
        travelPos.copy(dest);
        phase = 'descend';
      } else travelPos.add(delta.normalize().multiplyScalar(step));
    } else if (phase === 'descend') {
      actualHeight = Math.max(low, actualHeight - dt * speedValue('descentSpeed'));
      if (actualHeight <= low + 0.001) phase = 'hover';
    } else if (phase === 'hover') {
      hoverTime += dt;
      if (hoverTime >= 8) {
        phase = 'ascend';
      }
    } else if (phase === 'ascend') {
      actualHeight = Math.min(height, actualHeight + dt * speedValue('ascentSpeed'));
      if (actualHeight >= height - 0.001) nextTarget();
    }
    return travelPos;
  }
  const captureCanvas = document.createElement('canvas');
  captureCanvas.width = 160;
  captureCanvas.height = 160;
  const captureCtx = captureCanvas.getContext('2d');
  // Store a crop of the actual rendered downward-camera image for each sighting.
  function captureSightings() {
    const source = renderers[1].domElement;
    pendingCrops.forEach((id) => {
      const t = tracks.find((t) => t.id === id);
      if (!t) return;
      const v = new T.Vector3(t.x, 0.15, t.z).project(camera),
        size = Math.max(
          60,
          ((source.height * (dimensions[traps[id].type] * traps[id].scale)) /
            (2 * actualHeight * Math.tan(T.MathUtils.degToRad(camera.fov / 2)))) *
            3,
        ),
        x = (v.x + 1) * 0.5 * source.width,
        y = (1 - v.y) * 0.5 * source.height,
        sx = Math.max(0, Math.min(source.width - size, x - size / 2)),
        sy = Math.max(0, Math.min(source.height - size, y - size / 2)),
        s = Math.min(size, source.width, source.height);
      captureCtx.drawImage(source, sx, sy, s, s, 0, 0, 160, 160);
      t.crop = captureCanvas.toDataURL('image/jpeg', 0.75);
    });
    pendingCrops = [];
  }
  // Selected-track inspector: latest crop, position uncertainty, and confidence history.
  function showDetail() {
    const t = tracks.find((t) => t.id === selectedId);
    if (!t) return;
    q('detail').textContent =
      'O' +
      (t.id + 1) +
      ' · ' +
      t.type +
      ' · ' +
      (confirmed(t) ? 'Confirmed' : 'Candidate') +
      ' · ' +
      Math.round(t.conf * 100) +
      '% confidence · ' +
      t.n +
      ' sightings · E ' +
      t.x.toFixed(2) +
      ' / N ' +
      (-t.z).toFixed(2) +
      ' m · ±' +
      t.sigma.toFixed(2) +
      ' m · last seen ' +
      t.lastSeen.toFixed(1) +
      ' s · ' +
      (t.inspected ? 'Inspection finished' : target === t ? 'Inspecting now' : 'Not inspected');
    if (t.type === 'Crab trap')
      q('detail').textContent += ' · FISHING GEAR: active/abandoned status unknown';
    if (t.crop) {
      q('crop').src = t.crop;
      q('crop').hidden = false;
    }
    const c = q('history'),
      g = c.getContext('2d');
    g.clearRect(0, 0, c.width, c.height);
    g.strokeStyle = '#31464e';
    g.beginPath();
    g.moveTo(30, 10);
    g.lineTo(30, 90);
    g.lineTo(410, 90);
    g.stroke();
    g.fillStyle = '#a6bbc1';
    g.font = '12px sans-serif';
    g.fillText('100%', 0, 14);
    g.fillText('0%', 6, 94);
    g.strokeStyle = '#60d0b6';
    g.beginPath();
    t.history.forEach((h, i) => {
      const x = 30 + (i / Math.max(1, t.history.length - 1)) * 370,
        y = 90 - h.confidence * 80;
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    });
    g.stroke();
    q('historylabels').textContent =
      t.history.length +
      ' recent observations · ' +
      (t.history[0]?.time || 0).toFixed(1) +
      '–' +
      t.lastSeen.toFixed(1) +
      ' s · confidence 0–100%';
  }
  for (const tableId of ['tracks', 'traptracks'])
    q(tableId).onclick = (e) => {
      const btn = e.target.closest('button[data-track]');
      if (btn) {
        selectedId = Number(btn.dataset.track);
        showDetail();
      }
    };
  knowledge.onclick = (e) => {
    const r = knowledge.getBoundingClientRect(),
      x = ((e.clientX - r.left) / r.width) * 112 - 56,
      z = ((e.clientY - r.top) / r.height) * 80 - 40;
    let t = tracks
      .slice()
      .sort((a, b) => Math.hypot(a.x - x, a.z - z) - Math.hypot(b.x - x, b.z - z))[0];
    if (t && Math.hypot(t.x - x, t.z - z) < 6) {
      selectedId = t.id;
      showDetail();
    }
  };
  q('inspectnow').onclick = () => {
    if ((phase === 'survey' || phase === 'done') && tracks.length) beginInspection();
  };
  for (const id of ['wind', 'sun', 'glare'])
    q(id).oninput = () => {
      q(id + 'val').textContent = q(id).value + (id === 'wind' ? ' m/s' : id === 'sun' ? '°' : '%');
    };
  q('burial').onchange = () => {
    q('burialval').textContent = q('burial').value + '%';
    rebuild();
  };
  // Cosmetic motion and lighting; the waterline remains fixed.
  function animateEnvironment(now, dt) {
    const seconds = now / 1000,
      wind = Number(q('wind').value);
    waves.forEach((w, i) => {
      w.position.z = 39 + i * 3 + Math.sin(seconds * 0.65 + i * 0.8) * 0.5;
      w.position.y = 0.065 + Math.sin(seconds * 1.2 + i) * 0.008;
      w.material.opacity = 0.5 + 0.25 * Math.sin(seconds * 0.7 + i);
      w.material.transparent = true;
    });
    rotors.forEach((r, i) => (r.rotation.y += dt * (running ? 65 : 5) * (i % 2 ? -1 : 1)));
    drone.rotation.z = Math.sin(seconds * 2) * wind * 0.002;
    drone.rotation.x = Math.cos(seconds * 1.7) * wind * 0.002;
    const angle = T.MathUtils.degToRad(Number(q('sun').value));
    sun.position.set(-Math.cos(angle) * 65, Math.sin(angle) * 65, 25);
    sun.intensity = 1.2 + Math.sin(angle) * 2;
    const wet = scene.children.find(
      (o) => o.isMesh && o.geometry.type === 'BoxGeometry' && o.geometry.parameters.depth === 20,
    );
    if (wet) {
      wet.material.roughness = 1 - Number(q('glare').value) / 140;
      wet.material.metalness = Number(q('glare').value) / 250;
    }
  }

  // Export only observation records and the survey actually conducted.
  q('downloadreport').onclick = () => {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const report = window.buildDronePDF({
      generatedAt: new Date().toLocaleString(),
      timezone,
      phase,
      missionTime,
      surveyComplete: distance >= total,
      coverage: (surveyCount / surveyed.length) * 100,
      surveyed: Array.from(surveyed),
      trail: trail.map((point) => ({ x: point.x, z: point.z })),
      tracks: tracks.map((track) => ({
        id: track.id,
        type: track.type,
        x: track.x,
        z: track.z,
        n: track.n,
        conf: track.conf,
        sigma: track.sigma,
        inspected: track.inspected,
      })),
    });
    report.save('drone-survey-report-' + new Date().toISOString().replace(/[:.]/g, '-') + '.pdf');
  };
  q('simrate').oninput = () => {
    q('simrateval').textContent = Number(q('simrate').value) + '×';
  };
  let last = performance.now(),
    statusTime = 0,
    animationTime = 0;
  // Advance mission logic in simulated seconds. This function does not draw the scene.
  function advanceMission(dt) {
    const wasRunning = running,
      previousDistance = distance,
      wasSurvey = phase === 'survey';
    let p;
    if (running) {
      missionTime += dt;
      acc += dt;
      if (phase === 'survey') {
        distance = Math.min(total, distance + dt * speedValue('speed'));
        p = position();
        actualHeight = height;
      } else p = tickInspection(dt);
    }
    if (!p) p = phase === 'survey' ? position() : travelPos || position();
    drone.position.set(p.x, actualHeight, p.z);
    camera.position.copy(drone.position);
    camera.lookAt(p.x, 0, p.z);
    camera.updateMatrixWorld();
    if (wasRunning) {
      if (wasSurvey) {
        recordSurvey(position(previousDistance));
        for (let at = previousDistance + 1; at < distance; at += 1) recordSurvey(position(at));
      }
      recordSurvey(p);
    }
    if (acc > 0.2) {
      observe();
      acc = 0;
    }
    if (running && wasSurvey && distance >= total) {
      if (q('autoinspect').checked) beginInspection();
      else {
        phase = 'done';
        running = false;
        q('flight').textContent = 'Restart mission';
      }
    }
    return p;
  }
  // Split accelerated time into small steps, then render once per browser frame.
  // Small steps preserve survey coverage and detection cadence at higher simulation rates.
  function frame(now) {
    const elapsed = Math.min(Math.max((now - last) / 1000, 0), 0.1),
      rate = Math.min(5, Math.max(0.25, Number(q('simrate').value) || 1));
    last = now;
    let remaining = elapsed * rate,
      p;
    while (remaining > 0) {
      const dt = Math.min(0.05, remaining);
      p = advanceMission(dt);
      remaining -= dt;
    }
    if (!p) p = phase === 'survey' ? position() : travelPos || position();
    animationTime += elapsed * rate;
    animateEnvironment(animationTime * 1000, elapsed * rate);
    updateScan(p);
    renderers[0].render(scene, overview);
    drone.visible = false;
    scanGroup.visible = false;
    route.visible = false;
    renderers[1].render(scene, camera);
    captureSightings();
    drone.visible = true;
    scanGroup.visible = true;
    route.visible = true;
    overlay();
    mapDraw();
    knowledgeDraw();
    if (now - statusTime > 500) {
      statusTime = now;
      showDetail();
      q('missionstatus').textContent =
        'Phase: ' +
        phase +
        (target
          ? ' · O' +
            (target.id + 1) +
            ' · ' +
            actualHeight.toFixed(1) +
            ' m · hover ' +
            hoverTime.toFixed(1) +
            ' / 8 s'
          : '') +
        ' · ' +
        inspected +
        ' inspected · ' +
        queue.length +
        ' queued · tide fixed';
      q('knownstatus').textContent =
        `${((surveyCount / surveyed.length) * 100).toFixed(1)}% observed · ${tracks.length} detections · ${tracks.filter((t) => confirmed(t)).length} confirmed`;
      let found = tracks.filter((t) => confirmed(t));
      q('telemetry').textContent =
        `${running ? (phase === 'survey' ? 'Surveying' : 'Inspecting') : phase === 'done' ? 'Mission complete' : 'Paused'} · E ${p.x.toFixed(1)} m / N ${(-p.z).toFixed(1)} m · altitude ${actualHeight.toFixed(1)} m · ${Math.round((distance / total) * 100)}% route · ${found.length}/${traps.length} tracked`;
      q('classcounts').textContent = litterTypes
        .map(
          (type) =>
            type +
            ': ' +
            tracks.filter((t) => t.type === type && confirmed(t)).length +
            ' confirmed',
        )
        .join(' · ');
      const crabTracks = tracks.filter((t) => t.type === 'Crab trap');
      const debrisTracks = tracks.filter((t) => t.type !== 'Crab trap');
      q('trapcounts').textContent =
        crabTracks.length +
        ' identified · ' +
        crabTracks.filter(confirmed).length +
        ' confirmed · ' +
        crabTracks.filter((t) => !confirmed(t)).length +
        ' candidates · ' +
        crabTracks.filter((t) => t.inspected).length +
        ' inspected';
      q('traptracks').innerHTML = trackRows(crabTracks, 'No crab traps identified yet');
      q('tracks').innerHTML = trackRows(debrisTracks, 'No litter or unclassified sightings');
    }
    requestAnimationFrame(frame);
  }
  q('flight').onclick = () => {
    if (phase === 'done') reset();
    running = !running;
    q('flight').textContent = running ? 'Pause mission' : 'Resume mission';
  };
  q('random').onclick = randomize;
  q('height').oninput = () => {
    height = Number(q('height').value);
    q('heightval').textContent = height + ' m';
    planRoute();
  };
  q('place').onchange = () => {
    if (q('place').checked) {
      running = false;
      q('flight').textContent = 'Resume survey';
    }
    q('mapcaption').textContent = q('place').checked
      ? `Click map to move O${next + 1}: ${traps[next].type} (cycles through 30 objects)`
      : '▦ cage: crab trap · circles/squares: litter · triangle: drone';
  };
  map.onclick = (e) => {
    if (!q('place').checked) return;
    let r = map.getBoundingClientRect();
    traps[next] = {
      ...traps[next],
      x: Math.max(-46, Math.min(46, ((e.clientX - r.left) / r.width) * 112 - 56)),
      z: Math.max(-32, Math.min(30, ((e.clientY - r.top) / r.height) * 80 - 40)),
      rot: Math.random() * Math.PI,
    };
    next = (next + 1) % traps.length;
    rebuild();
    q('mapcaption').textContent =
      `Click map to move O${next + 1}: ${traps[next].type} (cycles through 30 objects)`;
  };
  q('world').setAttribute('aria-label', 'Isometric drone, beach and camera viewing pyramid');
  q('pov').setAttribute('aria-label', 'Rendered downward camera with simulated object detections');
  randomize();
  resize();
  requestAnimationFrame(frame);
})();
