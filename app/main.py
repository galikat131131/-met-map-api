import json
import math
import random
import threading
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional

import httpx
import shapely
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from shapely.geometry import shape

from .models import (
    Amenity,
    EdgeCount,
    Floor,
    Gallery,
    GalleryVisitCount,
    LocateResponse,
    QuietRouteResponse,
    QuietRouteStop,
    RouteRequest,
    RouteResponse,
    RouteStep,
    TransitionRequest,
)

DATA_PATH = Path(__file__).parent.parent / "data" / "asian_art.json"
TRANSITIONS_PATH = Path(__file__).parent.parent / "data" / "transitions.jsonl"
ADJACENCY_PATH = Path(__file__).parent.parent / "data" / "adjacency.json"
LIVINGMAP_ROUTE_URL = "https://map-api.prod.livingmap.com/v2/route"

# In-process lock for JSONL appends. Fine for single-worker uvicorn (Render default).
# If we ever scale to multiple workers, switch to fcntl or SQLite. Hackathon-scoped.
_transitions_lock = threading.Lock()
# (session_id, from, to) -> last server_ts (ms). 30s dedupe to absorb GPS flicker
# that slipped past the client's "resolved gallery changed" filter.
_recent_edges: dict = {}
_DEDUPE_WINDOW_MS = 30_000

API_DESCRIPTION = """
A read-only REST API covering the **Metropolitan Museum of Art's Asian Art wing**
(galleries 200–253), built on top of Living Map's Met data. Designed for a PWA
with GPS access.

**Base URL:** `https://met-asian-art-api.onrender.com`
**No auth required. CORS is open (`*`) for hackathon use.**

## Quick start

```js
// "Which gallery am I in?"
const pos = await new Promise((ok, err) =>
  navigator.geolocation.getCurrentPosition(ok, err));
const floor = "2"; // ask the user — GPS can't tell floors
const r = await fetch(
  `https://met-asian-art-api.onrender.com/locate` +
  `?lat=${pos.coords.latitude}&lon=${pos.coords.longitude}&floor=${floor}`
).then(r => r.json());
console.log(r.gallery.name, r.gallery.distance_m, "m away");
```

## Known limitations

- **Floor is never auto-detected.** GPS gives no z-axis indoors; the PWA must
  supply a floor.
- **Indoor GPS is noisy** (20–50 m accuracy in a stone building). `/locate`
  returns the nearest gallery centroid, which is a best-guess, not ground truth.
  Improving this with gallery polygons is on the roadmap.
- **Data snapshot.** Galleries and amenities come from a one-shot scrape of
  Living Map. Refresh by re-running `scrape.py`.
- **Free tier spin-down.** If the API hasn't been hit in 15 min, the first
  request takes ~30 s.

## Tag overview

- **meta** — venue-level info, floors, amenity type counts
- **galleries** — lookup and search
- **spatial** — GPS-driven endpoints (floor required)
- **routing** — wayfinding between galleries
"""

