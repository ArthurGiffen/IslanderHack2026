// Vector PDF generation from the drone's observation snapshot, without hidden objects.
window.buildDronePDF = function buildDronePDF(snapshot) {
  const doc = new window.jspdf.jsPDF({ unit: 'mm', format: 'a4' });
  const left = 15,
    width = 180,
    bottom = 278;
  let y = 22;
  const tracks = snapshot.tracks.slice().sort((a, b) => a.id - b.id);
  const isConfirmed = (track) => track.n >= 3 && track.conf >= 0.6;
  const crabTracks = tracks.filter((track) => track.type === 'Crab trap');
  const clean = (text) =>
    String(text)
      .replace(/[–—]/g, '-')
      .replace(/≥/g, '>=')
      .replace(/↑/g, 'up')
      .replace(/→/g, 'right');
  function room(height) {
    if (y + height > bottom) {
      doc.addPage();
      y = 22;
    }
  }
  function text(value, size = 10, bold = false) {
    doc.setFont('helvetica', bold ? 'bold' : 'normal');
    doc.setFontSize(size);
    const lines = doc.splitTextToSize(clean(value), width);
    for (const line of lines) {
      room(size * 0.45 + 2);
      doc.text(line, left, y);
      y += size * 0.45 + 1;
    }
    y += 2;
  }
  function heading(value) {
    room(20);
    y += 4;
    text(value, 14, true);
  }
  function table(items) {
    const columns = [15, 29, 58, 83, 105, 127, 148, 166];
    const labels = [
      'ID',
      'Type',
      'Status',
      'East m',
      'North m',
      'Conf.*',
      '+/- m',
      'Double checked',
    ];
    function header() {
      room(12);
      doc.setFillColor(232, 240, 238);
      doc.rect(left, y - 4, width, 8, 'F');
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(8);
      labels.forEach((label, index) => doc.text(label, columns[index], y));
      y += 8;
    }
    header();
    if (!items.length) {
      text('No objects identified in this category.');
      return;
    }
    for (const track of items) {
      if (y + 10 > bottom) {
        doc.addPage();
        y = 22;
        header();
      }
      const values = [
        'O' + (track.id + 1),
        track.type,
        isConfirmed(track) ? 'Confirmed' : 'Unconfirmed',
        track.x.toFixed(2),
        (-track.z).toFixed(2),
        Math.round(track.conf * 100) + '%',
        track.sigma.toFixed(2),
        track.inspected ? 'Yes' : 'No',
      ];
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8);
      values.forEach((value, index) => doc.text(value, columns[index], y));
      doc.setDrawColor(210, 220, 219);
      doc.line(left, y + 3, left + width, y + 3);
      y += 9;
    }
    y += 3;
  }
  text('Drone knowledge & survey report', 20, true);
  text(snapshot.generatedAt + ' - ' + snapshot.timezone, 9);
  text(
    (snapshot.surveyComplete ? 'Planned survey completed' : 'Partial survey') +
      ' | ' +
      snapshot.coverage.toFixed(1) +
      '% observed | ' +
      snapshot.missionTime.toFixed(1) +
      ' simulated seconds | ' +
      snapshot.phase,
  );
  text(
    'Observation snapshot at download time. East/north coordinates are metres from the beach centre, not GPS. Negative east means west; negative north means south.',
    9,
  );
  heading('Crab traps - priority findings');
  text(
    crabTracks.length +
      ' identified | ' +
      crabTracks.filter(isConfirmed).length +
      ' confirmed | ' +
      crabTracks.filter((track) => !isConfirmed(track)).length +
      ' unconfirmed',
  );
  table(crabTracks);
  room(165);
  heading('Survey conducted & relative locations');
  room(150);
  const mapX = left + 6,
    mapY = y + 5,
    mapW = 168,
    mapH = 120;
  const xy = (x, z) => [mapX + ((x + 56) / 112) * mapW, mapY + ((z + 40) / 80) * mapH];
  doc.setFillColor(244, 246, 245);
  doc.rect(mapX, mapY, mapW, mapH, 'F');
  doc.setFillColor(206, 230, 218);
  snapshot.surveyed.forEach((seen, index) => {
    if (seen) doc.rect(mapX + (index % 56) * 3, mapY + Math.floor(index / 56) * 3, 3, 3, 'F');
  });
  doc.setDrawColor(74, 100, 101);
  doc.setLineWidth(0.25);
  snapshot.trail.forEach((point, index) => {
    if (index)
      doc.line(
        ...xy(snapshot.trail[index - 1].x, snapshot.trail[index - 1].z),
        ...xy(point.x, point.z),
      );
  });
  tracks.forEach((track) => {
    const [x, z] = xy(track.x, track.z);
    const cage = track.type === 'Crab trap';
    doc.setDrawColor(...(cage ? [154, 108, 32] : [30, 94, 89]));
    doc.setLineDashPattern(isConfirmed(track) ? [] : [0.6, 0.5], 0);
    if (cage) {
      doc.rect(x - 1.5, z - 1.5, 3, 3);
      doc.line(x - 1.5, z, x + 1.5, z);
      doc.line(x, z - 1.5, x, z + 1.5);
    } else doc.circle(x, z, 1);
    doc.setLineDashPattern([], 0);
    doc.setFontSize(6);
    doc.text('O' + (track.id + 1), Math.min(mapX + mapW - 6, x + 2), Math.max(mapY + 3, z - 2));
  });
  doc.setDrawColor(100, 120, 120);
  doc.rect(mapX, mapY, mapW, mapH);
  y = mapY + mapH + 8;
  text(
    'North is up, east is right. Shaded: observed ground; blank: unobserved; line: flown path. Cage: identified crab trap; circle: other object; dashed: unconfirmed.',
    9,
  );
  heading('Other debris & unresolved types');
  table(tracks.filter((track) => track.type !== 'Crab trap'));
  const pages = doc.getNumberOfPages();
  for (let page = 1; page <= pages; page++) {
    doc.setPage(page);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(90, 110, 110);
    doc.text('Coastwatch simulation | Drone observations only', 15, 289);
    doc.text(page + ' / ' + pages, 185, 289);
  }
  return doc;
};

