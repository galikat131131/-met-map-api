# Met Asian Art Map API — Consumer Guide

Everything a PWA developer needs to integrate.

- **Base URL:** `https://met-asian-art-api.onrender.com`
- **Interactive docs:** [/docs (Swagger UI)](https://met-asian-art-api.onrender.com/docs) · [/redoc](https://met-asian-art-api.onrender.com/redoc)
- **OpenAPI JSON:** `/openapi.json`
- **Auth:** none
- **CORS:** `*` (hackathon — will be tightened later)

---

## Endpoint cheat sheet

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service info, counts, links |
| GET | `/floors` | All 7 floors of the Met |
| GET | `/galleries?floor=2` | List Asian Art galleries (optionally filter by floor) |
| GET | `/galleries/{number}` | One gallery by Met gallery number |
| GET | `/search?q=buddhist` | Substring search over name + description |
| GET | `/nearby?lat=&lon=&floor=&radius_m=` | Galleries within a radius |
| GET | `/locate?lat=&lon=&floor=` | Nearest gallery ("which gallery am I in?") |
| GET | `/nearest-amenity?type=&lat=&lon=&floor=` | Nearest toilet/lift/cafe/etc. |
| POST | `/route` | `{from_gallery, to_gallery}` → steps + distance |
| GET | `/amenity-types` | Counts by amenity type |

---

## PWA integration recipes

### 1. "Which gallery am I in?"

```js
const API = "https://met-asian-art-api.onrender.com";

async function whereAmI(floor) {
  const pos = await new Promise((ok, err) =>
    navigator.geolocation.getCurrentPosition(ok, err, {
      enableHighAccuracy: true,
      timeout: 10000,
    }));
  const { latitude: lat, longitude: lon } = pos.coords;
  const res = await fetch(
    `${API}/locate?lat=${lat}&lon=${lon}&floor=${floor}`
  );
  if (!res.ok) throw new Error(await res.text());
  const { gallery } = await res.json();
  return gallery; // { number, name, description, image_url, distance_m, ... }
}
```

### 2. Floor picker

There is no way to detect floor from the browser. Your PWA must show a floor picker (segmented control: "G · 1 · 1M · 2 · 3 · 4 · 5") and remember the user's choice.

```js
const floors = await fetch(`${API}/floors`).then(r => r.json());
// Only floors 2 and 3 contain Asian Art — filter the picker if you want:
const relevant = floors.filter(f => ["2", "3"].includes(f.short_name));
```

### 3. Route from current gallery to a target

```js
async function routeTo(fromNumber, toNumber) {
  const res = await fetch(`${API}/route`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ from_gallery: fromNumber, to_gallery: toNumber }),
  });
  return res.json();
  // {
  //   from_gallery, to_gallery: Gallery,
  //   distance_m: number,                    // total walking distance, meters
  //   steps: [{ instruction, lat?, lon?, floor? }],
  //     //  "Start at Gallery 206: ..."
  //     //  "Head straight (12 m)"
  //     //  "Take the lift from Floor 2 to Floor 3"
  //     //  "Arrive at Gallery 253: ..."
  //   upstream: object | null,               // raw Living Map v2/route response
  // }
}
```

For the on-map polyline, use `upstream.segments[].routeGeoJson[]` — each feature's `geometry.coordinates` is a dense `[lon,lat][]` polyline following real corridors, tagged with `properties.floorNumber` so you can filter to the currently-displayed floor. When `upstream` is null (upstream unreachable), fall back to drawing a line through `steps[].{lat,lon}`.

### 4. Nearest restroom

```js
async function nearestRestroom(lat, lon, floor) {
  const res = await fetch(
    `${API}/nearest-amenity?lat=${lat}&lon=${lon}&type=toilet&floor=${floor}&limit=1`
  );
  const [restroom] = await res.json();
  return restroom; // { name, distance_m, lat, lon, ... }
}
```

### 5. Search as you type

```js
async function search(q) {
  if (!q.trim()) return [];
  const res = await fetch(`${API}/search?q=${encodeURIComponent(q)}&limit=10`);
  return res.json();
}
```

---

## Data shapes

### Gallery

```ts
{
  id: string;              // internal Living Map ID
  number: number;          // Met gallery number, 200–253
  name: string;            // e.g. "Chinese Buddhist Art"
  summary: string;         // e.g. "Gallery 206"
  description: string;     // long-form description, may contain newlines
  lat: number;             // WGS84 latitude
  lon: number;             // WGS84 longitude
  floor: string;           // short name: "2" or "3"
  floor_id: number;        // numeric floor id
  image_url: string | null;// hero image from Met CDN
  is_closed: boolean;      // temporarily closed flag
  distance_m: number | null; // populated only on /nearby and /locate responses
  polygon: GeoJSONPolygon | null; // GeoJSON outline of the gallery for map rendering
}
```

### Amenity

```ts
{
  id: string;
  type: string;            // "toilet" | "lift" | "drinking_water" | "cafe" | ...
  name: string;
  description: string;
  lat: number;
  lon: number;
  floor: string;
  floor_id: number;
  is_closed: boolean;
  distance_m: number | null;
}
```

---

## Gotchas

- **Floor must come from the UI.** Every `/locate`, `/nearby`, and most `/nearest-amenity` calls require `floor=`. GPS will not tell you.
- **Indoor GPS is noisy** (20–50 m error). `/locate` now does real point-in-polygon containment (`method: "polygon"`) when possible, falling back to nearest-centroid (`method: "nearest-centroid"`) when the point lies in a corridor or outside the building. Treat `polygon` results as trustworthy.
- **Free tier cold start.** If the API has been idle for 15 min, the first request takes ~30 s. Run `./keepalive.sh` locally or set up [UptimeRobot](https://uptimerobot.com) with a 5 min HTTP check.
- **`/route` proxies Living Map's `v2/route` engine.** The response is reshaped into a flat `steps[]` list (including cross-floor "Take the lift" instructions) plus the raw `upstream` payload for the dense polyline. If Living Map is unreachable, you still get a minimal `steps[]` (Start → optional lift → Arrive) with `upstream: null` and straight-line distance.
- **Dataset is a snapshot.** Re-run `scrape.py` to refresh it.

---

## Asian Art coverage

54 galleries (numbers 200–253) split across floors 2 and 3:

- **Floor 2** — Chinese ceramics, Chinese Buddhist art, Japanese ceramics, South Asian sculpture, Korean art, Southeast Asian art, Islamic-adjacent galleries.
- **Floor 3** — Chinese Treasury, enamel / decorative arts, jade collection.

31 amenities on those floors: toilets, lifts, drinking fountains, cafes, shops, information desks, defibrillators.

See `/amenity-types` for live counts.
