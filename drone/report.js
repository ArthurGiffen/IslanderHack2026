// Build an offline, printable report from the drone's knowledge snapshot only.
// No simulator object list, hidden locations, or missed-object counts are accepted.
window.buildDroneReport = function buildDroneReport(snapshot) {
  const escape = (value) =>
    String(value).replace(
      /[&<>"']/g,
      (character) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character],
    );
  const confirmed = (track) => track.n >= 3 && track.conf >= 0.6;
  const tracks = snapshot.tracks.slice().sort((a, b) => a.id - b.id);
  const crabTraps = tracks.filter((track) => track.type === 'Crab trap');
  const otherObjects = tracks.filter((track) => track.type !== 'Crab trap');
  const rows = (items) =>
    items.length
      ? items
          .map(
            (track) => `<tr>
    <td>O${track.id + 1}</td><td>${escape(track.type)}</td>
    <td>${confirmed(track) ? 'Confirmed' : 'Not confirmed'}</td>
    <td>${track.x.toFixed(2)}</td><td>${(-track.z).toFixed(2)}</td>
    <td>${Math.round(track.conf * 100)}%</td><td>±${track.sigma.toFixed(2)}</td>
    <td>${track.n}</td><td>${track.inspected ? 'Yes' : 'No'}</td>
  </tr>`,
          )
          .join('')
      : '<tr><td colspan="9">No objects identified in this category.</td></tr>';
  const table = (items) => `<table><thead><tr><th>ID</th><th>Type</th><th>Status</th>
    <th>East (m)</th><th>North (m)</th><th>Confidence*</th><th>Uncertainty (m)</th>
    <th>Sightings</th><th>Inspected</th></tr></thead><tbody>${rows(items)}</tbody></table>`;
  const xy = (x, z) => [((x + 56) / 112) * 672 + 24, ((z + 40) / 80) * 480 + 24];
  const cells = snapshot.surveyed
    .map((seen, index) =>
      seen
        ? `<rect x="${24 + (index % 56) * 12}" y="${24 + Math.floor(index / 56) * 12}" width="12" height="12" fill="#d4e6df"/>`
        : '',
    )
    .join('');
  const stride = Math.max(1, Math.ceil(snapshot.trail.length / 1200));
  const route = snapshot.trail
    .filter((_, index) => index % stride === 0 || index === snapshot.trail.length - 1)
    .map((point) =>
      xy(point.x, point.z)
        .map((value) => value.toFixed(1))
        .join(','),
    )
    .join(' ');
  const markers = tracks
    .map((track) => {
      const [x, y] = xy(track.x, track.z);
      const shape =
        track.type === 'Crab trap'
          ? `<rect x="${x - 5}" y="${y - 5}" width="10" height="10"/><path d="M${x - 5} ${y}H${x + 5} M${x} ${y - 5}V${y + 5}"/>`
          : `<circle cx="${x}" cy="${y}" r="4"/>`;
      return `<g fill="none" stroke="${track.type === 'Crab trap' ? '#90601b' : '#24645c'}" ${confirmed(track) ? '' : 'stroke-dasharray="2 2"'}>${shape}</g>
      <text x="${Math.min(673, x + 7)}" y="${Math.max(36, y - 6)}" font-size="11">O${track.id + 1}</text>`;
    })
    .join('');
  const uncertainties = [];
  if (!snapshot.surveyComplete)
    uncertainties.push('The planned survey was incomplete when this report was downloaded.');
  if (snapshot.coverage < 100)
    uncertainties.push(
      `${(100 - snapshot.coverage).toFixed(1)}% of the survey area remains unobserved.`,
    );
  if (snapshot.phase !== 'done')
    uncertainties.push(
      'The mission is still in progress or paused; further sightings may change these results.',
    );
  tracks.forEach((track) => {
    const reasons = [];
    if (!confirmed(track))
      reasons.push(
        `not confirmed (${track.n} sightings, ${Math.round(track.conf * 100)}% confidence)`,
      );
    if (track.type === 'Unknown debris') reasons.push('object type unresolved');
    if (track.sigma > 0.35)
      reasons.push(`location uncertainty remains ±${track.sigma.toFixed(2)} m`);
    if (!track.inspected && (track.conf < 0.85 || track.sigma > 0.35 || track.n < 5))
      reasons.push('lower-altitude inspection not completed');
    if (reasons.length)
      uncertainties.push(`O${track.id + 1} — ${track.type}: ${reasons.join('; ')}.`);
  });
  if (!tracks.length)
    uncertainties.push(
      'No objects have been observed. This does not establish that the beach is clear.',
    );
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Drone survey report</title><style>
    body{font:14px/1.5 system-ui,sans-serif;color:#172d32;background:#fff;max-width:1050px;margin:32px auto;padding:0 24px}
    h1{font-size:28px}h2{font-size:20px;margin-top:28px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px;border-bottom:1px solid #ccd8d8;text-align:left}th{background:#eef3f3}
    .trap{border-left:4px solid #90601b;padding-left:16px}.small{font-size:12px;color:#42585c}svg{width:100%;max-width:720px;height:auto}li{margin:8px 0}footer{border-top:1px solid #ccd8d8;margin-top:30px;padding-top:14px}
    @media(max-width:700px){.scroll{overflow-x:auto}}@media print{body{margin:0;max-width:none}tr{break-inside:avoid}h2{break-after:avoid}.scroll{overflow:visible}}
    </style></head><body><h1>Drone knowledge & survey report</h1>
    <p>Generated ${escape(snapshot.generatedAt)} · ${escape(snapshot.timezone)}</p>
    <p><strong>${snapshot.surveyComplete ? 'Planned survey completed' : 'Partial survey'}</strong> · ${snapshot.coverage.toFixed(1)}% ground observed · ${snapshot.missionTime.toFixed(1)} simulated seconds · ${escape(snapshot.phase)} phase</p>
    <p class="small">Snapshot of the drone's observations at download time. Coordinates are relative to the beach centre: positive east/right and north/up; negative values mean west and south. These are not GPS coordinates. Coverage uses 2 m cells in the 112 × 80 m survey area.</p>
    <section class="trap"><h2>▦ Crab traps — priority findings</h2><p>${crabTraps.length} identified · ${crabTraps.filter(confirmed).length} confirmed · ${crabTraps.filter((track) => !confirmed(track)).length} not confirmed</p><div class="scroll">${table(crabTraps)}</div></section>
    <h2>Survey conducted & relative locations</h2><svg viewBox="0 0 720 540" role="img" aria-label="Observed ground, actual flown path and detected objects"><rect x="24" y="24" width="672" height="480" fill="#f4f5f4"/>${cells}<polyline points="${route}" fill="none" stroke="#42585c" stroke-width="1.5"/>${markers}<rect x="24" y="24" width="672" height="480" fill="none" stroke="#809496"/><text x="28" y="18" font-size="12">North ↑ · local beach coordinates</text><text x="24" y="528" font-size="12">East → · shaded: observed ground · line: flown path · blank: unobserved</text></svg>
    <p class="small">Cage: identified crab trap · circle: other observed object · dashed: not confirmed. Only observed objects are shown.</p>
    <h2>Other debris & unresolved types</h2><div class="scroll">${table(otherObjects)}</div>
    <h2>Uncertainties & follow-up</h2><ul>${uncertainties.length ? uncertainties.map((item) => `<li>${escape(item)}</li>`).join('') : '<li>No outstanding track or survey flags under the demo thresholds.</li>'}
    <li>Crab-trap detections do not establish whether fishing gear is active or abandoned.</li>
    <li>*Confidence is heuristic, not a calibrated probability. Confirmation requires at least three sightings and 60% confidence. Location uncertainty is an estimate, not a guaranteed bound.</li>
    <li>Observed coverage does not prove all objects were detected. This report excludes hidden simulator objects and cannot determine how many objects were missed.</li></ul>
    <footer class="small">Coastwatch simulation · downloadable observation report · use your browser's Print command to save as PDF.</footer></body></html>`;
};
