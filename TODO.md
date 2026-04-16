# Met Map API — TODO

## Polygon containment for `/locate`

Current `/locate` returns the **nearest gallery centroid** on a given floor.
This is approximate — a user standing in the hallway outside Gallery 210 may
get Gallery 209 if its centroid is closer. For a real PWA, decode Living Map's
vector tiles and do point-in-polygon.

### How

1. Vector tile URL: `https://prod.cdn.livingmap.com/tiles/the_met/{z}/{x}/{y}.pbf?lang=en-GB`
   (min/max zoom 0–19; zoom 18 or 19 for gallery-level detail)
2. Find the tile covering The Met's bbox (around lat 40.7795, lon -73.9635) at
   target zoom: `(z, x, y)` via standard slippy-map math.
3. Decode with `mapbox-vector-tile` (Python) — each tile has layers
   (galleries, amenities, circulation). Extract polygon features.
4. Convert tile-local coordinates to lat/lon using tile bounds.
5. Build a `shapely.STRtree` for O(log n) point-in-polygon.
6. Replace `nearest_centroid_on_floor()` with `polygon_contains(point, floor)`.

### Dependencies to add

```
mapbox-vector-tile
shapely
mercantile
```

### Fallback behavior

If the point is outside all polygons (user is in a hallway or outdoors), fall
back to nearest centroid so the API always returns something.

## Other deferred items

- Proper fuzzy search (rapidfuzz) instead of substring match
- Rate limiting (slowapi) if the API gets public traffic
- Cache `/route` proxy responses (same start/end rarely change)
- Floor inference from barometer if the PWA exposes it
