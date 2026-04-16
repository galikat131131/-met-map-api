# Met Map API — TODO

## Done

- **Polygon containment for `/locate`** — shipped. Decodes Living Map vector tiles
  at z=17 (6 tiles covering Met bbox), attaches GeoJSON polygon to every gallery,
  uses `shapely.STRtree` with `predicate="within"` for O(log n) point-in-polygon.
  Falls back to nearest-centroid when the point is outside all polygons (e.g. the
  user is in a corridor). Gallery response now includes `polygon` field as
  GeoJSON Polygon/MultiPolygon for map rendering.

## Future

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
