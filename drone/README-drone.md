# Beach Survey Dashboard

Run `npm run dev` and open http://127.0.0.1:4173.

Edit `dist/index.html` for the dashboard and simulation. Three.js is bundled locally.

Features: altitude-aware coverage planning, isometric view, downward camera, ground-truth placement map, drone knowledge map, and heuristic litter tracks.

## Reading the simulation

The main source is `dist/simulation.js`; `server.cjs` is the local preview server.

- `planRoute` creates overlapping lanes from altitude and camera field of view.
- `recordSurvey` records observed ground and the actual flown path.
- `observe` creates heuristic detections and combines location estimates.
- `beginInspection`, `nextTarget`, and `tickInspection` run the revisit mission.
- `captureSightings` and `showDetail` power the selected-track inspector.
- `advanceMission` updates simulated time; `frame` draws each browser frame.

Distances use metres, flight speeds use metres per second, and mission time uses simulated seconds. `traps` holds simulation ground truth; `tracks` holds the drone's observations. `three.min.js` is the unchanged bundled third-party library.