app = FastAPI(
    title="Met Asian Art Map API",
    description=API_DESCRIPTION,
    version="0.1.0",
    contact={"name": "galikat131131", "url": "https://github.com/galikat131131/-met-map-api"},
    openapi_tags=[
        {"name": "meta", "description": "Venue-level metadata: floors, amenity type counts."},
        {"name": "galleries", "description": "Gallery lookup, browse, and search."},
        {"name": "spatial", "description": "GPS-driven endpoints. **Floor must be supplied by the client.**"},
        {"name": "routing", "description": "Multi-floor wayfinding between galleries."},
        {"name": "tracking", "description": "Anonymous route heat-map: record gallery-to-gallery transitions and aggregate counts."},
        {"name": "recommendations", "description": "Route suggestions derived from the heat-map data (e.g. 'show me quiet galleries')."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load():
    with DATA_PATH.open() as f:
        return json.load(f)


DATA = _load()
GALLERIES = [Gallery(**g) for g in DATA["galleries"]]
AMENITIES = [Amenity(**a) for a in DATA["amenities"]]
FLOORS = [Floor(**f) for f in DATA["floors"]]
GALLERIES_BY_NUMBER = {g.number: g for g in GALLERIES}
AMENITY_TYPES_AVAILABLE = sorted({a.type for a in AMENITIES})

# Spatial index for point-in-polygon /locate.
_poly_galleries = [g for g in GALLERIES if g.polygon]
_poly_geoms = [shape(g.polygon) for g in _poly_galleries]
POLYGON_TREE = shapely.STRtree(_poly_geoms) if _poly_geoms else None


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


TRANSITION_LABELS = {
    "lift": "Take the lift",
    "escalator": "Take the escalator",
    "stairs": "Take the stairs",
    "steps": "Go up/down steps",
}


def _steps_from_upstream(upstream: dict, start: Gallery, end: Gallery) -> List[RouteStep]:
    """Flatten Living Map's segments[].routeGeoJson[] into human-readable steps.

    Upstream features fall into three shapes: ones with `directions` text
    ("Head straight"), ones with `transition_mode` (lift / escalator / stairs),
    and structural filler. We emit a step for the first two, tagged with the
    feature's starting coord and floor so the UI can filter by active floor."""
    steps: List[RouteStep] = [RouteStep(
        instruction=f"Start at Gallery {start.number}: {start.name}",
        lat=start.lat, lon=start.lon, floor=start.floor,
    )]
    for segment in upstream.get("segments", []):
        for feature in segment.get("routeGeoJson", []):
            props = feature.get("properties", {}) or {}
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            lat = lon = None
            if coords:
                lon, lat = coords[0][0], coords[0][1]
            floor_num = props.get("floorNumber")
            floor = str(floor_num) if floor_num is not None else None

            text = props.get("directions")
            if text:
                length = props.get("length")
                instruction = f"{text} ({int(length)} m)" if length else text
                steps.append(RouteStep(instruction=instruction, lat=lat, lon=lon, floor=floor))
                continue

            transition = props.get("transition_mode")
            if transition and transition != "steps":
                label = TRANSITION_LABELS.get(transition, transition.replace("_", " ").title())
                if start.floor != end.floor and transition == "lift":
                    label = f"Take the lift from Floor {start.floor} to Floor {end.floor}"
                steps.append(RouteStep(instruction=label, lat=lat, lon=lon, floor=floor))
    steps.append(RouteStep(
        instruction=f"Arrive at Gallery {end.number}: {end.name}",
        lat=end.lat, lon=end.lon, floor=end.floor,
    ))
    return steps


@app.get(
    "/",
    tags=["meta"],
    summary="Service info + counts",
    description="Entry point. Returns service name, venue, dataset counts, and useful links.",
)
def root():
    return {
        "name": "Met Asian Art Map API",
        "venue": DATA["venue"],
        "counts": {
            "galleries": len(GALLERIES),
            "amenities": len(AMENITIES),
            "floors": len(FLOORS),
        },
        "floors_with_galleries": sorted({g.floor for g in GALLERIES}),
        "amenity_types": AMENITY_TYPES_AVAILABLE,
        "links": {
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi_json": "/openapi.json",
            "source": "https://github.com/galikat131131/-met-map-api",
        },
    }


@app.get(
    "/floors",
    response_model=List[Floor],
    tags=["meta"],
    summary="List all floors at the Met",
    description=(
        "Returns all 7 floors in the venue (G, 1, 1M, 2, 3, 4, 5). "
        "**Asian Art galleries only exist on floors 2 and 3** — use `floors_with_galleries` "
        "from `/` if you want just those."
    ),
)
def list_floors():
    return FLOORS


@app.get(
    "/galleries",
    response_model=List[Gallery],
    tags=["galleries"],
    summary="List Asian Art galleries",
    description=(
        "Returns all galleries in the Asian Art wing (200–253). "
        "Filter by floor with `?floor=2` or `?floor=3`. "
        "Pass `?include_closed=false` to hide temporarily closed galleries."
    ),
)
def list_galleries(
    floor: Optional[str] = Query(None, description="Floor short name, e.g. `2` or `3`", examples=["2"]),
    include_closed: bool = Query(True, description="Include galleries marked temporarily closed"),
):
    result = GALLERIES
    if floor is not None:
        result = [g for g in result if g.floor == floor]
    if not include_closed:
        result = [g for g in result if not g.is_closed]
    return result


@app.get(
    "/galleries/{number}",
    response_model=Gallery,
    tags=["galleries"],
    summary="Get one gallery by its Met gallery number",
    description=(
        "Example: `/galleries/207` returns Gallery 207 (Celebrating the Year of the Horse). "
        "Returns 404 if the number is outside 200–253 or not in the dataset."
    ),
    responses={404: {"description": "Gallery not found in Asian Art range (200–253)."}},
)
def get_gallery(number: int):
    g = GALLERIES_BY_NUMBER.get(number)
    if g is None:
        raise HTTPException(404, f"Gallery {number} not found in Asian Art (200–253).")
    return g


@app.get(
    "/search",
    response_model=List[Gallery],
    tags=["galleries"],
    summary="Substring search over gallery name + description",
    description=(
        "Case-insensitive substring match against each gallery's name and description. "
        "Name matches rank ahead of description-only matches. "
        "Example: `/search?q=buddhist` returns galleries 206, 208, 234-236, etc."
    ),
)
def search(
    q: str = Query(..., min_length=1, description="Search term", examples=["buddhist"]),
    limit: int = Query(20, ge=1, le=100),
):
    needle = q.lower().strip()
    scored = []
    for g in GALLERIES:
        hay = f"{g.name} {g.description}".lower()
        if needle in hay:
            score = (0 if needle in g.name.lower() else 1, len(g.name))
            scored.append((score, g))
    scored.sort(key=lambda x: x[0])
    return [g for _, g in scored[:limit]]


@app.get(
    "/nearby",
    response_model=List[Gallery],
    tags=["spatial"],
    summary="Galleries within a radius of a point",
    description=(
        "Returns galleries on the given floor within `radius_m` meters of (lat, lon), "
        "sorted nearest-first with a `distance_m` field populated.\n\n"
        "**Floor is required** because GPS doesn't report floor indoors — the PWA must "
        "let the user pick one."
    ),
)
def nearby(
    lat: float = Query(..., description="Latitude (WGS84)", examples=[40.7796]),
    lon: float = Query(..., description="Longitude (WGS84)", examples=[-73.9633]),
    floor: str = Query(..., description="Floor short name, e.g. `2` or `3`", examples=["2"]),
    radius_m: float = Query(100.0, gt=0, description="Search radius in meters"),
    limit: int = Query(10, ge=1, le=100),
):
    out = []
    for g in GALLERIES:
        if g.floor != floor:
            continue
        d = haversine_m(lat, lon, g.lat, g.lon)
        if d <= radius_m:
            out.append(g.model_copy(update={"distance_m": round(d, 1)}))
    out.sort(key=lambda g: g.distance_m)
    return out[:limit]


@app.get(
    "/locate",
    response_model=LocateResponse,
    tags=["spatial"],
    summary='"Which gallery am I in?" — point-in-polygon on a floor',
    description=(
        "Returns the gallery containing (lat, lon) on the given floor.\n\n"
        "**Method:** tries real point-in-polygon against the gallery's outline first. "
        "If the point lies inside a gallery's polygon, the response has `method: \"polygon\"`. "
        "If the point is outside every polygon (e.g. the user is in a corridor), falls back "
        "to nearest-centroid and returns `method: \"nearest-centroid\"`.\n\n"
        "The PWA should treat `polygon` results as trustworthy and `nearest-centroid` as a "
        "best-guess fallback."
    ),
    responses={404: {"description": "No galleries on that floor (try `2` or `3`)."}},
)
def locate(
    lat: float = Query(..., examples=[40.779808]),
    lon: float = Query(..., examples=[-73.963105]),
    floor: str = Query(..., description="Floor short name (`2` or `3` for Asian Art)", examples=["2"]),
):
    on_floor = [g for g in GALLERIES if g.floor == floor]
    if not on_floor:
        raise HTTPException(404, f"No Asian Art galleries on floor '{floor}'. Try '2' or '3'.")

    if POLYGON_TREE is not None:
        pt = shapely.Point(lon, lat)
        idxs = POLYGON_TREE.query(pt, predicate="within")
        for i in idxs:
            g = _poly_galleries[int(i)]
            if g.floor == floor:
                d = haversine_m(lat, lon, g.lat, g.lon)
                return LocateResponse(
                    gallery=g.model_copy(update={"distance_m": round(d, 1)}),
                    method="polygon",
                )

    best = min(on_floor, key=lambda g: haversine_m(lat, lon, g.lat, g.lon))
    d = haversine_m(lat, lon, best.lat, best.lon)
    return LocateResponse(
        gallery=best.model_copy(update={"distance_m": round(d, 1)}),
        method="nearest-centroid",
    )


@app.get(
    "/nearest-amenity",
    response_model=List[Amenity],
    tags=["spatial"],
    summary="Nearest toilet / water fountain / cafe / lift / etc.",
    description=(
        "Returns the nearest amenities of a given type, ranked by distance. "
        "Omit `floor` for lifts/elevators (they span floors); set it for everything else.\n\n"
        "Available types (dataset is filtered to floors 2 and 3): see `/amenity-types`. "
        "Common values: `toilet`, `drinking_water`, `lift`, `cafe`, `restaurant`, `shop`, "
        "`information`, `defibrillator`."
    ),
    responses={404: {"description": "No amenities of that type exist in the dataset."}},
)
def nearest_amenity(
    lat: float = Query(..., examples=[40.7796]),
    lon: float = Query(..., examples=[-73.9633]),
    type: str = Query(..., description="Amenity type (see `/amenity-types`)", examples=["toilet"]),
    floor: Optional[str] = Query(None, description="Optional floor filter; omit for lifts"),
    limit: int = Query(5, ge=1, le=50),
):
    pool = [a for a in AMENITIES if a.type == type]
    if floor:
        pool = [a for a in pool if a.floor == floor]
    if not pool:
        raise HTTPException(404, f"No amenities of type '{type}' found.")
    ranked = [
        a.model_copy(update={"distance_m": round(haversine_m(lat, lon, a.lat, a.lon), 1)})
        for a in pool
    ]
    ranked.sort(key=lambda a: a.distance_m)
    return ranked[:limit]


@app.post(
    "/route",
    response_model=RouteResponse,
    tags=["routing"],
    summary="Route between two galleries",
    description=(
        "Given two gallery numbers, proxies Living Map's routing engine "
        "(`POST /v2/route`) and reshapes the response for PWA consumers.\n\n"
        "`upstream` is the raw Living Map payload — its `segments[].routeGeoJson` "
        "features contain the dense polyline (use these for rendering a smooth path "
        "on the map). `steps` is a flattened, human-readable direction list.\n\n"
        "If the upstream call fails, a minimal Start/Arrive fallback is returned "
        "with straight-line distance and `upstream: null`."
    ),
    responses={404: {"description": "One or both gallery numbers aren't in the Asian Art range."}},
)
def route(req: RouteRequest):
    start = GALLERIES_BY_NUMBER.get(req.from_gallery)
    end = GALLERIES_BY_NUMBER.get(req.to_gallery)
    if start is None or end is None:
        missing = [n for n, g in [(req.from_gallery, start), (req.to_gallery, end)] if g is None]
        raise HTTPException(404, f"Gallery not in Asian Art range: {missing}")

    upstream = None
    try:
        with httpx.Client(timeout=8) as client:
            r = client.post(
                LIVINGMAP_ROUTE_URL,
                json={
                    "from": {"lmId": start.id},
                    "to": {"lmId": end.id},
                    "project": "the_met",
                },
            )
            if r.status_code < 400:
                upstream = r.json()
    except httpx.HTTPError:
        pass

    if upstream:
        meta = (upstream.get("routeMetadata") or [{}])[0]
        distance_m = round((meta.get("totalLength") or 0) * 1000, 1)
        steps = _steps_from_upstream(upstream, start, end)
    else:
        steps = [RouteStep(
            instruction=f"Start at Gallery {start.number}: {start.name}",
            lat=start.lat, lon=start.lon, floor=start.floor,
        )]
        if start.floor != end.floor:
            steps.append(RouteStep(
                instruction=f"Take a lift or stairs from Floor {start.floor} to Floor {end.floor}",
                floor=end.floor,
            ))
        steps.append(RouteStep(
            instruction=f"Arrive at Gallery {end.number}: {end.name}",
            lat=end.lat, lon=end.lon, floor=end.floor,
        ))
        distance_m = round(haversine_m(start.lat, start.lon, end.lat, end.lon), 1)

    return RouteResponse(
        from_gallery=start,
        to_gallery=end,
        distance_m=distance_m,
        steps=steps,
        upstream=upstream,
    )


@app.get(
    "/amenity-types",
    tags=["meta"],
    summary="Counts of amenities by type",
    description=(
        "Returns a dict of amenity type → count in the dataset. Useful for populating "
        "a filter UI in the PWA."
    ),
)
def amenity_types():
    types = {}
    for a in AMENITIES:
        types[a.type] = types.get(a.type, 0) + 1
    return types


def _append_transition(record: dict) -> None:
    with _transitions_lock:
        TRANSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRANSITIONS_PATH.open("a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _read_transitions() -> list[dict]:
    if not TRANSITIONS_PATH.exists():
        return []
    out = []
    with TRANSITIONS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@app.post(
    "/track/transition",
    tags=["tracking"],
    summary="Record an anonymous gallery-to-gallery transition",
    description=(
        "Appends one transition record to a server-side JSONL file. Consumed by "
        "`/heatmap/*` endpoints to build a popularity heat map of routes.\n\n"
        "**No login; anonymous.** `session_id` is a per-visit UUID generated by the "
        "client (sessionStorage). Not a user identity — scoped to one browser tab.\n\n"
        "**Dedupe:** identical `(session_id, from_gallery, to_gallery)` within a 30s "
        "window is dropped server-side to absorb GPS flicker."
    ),
    responses={
        202: {"description": "Recorded."},
        409: {"description": "Duplicate transition within the dedupe window — dropped."},
    },
    status_code=202,
)
def track_transition(req: TransitionRequest):
    if req.from_gallery == req.to_gallery:
        raise HTTPException(400, "from_gallery and to_gallery must differ.")
    if req.from_gallery not in GALLERIES_BY_NUMBER or req.to_gallery not in GALLERIES_BY_NUMBER:
        raise HTTPException(404, "One or both galleries are not in the Asian Art range.")

    now_ms = int(time.time() * 1000)
    key = (req.session_id, req.from_gallery, req.to_gallery)
    last = _recent_edges.get(key)
    if last is not None and now_ms - last < _DEDUPE_WINDOW_MS:
        raise HTTPException(409, "Duplicate transition within dedupe window.")
    _recent_edges[key] = now_ms

    # Opportunistic cleanup so the dict doesn't grow unbounded.
    if len(_recent_edges) > 10_000:
        cutoff = now_ms - _DEDUPE_WINDOW_MS
        for k, ts in list(_recent_edges.items()):
            if ts < cutoff:
                _recent_edges.pop(k, None)

    record = {
        "session_id": req.session_id,
        "from_gallery": req.from_gallery,
        "to_gallery": req.to_gallery,
        "floor_from": req.floor_from,
        "floor_to": req.floor_to,
        "client_ts": req.client_ts,
        "server_ts": now_ms,
        "locate_method": req.locate_method,
    }
    _append_transition(record)
    return {"ok": True}


@app.get(
    "/heatmap/edges",
    response_model=List[EdgeCount],
    tags=["tracking"],
    summary="Aggregated edge counts (from_gallery, to_gallery) → count",
    description=(
        "Counts all recorded transitions. Directed — `206→216` and `216→206` are "
        "separate edges. Filter with `min_count` to hide noise."
    ),
)
def heatmap_edges(min_count: int = Query(1, ge=1)):
    counts = Counter()
    for t in _read_transitions():
        counts[(t["from_gallery"], t["to_gallery"])] += 1
    return [
        EdgeCount(from_gallery=a, to_gallery=b, count=c)
        for (a, b), c in counts.most_common()
        if c >= min_count
    ]


def _load_adjacency() -> dict[int, list[int]]:
    """Read the adjacency cache on each request. Small file, cheap to parse,
    and this lets `scripts/compute_adjacency.py` rebuild it without a redeploy.

    If the cache only contains raw `probes` (mid-build, before the final
    `pairs` aggregation was written), derive neighbor lists on the fly so the
    endpoint still works. Returns {} only if nothing usable is cached."""
    if not ADJACENCY_PATH.exists():
        return {}
    try:
        raw = json.load(ADJACENCY_PATH.open())
    except json.JSONDecodeError:
        return {}
    pairs = raw.get("pairs") or {}
    if pairs:
        return {int(k): v for k, v in pairs.items()}
    probes = raw.get("probes") or {}
    adj: dict[int, set[int]] = {}
    for entry in probes.values():
        if not entry.get("adjacent"):
            continue
        a, b = entry.get("from"), entry.get("to")
        if a is None or b is None:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return {g: sorted(ns) for g, ns in adj.items()}


def _gallery_visit_counts() -> dict[int, int]:
    counts: Counter = Counter()
    for t in _read_transitions():
        counts[t["to_gallery"]] += 1
    return dict(counts)


def _quiet_walk(
    start: Gallery,
    length: int,
    adjacency: dict[int, list[int]],
    visits: dict[int, int],
    rng: random.Random,
    floor: Optional[str] = None,
) -> list[Gallery]:
    """Weighted random walk on the adjacency graph; neighbor weight ∝
    1/(visits+1)^1.5 so quiet rooms are strongly preferred but heavily-visited
    galleries can still be chosen when they're the only neighbor. We never
    revisit a gallery within a single recommendation — the point is to show
    the user new rooms."""
    path: list[Gallery] = []
    visited: set[int] = {start.number}
    current = start
    for _ in range(length):
        neighbors = adjacency.get(current.number, [])
        pool = []
        for n in neighbors:
            if n in visited:
                continue
            g = GALLERIES_BY_NUMBER.get(n)
            if g is None or g.is_closed:
                continue
            if floor is not None and g.floor != floor:
                continue
            pool.append(g)
        if not pool:
            break
        weights = []
        for g in pool:
            v = visits.get(g.number, 0)
            weights.append(1.0 / ((v + 1) ** 1.5))
        nxt = rng.choices(pool, weights=weights, k=1)[0]
        path.append(nxt)
        visited.add(nxt.number)
        current = nxt
    return path


@app.get(
    "/recommendations/quiet-route",
    response_model=QuietRouteResponse,
    tags=["recommendations"],
    summary="Suggest a walk through under-visited galleries",
    description=(
        "Given a starting gallery, walks the adjacency graph biasing toward "
        "rooms with low recorded visit counts. Intended for visitors who've "
        "seen the highlights and want to explore the quieter corners of the "
        "wing.\n\n"
        "Neighbors are weighted by `1/(visits+1)^1.5`, so zero-visit galleries "
        "dominate when they're reachable. The walk never revisits a gallery "
        "and stops early if the adjacency dead-ends.\n\n"
        "Set `floor` to pin the walk to a single level. Results are stochastic "
        "— refreshing produces a different suggestion."
    ),
    responses={
        404: {"description": "Starting gallery not in Asian Art, or adjacency cache hasn't been built yet."},
    },
)
def recommend_quiet_route(
    from_gallery: int = Query(..., description="Gallery number the visitor is starting from.", examples=[209]),
    length: int = Query(5, ge=1, le=15, description="Number of stops to suggest (excluding the starting gallery)."),
    floor: Optional[str] = Query(None, description="Optional floor filter (`2` or `3`). Omit to allow cross-floor routes."),
):
    start = GALLERIES_BY_NUMBER.get(from_gallery)
    if start is None:
        raise HTTPException(404, f"Gallery {from_gallery} not in Asian Art (200–253).")

    adjacency = _load_adjacency()
    if not adjacency:
        raise HTTPException(
            404,
            "Adjacency cache not built. Run scripts/compute_adjacency.py to enable recommendations.",
        )

    visits = _gallery_visit_counts()
    rng = random.Random()  # fresh randomness per request
    stops_gs = _quiet_walk(start, length, adjacency, visits, rng, floor)

    # Popularity rank: 1 = most-visited. Galleries with zero visits share the
    # last rank so the UI can say "one of the quietest rooms".
    ranked = sorted(GALLERIES, key=lambda g: -visits.get(g.number, 0))
    rank_by_num = {g.number: i + 1 for i, g in enumerate(ranked)}

    stops = [
        QuietRouteStop(
            gallery=g,
            visits=visits.get(g.number, 0),
            popularity_rank=rank_by_num.get(g.number, len(GALLERIES)),
        )
        for g in stops_gs
    ]

    total_distance = 0.0
    prev = start
    for g in stops_gs:
        total_distance += haversine_m(prev.lat, prev.lon, g.lat, g.lon)
        prev = g

    avg_visits_here = (
        sum(s.visits for s in stops) / len(stops) if stops else 0.0
    )
    visited_galleries = [v for v in visits.values() if v > 0]
    baseline = (
        sum(visited_galleries) / len(visited_galleries) if visited_galleries else 0.0
    )

    return QuietRouteResponse(
        from_gallery=start,
        stops=stops,
        total_distance_m=round(total_distance, 1),
        avg_visits_per_stop=round(avg_visits_here, 2),
        baseline_avg_visits=round(baseline, 2),
    )


@app.get(
    "/heatmap/galleries",
    response_model=List[GalleryVisitCount],
    tags=["tracking"],
    summary="Visit counts per gallery (derived from transitions)",
    description=(
        "A gallery is 'visited' every time it appears as the `to_gallery` of a "
        "recorded transition. Sorted descending by visit count."
    ),
)
def heatmap_galleries():
    counts = Counter()
    for t in _read_transitions():
        counts[t["to_gallery"]] += 1
    return [GalleryVisitCount(gallery=g, visits=c) for g, c in counts.most_common()]


app.mount("/map", StaticFiles(directory="app/static", html=True), name="map")
