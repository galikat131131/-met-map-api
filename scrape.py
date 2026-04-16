"""Scrape Living Map's Met endpoint → filter to Asian Art galleries + amenities → save clean JSON."""
import json
import re
from pathlib import Path

import httpx

BASE = "https://map-api.prod.livingmap.com/v1/maps/the_met"
LANG = "en-GB"
OUT = Path(__file__).parent / "data" / "asian_art.json"

ASIAN_ART_RANGE = range(200, 254)
AMENITY_SUBCATS = {
    "toilet", "lift", "ramp", "escalator", "drinking_water",
    "cafe", "restaurant", "bar", "shop", "information",
    "cloakroom", "defibrillator", "tickets",
}


def flatten_lang(field, lang=LANG):
    """Living Map fields are [{lang, text}, ...]. Return the matching text or ''."""
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


def fetch():
    with httpx.Client(timeout=30) as client:
        venue = client.get(f"{BASE}/", params={"lang": LANG}).json()
        features = client.get(f"{BASE}/features", params={"limit": 1000, "lang": LANG}).json()
    return venue, features["data"]


def build():
    venue, raw = fetch()

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
    print(f"  galleries: {len(galleries)}")
    print(f"  amenities: {len(amenities)}")
    print(f"  floors:    {len(floors)}")


if __name__ == "__main__":
    build()
