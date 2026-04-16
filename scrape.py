"""Scrape Living Map's Met endpoint → filter to Asian Art galleries + amenities → save clean JSON.

Also decodes vector tiles to attach gallery polygon geometry for point-in-polygon /locate.
"""
import json
import re
from pathlib import Path

import httpx
import mapbox_vector_tile
import mercantile
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

BASE = "https://map-api.prod.livingmap.com/v1/maps/the_met"
TILE_BASE = "https://prod.cdn.livingmap.com/tiles/the_met"
LANG = "en-GB"
OUT = Path(__file__).parent / "data" / "asian_art.json"

ASIAN_ART_RANGE = range(200, 254)
AMENITY_SUBCATS = {
    "toilet", "lift", "ramp", "escalator", "drinking_water",
    "cafe", "restaurant", "bar", "shop", "information",
    "cloakroom", "defibrillator", "tickets",
}
TILE_ZOOM = 17


def flatten_lang(field, lang=LANG):
    if not field:
        return ""
    if isinstance(field, list):
        for entry in field:
            if entry.get("lang") == lang:
                t = entry.get("text")
                if isinstance(t, dict):
                    return t.get(lang, "") or ""
                return (t or "").strip()
    return ""


def extract_gallery_number(summary_text):
    m = re.search(r"Gallery\s+(\d+)", summary_text or "")
    return int(m.group(1)) if m else None


def fetch_api():
    with httpx.Client(timeout=30) as client:
        venue = client.get(f"{BASE}/", params={"lang": LANG}).json()
        features = client.get(f"{BASE}/features", params={"limit": 1000, "lang": LANG}).json()
    return venue, features["data"]


def _tile_coord_transformer(tile):
    b = mercantile.bounds(tile)
    west, south, east, north = b.west, b.south, b.east, b.north
    xr, yr = east - west, north - south

    def pt(x, y, extent):
        return [west + (x / extent) * xr, south + (y / extent) * yr]

    def walk(coords, extent):
        if isinstance(coords[0], (int, float)):
            return pt(*coords, extent=extent)
        return [walk(c, extent) for c in coords]

    return walk


def fetch_polygons(galleries):
    """For each gallery, fetch all MVT fragments across tiles and union them.

    Galleries that straddle tile boundaries have one clipped fragment per tile.
    Taking only the first fragment (the previous behavior) gave gallery 206 a
    triangle — the rest of the room was in the neighbor tile."""
    lats = [g["lat"] for g in galleries]
    lons = [g["lon"] for g in galleries]
    north, south = max(lats) + 0.0005, min(lats) - 0.0005
    east, west = max(lons) + 0.0005, min(lons) - 0.0005

    want = {g["id"] for g in galleries}
    fragments: dict = {}

    tiles = list(mercantile.tiles(west, south, east, north, TILE_ZOOM))
    with httpx.Client(timeout=30) as client:
        for t in tiles:
            url = f"{TILE_BASE}/{t.z}/{t.x}/{t.y}.pbf?lang={LANG}"
            r = client.get(url)
            if r.status_code != 200:
                continue
            dec = mapbox_vector_tile.decode(r.content)
            layer = dec.get("indoor", {})
            extent = layer.get("extent", 4096)
            walk = _tile_coord_transformer(t)
            for f in layer.get("features", []):
                p = f["properties"]
                if p.get("type") != "gallery" or p.get("lm_id") not in want:
                    continue
                geom = f["geometry"]
                if geom["type"] not in ("Polygon", "MultiPolygon"):
                    continue
                fragments.setdefault(p["lm_id"], []).append({
                    "type": geom["type"],
                    "coordinates": walk(geom["coordinates"], extent),
                })

    polys = {}
    for lm_id, parts in fragments.items():
        shapes = [shape(p).buffer(0) for p in parts]
        merged = unary_union(shapes) if len(shapes) > 1 else shapes[0]
        polys[lm_id] = mapping(merged)
    return polys


def build():
    venue, raw = fetch_api()

    floors = [
        {
            "id": f["id"],
            "short_name": f["short_name"],
            "name": flatten_lang(f["name"]),
            "level": float(f["floor"]),
            "default": f.get("default", False),
        }
        for f in venue["floors"]
    ]

    galleries = []
    amenities = []

    for f in raw:
        loc = f.get("location") or {}
        center = loc.get("center") or {}
        floor = loc.get("floor") or {}
        if not center.get("latitude"):
            continue

        info = f.get("information") or {}
        label = f.get("label") or {}
        cats = f.get("categories") or {}
        sub = (cats.get("subcategory") or {}).get("id")

        summary = flatten_lang(info.get("summary"))
        long_name = flatten_lang(info.get("long_name")).strip()
        reference = flatten_lang(label.get("reference")).strip()
        description = flatten_lang(info.get("description"))
        popup = ((f.get("media") or {}).get("popup") or {}).get("url")

        base = {
            "id": f["id"],
            "lat": center["latitude"],
            "lon": center["longitude"],
            "floor": floor.get("short_name"),
            "floor_id": floor.get("id"),
            "is_closed": f.get("is_temporarily_closed", False),
        }

        gnum = extract_gallery_number(summary)
        if gnum in ASIAN_ART_RANGE:
            galleries.append({
                **base,
                "number": gnum,
                "name": reference or long_name or summary,
                "summary": summary,
                "description": description,
                "image_url": popup,
            })
        elif sub in AMENITY_SUBCATS and base["floor"] in {"2", "3"}:
            amenities.append({
                **base,
                "type": sub,
                "name": reference or long_name or summary,
                "description": description,
            })

    galleries.sort(key=lambda g: g["number"])

    print("Fetching gallery polygons from vector tiles…")
    polys = fetch_polygons(galleries)
    for g in galleries:
        g["polygon"] = polys.get(g["id"])
    with_poly = sum(1 for g in galleries if g["polygon"])

    out = {
        "venue": {
            "name": flatten_lang(venue["name"]),
            "center": venue["center"],
            "timezone": venue["timezone"],
        },
        "floors": floors,
        "galleries": galleries,
        "amenities": amenities,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(f"Wrote {OUT}")
    print(f"  galleries: {len(galleries)} ({with_poly} with polygon)")
    print(f"  amenities: {len(amenities)}")
    print(f"  floors:    {len(floors)}")


if __name__ == "__main__":
    build()
