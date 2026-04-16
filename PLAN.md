# Plan — Google Maps PWA served from the API

Mobile-only PWA. Served from the existing FastAPI deploy at `https://met-asian-art-api.onrender.com/map`. No build step, no separate repo, no framework.

## Answered decisions

- **Basemap**: `roadmap`. Verified against Google Maps at zoom 19 — Google already shows gallery numbers (200, 202, 300–303, etc.) and POI labels (Balcony Lounge, Great Hall Cafe, "Ancient Near Eastern Art"). Our polygons sit on top; no conflict. Do not use `satellite`.
- **Floor auto-detect**: on app load, call `/locate` once per floor (2 and 3). Whichever returns `method: polygon` wins and sets the picker. If neither returns polygon, pick whichever has smaller `distance_m`. This works on every phone (no barometer dependency) because Asian Art galleries on floors 2 and 3 have different XY footprints, so 2D polygon containment disambiguates on its own. User can override via the picker.
- **Route start**: user's current GPS-resolved gallery (from the auto-detect above). Destination set via "Route here" button inside each gallery's InfoWindow. No long-press — rejected as undiscoverable by UX review.
- **"Not in Asian Art"**: if `/locate` returns a gallery with `distance_m > 30`, show a correction prompt "Can't pinpoint you — tap the gallery you're in" rather than asserting the user isn't there. GPS inside the Met is ±30–150m; the banner can't be confidently negative.
- **Offline scope**: descoped. No service worker. Only `manifest.json` + icons for installability. Rationale: a stale SW cache on demo day (wrong JS version, empty `/galleries` array) is a higher risk than the benefit of offline support — and the real cold-start problem is solved by UptimeRobot, not SW caching.
- **Google Maps key**: HTML-embedded, restricted to `met-asian-art-api.onrender.com/*` and `localhost/*`. User generates it; Claude pastes it into `index.html`.
- **Static mount path**: `/map/` with trailing slash. `index.html` sets `<base href="/map/">` so all relative URLs resolve under the prefix. Mounting at `/map` (no slash) causes a 307 redirect and breaks asset loading.

## File layout

```
app/
  main.py                ← mount StaticFiles("/map/", "app/static", html=True)
  static/
    index.html           ← <base href="/map/"> + <link rel="manifest" href="manifest.json">
    app.js
    style.css
    manifest.json
    icons/
      icon-192.png
      icon-512.png
      apple-touch-icon-180.png
```

No `sw.js` — service worker intentionally omitted (see Offline scope above).

FastAPI change: one-line mount. The `/` root JSON endpoint stays where it is.

## Build order

**1. Shell + polygon overlay**
- Google Maps SDK loaded with key in `<script src="...&key=KEY">`
- `mapTypeId: "roadmap"`, center `{40.779448, -73.963517}`, zoom 19
- Segmented control top-left with human labels:
  - "Floor 2 — Chinese, Japanese, Korean"
  - "Floor 3 — Treasury, Jade"
  - (default set by the auto-detect in step 2; before GPS resolves, start on Floor 2)
- Fetch `/galleries` ONCE on load (no floor filter), cache in memory — both floors' data drives the overlay, the search, and the floor auto-detect. Re-render by filtering locally when the picker changes.
- `map.data.addGeoJson` for the selected floor → blue fill, click opens `InfoWindow` with `{number} — {name}` + image + summary
- Red dot at each gallery centroid (`lat`/`lon`) for label-offset visibility

**2. "Where am I?" + floor auto-detect**
- Floating "Locate" button bottom-right (must be user-gesture-triggered for iOS geolocation)
- `navigator.geolocation.getCurrentPosition` → call `/locate?lat=&lon=&floor=2` and `/locate?lat=&lon=&floor=3` in parallel
- Pick the response with `method: polygon`; if neither, pick smaller `distance_m`
- Set floor picker to the winning floor; re-render polygons for that floor
- Blue circle marker + accuracy circle for the user's position
- Highlight containing polygon green; small badge shows `method: polygon` or `nearest-centroid`
- If winning `distance_m > 30`, show prompt "Can't pinpoint you — tap the gallery you're in" (tap-to-correct, not an error)
- Store user's GPS + resolved gallery for route + nearest-amenity flows

**3. Nearest amenity**
- Icon row (toilet first — biggest touch target — then lift / drinking_water / cafe / shop): tap → `GET /nearest-amenity?type=&lat=&lon=&floor=&limit=1`
- Instead of drawing a Euclidean line (leads through walls), resolve the amenity's containing gallery (via `/locate` with its lat/lon) and call `POST /route` from the user's current gallery to that gallery so the path respects corridors
- Drop marker at the amenity, draw routed polyline, InfoWindow shows name + walking `distance_m` from the `/route` response

**4. Route**
- Start = user's resolved gallery from step 2; if unknown, prompt user to tap a starting gallery
- Destination set only via "Route here" button in the gallery InfoWindow (no long-press)
- `POST /route {from_gallery, to_gallery}` → draw polyline from `upstream` if present, else connect `steps` coordinates
- Bottom sheet shows `distance_m` + ordered step instructions
- Editable "From" / "To" chips in the sheet so user can fix a wrong GPS-picked start

**5. Search**
- Client-side filter over the already-loaded `/galleries` array (name + summary + description substring, case-insensitive). No `/search` API call — zero network, zero debounce plumbing.
- Top-right input with curated chips above it: "Buddha", "jade", "tea ceremony", "samurai", "calligraphy" (prefills input on tap)
- Dropdown results show thumbnail (`image_url`) + number + name; tap zooms map + opens that gallery's InfoWindow

**6. Installability (no service worker)**
- `manifest.json` with `name`, `short_name`, `icons` (192, 512), `theme_color`, `display: "standalone"`, `start_url: "/map/"`
- `<link rel="manifest" href="manifest.json">`, `<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">`, `<link rel="apple-touch-icon" sizes="180x180" href="icons/apple-touch-icon-180.png">`, `<meta name="apple-mobile-web-app-capable" content="yes">`
- Verify "Add to Home Screen" works on iOS Safari + Chrome Android. That's it — no SW registration, no offline caching.

## Testing sequence

- Desktop browser first at `http://localhost:8000/map/` (geolocation works on localhost without HTTPS)
- Push to Render, open on phone at `https://met-asian-art-api.onrender.com/map/`
- "Add to Home Screen" from Safari/Chrome to verify install prompt
- In-museum check during the hackathon

## Things Claude does NOT decide without asking

- Swapping Google for Mapbox if user is blocked by Google's CC requirement — ask before reworking.
- Adding any build step (Vite, bundler, TS) — stay vanilla.
- Caching Google Maps tiles in the service worker — against their ToS; would need Mapbox/Leaflet swap.

## Handoff to fresh context

The implementing context should:

1. Read this file and `CONSUMERS.md` (endpoint contracts).
2. Ask user for the Google Maps API key.
3. Build in the order above. Commit after each step. Push and verify on Render between steps.
4. Do not touch existing endpoints in `app/main.py` — only add the static mount.
