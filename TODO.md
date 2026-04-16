# Met Map API — TODO

## Done

- **Polygon containment for `/locate`** — shipped. Decodes Living Map vector tiles
  at z=17 (6 tiles covering Met bbox), attaches GeoJSON polygon to every gallery,
  uses `shapely.STRtree` with `predicate="within"` for O(log n) point-in-polygon.
  Falls back to nearest-centroid when the point is outside all polygons (e.g. the
  user is in a corridor). Gallery response now includes `polygon` field as
  GeoJSON Polygon/MultiPolygon for map rendering.

## Future

### Heat-map of user routes (v1 shipped on `feat/heatmap-tracking`)

v1 records anonymous gallery-to-gallery transitions via `POST /track/transition`,
exposes raw counts via `GET /heatmap/edges` and `GET /heatmap/galleries`, and
stores data in `data/transitions.jsonl`. Opt-in banner, session-scoped UUID in
sessionStorage. Remaining work:

- **GPS smoothing / transition hysteresis.** Current logic emits a transition
  every time the resolved gallery differs from the previous. Indoor GPS is ±20–50m
  (per CONSUMERS.md) so a user standing still at a polygon boundary will produce
  a 206→208→206→208 sawtooth. Mitigations:
  - Require N consecutive `/locate` hits in the new gallery before committing
    (hysteresis).
  - Weight `method: "polygon"` over `method: "nearest-centroid"` server-side
    when aggregating.
  - Kalman filter or moving average on (lat, lon) client-side before `/locate`.
  - Minimum dwell time (drop transitions faster than ~10s apart).
  - Once the gallery adjacency graph (below) exists, reject transitions between
    non-adjacent galleries.
- **Heat overlay UI.** Toggle button; fetch `/heatmap/edges` + `/heatmap/galleries`,
  draw weighted arcs between centroids and modulate polygon fill opacity by
  visit-count percentile.
- **Route recommendations.** `GET /recommendations/next?from_gallery=` (top-K
  common next galleries). `GET /recommendations/explore?from=&to=` (weighted
  shortest path with edge weights `1/(1+visits)` so it prefers under-visited
  galleries). The `/explore` endpoint needs the gallery adjacency graph below.
- **Move storage off ephemeral JSONL.** Render free tier wipes the filesystem on
  cold start — fine for a hackathon, bad for real users. Candidates: SQLite +
  Fly volume, or Postgres (Supabase free tier).
- **Re-seed tracking on user tap-correct.** Currently `lastTrackedGalleryNumber`
  only updates from GPS fixes; if the user tap-corrects from 208→210, the next
  GPS fix (if it now correctly lands on 210) will emit a spurious 208→210
  transition.

### Other

- **Polygon containment for amenities** — same approach would let us answer
  "am I right next to the elevator?"
- **Proper fuzzy search** — substring is enough for a hackathon but `rapidfuzz`
  would rank typo-tolerant matches better.
- **Rate limiting** — `slowapi` if the API gets real public traffic.
- **Cache `/route` proxy responses** — same start/end rarely change.
- **Gallery adjacency graph** — derive from polygon edge sharing so a "tour next
  gallery" button can walk the wing in a sensible order.
- **Floor inference from barometer** — if the PWA exposes relative altitude,
  we could guess floor instead of asking the user.
- **Expand to other departments** — Islamic, Near Eastern, European painting,
  etc. Change the gallery-number filter in `scrape.py`.
