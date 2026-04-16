"""Compute the gallery adjacency cache by probing Living Map routes.

Two galleries are "adjacent" if the Living Map shortest-path polyline
between them never enters the interior of a third gallery's polygon —
i.e. a visitor can walk directly from one to the other without passing
through another room.

We cache the result in data/adjacency.json so downstream consumers
(fake-data seeding, route recommendations) can ask "which galleries can
the user plausibly walk to next?" without calling Living Map again.

Rate-limited and resumable: writes to adjacency.json after every probe
so a crash or Ctrl-C doesn't waste upstream calls.

Usage:
    python3 scripts/compute_adjacency.py                 # probe all pairs
    python3 scripts/compute_adjacency.py --max-dist 50   # prune harder
    python3 scripts/compute_adjacency.py --rebuild       # ignore cache
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import httpx
import shapely
from shapely.geometry import LineString, Point, shape

REPO_ROOT = Path(__file__).parent.parent
DATA_PATH = REPO_ROOT / "data" / "asian_art.json"
ADJ_PATH = REPO_ROOT / "data" / "adjacency.json"
LIVINGMAP_URL = "https://map-api.prod.livingmap.com/v2/route"

# Prune pairs whose centroids are farther apart than this. True neighbors
# in the Met's Asian Art wing have centroids within ~30m; we go generous.
DEFAULT_MAX_CENTROID_M = 80.0

# Sleep between Living Map calls. Their TOS and our conscience both want this.
SLEEP_BETWEEN_CALLS_S = 0.45


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def extract_polyline(upstream: dict, start_floor: str) -> list[tuple[float, float]]:
    """Flatten routeGeoJson features into a single list of (lon, lat) points
    belonging to the starting floor. We only care about segments a visitor
    actually walks on that floor — stair/lift transitions are out of scope."""
    pts: list[tuple[float, float]] = []
    for segment in upstream.get("segments", []) or []:
        for feature in segment.get("routeGeoJson", []) or []:
            props = feature.get("properties") or {}
            floor_num = props.get("floorNumber")
            floor = str(floor_num) if floor_num is not None else None
            if floor is not None and floor != start_floor:
                continue
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            # Both LineString [[lon,lat],...] and Point [lon,lat] are possible.
            if coords and isinstance(coords[0], (list, tuple)):
                for c in coords:
                    pts.append((c[0], c[1]))
            elif len(coords) >= 2 and isinstance(coords[0], (int, float)):
                pts.append((coords[0], coords[1]))
    return pts


def call_living_map(a_lm_id: str, b_lm_id: str, client: httpx.Client) -> dict | None:
    try:
        r = client.post(
            LIVINGMAP_URL,
            json={
                "from": {"lmId": a_lm_id},
                "to": {"lmId": b_lm_id},
                "project": "the_met",
            },
        )
        if r.status_code >= 400:
            print(f"  living map {r.status_code}", file=sys.stderr)
            return None
        return r.json()
    except httpx.HTTPError as err:
        print(f"  living map error: {err}", file=sys.stderr)
        return None


def path_crosses_other_gallery(
    path_lonlat: list[tuple[float, float]],
    a_num: int,
    b_num: int,
    polygons_by_num: dict[int, shapely.geometry.base.BaseGeometry],
    tree: shapely.STRtree,
    poly_nums: list[int],
) -> int | None:
    """Return the gallery number of the first "interior" gallery the path
    passes through (other than a or b), or None if the path is clean.

    We trim off a small buffer near the endpoints because the polyline
    naturally starts/ends inside each endpoint's polygon.
    """
    if len(path_lonlat) < 2:
        return None
    line = LineString(path_lonlat)
    total_len = line.length  # degrees, fine for relative trimming
    if total_len == 0:
        return None
    # Trim the first/last 10% of the path so endpoint-interior points don't count.
    trim = total_len * 0.10
    interior = line.interpolate(trim)
    end_interior = line.interpolate(total_len - trim)
    if interior.equals(end_interior):
        return None

    # Sample ~40 points along the trimmed polyline and test containment.
    n_samples = 40
    for i in range(1, n_samples):
        frac = i / n_samples
        d = trim + frac * (total_len - 2 * trim)
        pt = line.interpolate(d)
        candidate_idxs = tree.query(pt, predicate="contains")
        for idx in candidate_idxs:
            num = poly_nums[int(idx)]
            if num != a_num and num != b_num:
                return num
    return None


def load_cache() -> dict:
    if not ADJ_PATH.exists():
        return {"generated_at": None, "pairs": {}, "probes": {}}
    try:
        return json.load(ADJ_PATH.open())
    except json.JSONDecodeError:
        return {"generated_at": None, "pairs": {}, "probes": {}}


def save_cache(cache: dict) -> None:
    ADJ_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ADJ_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, separators=(",", ":"), indent=2))
    tmp.replace(ADJ_PATH)


def pair_key(a: int, b: int) -> str:
    x, y = sorted((a, b))
    return f"{x}-{y}"


def build_adjacency_lists(probes: dict) -> dict[str, list[int]]:
    """Turn the symmetric pair map into per-gallery neighbor lists."""
    adj: dict[int, set[int]] = {}
    for key, entry in probes.items():
        if not entry.get("adjacent"):
            continue
        a, b = entry["from"], entry["to"]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return {str(g): sorted(ns) for g, ns in sorted(adj.items())}


def main(max_dist: float, rebuild: bool) -> None:
    data = json.load(DATA_PATH.open())
    galleries = [g for g in data["galleries"] if g.get("polygon")]
    by_num = {g["number"]: g for g in galleries}
    polygons_by_num = {g["number"]: shape(g["polygon"]) for g in galleries}
    poly_nums = list(polygons_by_num.keys())
    geoms = [polygons_by_num[n] for n in poly_nums]
    tree = shapely.STRtree(geoms)

    cache = {"generated_at": None, "pairs": {}, "probes": {}} if rebuild else load_cache()
    probes: dict = cache.setdefault("probes", {})

    pairs_to_probe: list[tuple[dict, dict]] = []
    for floor in sorted({g["floor"] for g in galleries}):
        pool = [g for g in galleries if g["floor"] == floor]
        for i, a in enumerate(pool):
            for b in pool[i + 1 :]:
                d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                if d > max_dist:
                    continue
                key = pair_key(a["number"], b["number"])
                if key in probes:
                    continue
                pairs_to_probe.append((a, b))

    already = len(probes)
    total = already + len(pairs_to_probe)
    print(f"Cached probes: {already}. New pairs to probe: {len(pairs_to_probe)}. Total: {total}")
    print(f"Estimated time @ {SLEEP_BETWEEN_CALLS_S}s/call: ~{len(pairs_to_probe) * SLEEP_BETWEEN_CALLS_S / 60:.1f} min")

    if not pairs_to_probe:
        print("Nothing to probe; rewriting adjacency lists from cache.")
    else:
        with httpx.Client(timeout=15, headers={"User-Agent": "met-map-api adjacency cache"}) as client:
            for i, (a, b) in enumerate(pairs_to_probe, 1):
                key = pair_key(a["number"], b["number"])
                upstream = call_living_map(a["id"], b["id"], client)
                if upstream is None:
                    probes[key] = {"from": a["number"], "to": b["number"], "adjacent": False, "reason": "upstream_error"}
                else:
                    path = extract_polyline(upstream, a["floor"])
                    crossed = path_crosses_other_gallery(
                        path, a["number"], b["number"], polygons_by_num, tree, poly_nums
                    )
                    meta = (upstream.get("routeMetadata") or [{}])[0]
                    distance_m = round((meta.get("totalLength") or 0) * 1000, 1)
                    probes[key] = {
                        "from": a["number"],
                        "to": b["number"],
                        "adjacent": crossed is None,
                        "distance_m": distance_m,
                        "blocked_by": crossed,
                    }
                if i % 25 == 0 or i == len(pairs_to_probe):
                    save_cache(cache)
                    print(f"  {i}/{len(pairs_to_probe)}  {key}  adjacent={probes[key].get('adjacent')}")
                time.sleep(SLEEP_BETWEEN_CALLS_S)

    cache["generated_at"] = int(time.time())
    cache["pairs"] = build_adjacency_lists(probes)
    save_cache(cache)

    total_adj = sum(1 for p in probes.values() if p.get("adjacent"))
    print(f"\nDone. {total_adj}/{len(probes)} probed pairs are adjacent.")
    print(f"Per-gallery neighbor lists: {len(cache['pairs'])} galleries.")
    print(f"Cache: {ADJ_PATH}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--max-dist", type=float, default=DEFAULT_MAX_CENTROID_M,
                   help=f"Max centroid distance (m) to consider a pair. Default {DEFAULT_MAX_CENTROID_M}.")
    p.add_argument("--rebuild", action="store_true", help="Ignore existing cache and re-probe all pairs.")
    args = p.parse_args()
    main(max_dist=args.max_dist, rebuild=args.rebuild)
