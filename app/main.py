import json
import math
from pathlib import Path
from typing import List, Optional

import httpx
import shapely
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from shapely.geometry import shape

from .models import (
    Amenity,
    Floor,
    Gallery,
    LocateResponse,
    RouteRequest,
    RouteResponse,
    RouteStep,
)

DATA_PATH = Path(__file__).parent.parent / "data" / "asian_art.json"
LIVINGMAP_BASE = "https://map-api.prod.livingmap.com/v1/maps/the_met"

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
        "Given two gallery numbers, returns a list of steps to walk between them plus "
        "the straight-line distance. If the galleries are on different floors, a "
        '"take a lift or stairs" step is inserted.\n\n'
        "The endpoint also transparently attempts to call Living Map's upstream routing "
        "API — if that succeeds, the full polyline is returned in `upstream` and you can "
        "render a smoother path. If `upstream` is `null`, fall back to the `steps` list."
    ),
    responses={404: {"description": "One or both gallery numbers aren't in the Asian Art range."}},
)
def route(req: RouteRequest):
    start = GALLERIES_BY_NUMBER.get(req.from_gallery)
    end = GALLERIES_BY_NUMBER.get(req.to_gallery)
    if start is None or end is None:
        missing = [n for n, g in [(req.from_gallery, start), (req.to_gallery, end)] if g is None]
        raise HTTPException(404, f"Gallery not in Asian Art range: {missing}")

    steps: List[RouteStep] = [
        RouteStep(
            instruction=f"Start at Gallery {start.number}: {start.name}",
            lat=start.lat, lon=start.lon, floor=start.floor,
        )
    ]
    if start.floor != end.floor:
        steps.append(RouteStep(
            instruction=f"Take a lift or stairs from Floor {start.floor} to Floor {end.floor}",
            floor=end.floor,
        ))
    steps.append(RouteStep(
        instruction=f"Arrive at Gallery {end.number}: {end.name}",
        lat=end.lat, lon=end.lon, floor=end.floor,
    ))

    distance_m = haversine_m(start.lat, start.lon, end.lat, end.lon)

    upstream = None
    try:
        with httpx.Client(timeout=5) as client:
            r = client.post(
                f"{LIVINGMAP_BASE}/route",
                json={"start": {"feature_id": start.id}, "end": {"feature_id": end.id}},
            )
            if r.status_code < 400:
                upstream = r.json()
    except httpx.HTTPError:
        pass

    return RouteResponse(
        from_gallery=start,
        to_gallery=end,
        distance_m=round(distance_m, 1),
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
