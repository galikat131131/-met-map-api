# `/map` — embedded PWA

Mobile-first map overlay served at `/map/` from the same FastAPI deploy. Vanilla JS, no build step, no framework, no service worker. Google Maps JavaScript API for the basemap, our polygon data on top.

See [`PLAN.md`](PLAN.md) for the full design rationale (basemap choice, floor auto-detect heuristic, offline scope, etc.).

## Current status

**Done:**

- **Step 1** — shell + polygon overlay. Floor picker with human labels, Google Maps roadmap at zoom 19, polygons via `map.data.addGeoJson`, red centroid markers, InfoWindow with number / name / image on click.
- **Step 2** — "Where am I?" + floor auto-detect. Locate FAB (bottom-right), parallel `/locate` calls for floors 2 and 3, winner picked by `method: polygon` (or smaller `distance_m`), blue dot + accuracy ring, green highlight on resolved polygon, you-are-here chip, tap-to-correct prompt when `distance_m > 30`.
- **Step 4** — gallery-to-gallery routing. "Route here" button in each InfoWindow → editable From/To bottom sheet → `POST /route` (proxies Living Map's `v2/route`) → dense blue polyline along real corridors + human-readable step list. Cross-floor routes include a "Take the lift from Floor X to Floor Y" step; switching floors redraws the polyline to the active floor's segment.

**Not yet wired up:**

- **Step 3** — nearest amenity with corridor-respecting routes (`/nearest-amenity` → `/locate` on amenity → `POST /route`). Icon row for toilet / lift / restaurant / shop. Deferred.
- **Step 5** — client-side search over the cached `/galleries` array + curated chips (Buddha, jade, tea ceremony, samurai, calligraphy).
- **Step 6** — PWA `manifest.json` + icons so iOS / Android "Add to Home Screen" installs as a standalone app.

## Setup

You need a Google Maps JavaScript API key.

1. Go to https://console.cloud.google.com/google/maps-apis → **Credentials** → **Create credentials** → **API key**.
2. Enable the **Maps JavaScript API** on the project.
3. Restrict the key to HTTP referrers:
   - `localhost/*`
   - `met-asian-art-api.onrender.com/*`
4. Paste your key into `app/static/index.html`, replacing the one in the `<script async defer src="…maps/api/js?key=…">` tag near the bottom of the file.

Run the server:

```sh
pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload
```

Open http://localhost:8000/map/. Geolocation works on localhost without HTTPS, so you can test the locate flow for real.

## Mocking location

Pass `?mock=lat,lng` on the URL to bypass the browser's Geolocation API and inject fake coords. Useful for testing polygon containment when not physically at the Met.

- **Polygon hit** (Gallery 206, Chinese Buddhist Art — turns green, no correction prompt):
  http://localhost:8000/map/?mock=40.77953,-73.96275
- **Corridor / nearest-centroid fallback** (shows "Best guess" chip + yellow correction prompt):
  http://localhost:8000/map/?mock=40.77965,-73.96270

Then tap the blue locate FAB. The mock coord feeds `/locate` for floors 2 and 3 in parallel, same as real GPS.

### Testing routes

After locating yourself somewhere, tap any other gallery polygon → "Route here" in the InfoWindow. The bottom sheet shows distance + steps; the blue polyline draws on the map.

- **Same-floor route** — drop in Gallery 206, route to 216. Expected: ~93 m, 4 steps, polyline follows corridors (not diagonal).
  http://localhost:8000/map/?mock=40.779504915,-73.962624073
- **Cross-floor route** — drop in Gallery 206 (floor 2), switch the floor picker to 3, tap Gallery 253, "Route here". Expected: 126 m · Floors 2 → 3, 6 steps including "Take the lift from Floor 2 to Floor 3". Toggling the floor picker 2↔3 redraws the polyline to the active floor's segment.

## File layout

```
app/
  main.py            StaticFiles mount at /map (one line, see bottom of file)
  static/
    index.html       <base href="/map/"> + Google Maps script + UI shell
    app.js           All client behavior — init map, locate, render floors
    style.css        Mobile-first styles (safe-area-inset aware)
```

## Known issues

- **Gallery 206's polygon renders as a triangle** instead of the actual room shape. Bug in the scraped Living Map data, not the PWA. Fix belongs in `data/asian_art.json` / `scrape.py`.
- **Steps 3, 5, 6 not yet wired up** — see status above.
- **API key is committed to the repo** for hackathon convenience. It's referrer-restricted to `localhost/*` and `met-asian-art-api.onrender.com/*` so the blast radius is small for the hackathon window. Rotate it after.
- **Routing depends on Living Map being reachable.** `/route` proxies `https://map-api.prod.livingmap.com/v2/route`. If upstream is down the response falls back to a minimal Start / (optional lift) / Arrive list with `upstream: null` and straight-line distance — the UI still works, but the polyline won't follow corridors.

## How to modify

| Change | Where |
|---|---|
| Floor picker labels | `#floor-picker` in `index.html` |
| Polygon fill / stroke color | `polygonStyle()` in `app.js` |
| Resolved / route-from / route-to polygon colors | `polygonStyle()` in `app.js` (3-way branch) |
| User marker color / size | `drawUserPosition()` in `app.js` |
| Tap-to-correct distance threshold | `AUTO_CORRECT_THRESHOLD_M` const in `app.js` |
| InfoWindow content | `onPolygonClick()` in `app.js` |
| You-are-here chip text | `renderYouChip()` in `app.js` |
| Route polyline style | `drawRoutePolyline()` in `app.js` |
| Route sheet layout | `#route-sheet` in `index.html` + `.route-*` rules in `style.css` |
| Transition labels ("Take the lift") | `TRANSITION_LABELS` in `app/main.py` |

Adding a new UI element: put DOM in `index.html`, styles in `style.css`, behavior in `app.js`. No framework, no build — save and reload and it's live. Hard-reload (`Cmd+Shift+R`) if the browser cached the old HTML/JS.

Implementing the next step from [`PLAN.md`](PLAN.md): the plan has the spec. Keep the same-origin fetch pattern (`fetch("/galleries")`, `fetch("/locate?...")`) and add state to the module-level `let` block at the top of `app.js`.

## Gotchas baked into the code

- **Same-origin API calls.** `app.js` uses relative paths like `fetch("/galleries")`. Because `<base href="/map/">` only affects relative URLs *without* leading slashes, `/galleries` resolves to the site root. Works both locally and on Render with zero config.
- **Static mount is at `/map` (no trailing slash) in `main.py`.** FastAPI serves `/map/` directly and 307-redirects bare `/map` → `/map/`; the browser follows transparently, and `<base href>` makes all assets resolve correctly regardless.
- **Polygon highlight** is driven by a function-based `map.data.setStyle((feature) => …)`. Mutate `resolvedGalleryNumber` then call `refreshPolygonStyle()` to re-render the fill.
