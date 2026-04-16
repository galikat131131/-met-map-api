"""Scrape Met Open Access → keep artworks in Asian Art galleries 200–253 → save compact JSON.

Companion to scrape.py. Writes data/asian_art_objects.json for main.py to load at import time.

Resumable: per-object results stream into data/.asian_art_objects.cache.jsonl as they arrive.
On restart, IDs already in the cache are skipped — safe to rerun after network drops.
Final JSON is assembled from the cache and the cache is then deleted.
"""
import asyncio
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

import httpx

BASE = "https://collectionapi.metmuseum.org/public/collection/v1"
DEPARTMENT_ID = 6  # Asian Art
GALLERY_RANGE = range(200, 254)
DATA_DIR = Path(__file__).parent / "data"
OUT = DATA_DIR / "asian_art_objects.json"
CACHE = DATA_DIR / ".asian_art_objects.cache.jsonl"
IDS_CACHE = DATA_DIR / ".asian_art_objects.ids.json"

CONCURRENCY = 1  # Met bot-filter tolerates serial ~10 req/s; any burst gets clamped
PER_WORKER_DELAY = 0.15  # ~6 req/s — comfortably below the serial ceiling
PER_REQUEST_TIMEOUT = 15.0
# No retries on 4xx/5xx — resume cache handles it. Rerun after failures.
MAX_ATTEMPTS = 1

# Optional uniform random sample of the 37k Asian Art ID list.
# Set SAMPLE_SIZE env var to cap the fetch (seed is fixed for reproducibility).
SAMPLE_SIZE = int(os.environ.get("SAMPLE_SIZE", "0")) or None
SAMPLE_SEED = int(os.environ.get("SAMPLE_SEED", "42"))
HEADERS = {"User-Agent": "met-asian-art-map-api/0.1 (+https://github.com/galikat131131/-met-map-api)"}


async def fetch_object_ids(client: httpx.AsyncClient) -> list[int]:
    """Use /objects?departmentIds=6 — the list endpoint actually filters by department.
    The /search endpoint's departmentIds filter is silently ignored (known Met API bug),
    which is why an earlier keyword-union approach returned objects from every department.
    Cached to disk; the full-dept ID list is stable between runs."""
    if IDS_CACHE.exists():
        cached = json.loads(IDS_CACHE.read_text())
        print(f"  using cached id list: {IDS_CACHE.name} ({len(cached)} ids)")
        return cached

    r = await client.get(f"{BASE}/objects", params={"departmentIds": DEPARTMENT_ID})
    r.raise_for_status()
    ids = sorted(set(r.json().get("objectIDs") or []))
    IDS_CACHE.write_text(json.dumps(ids))
    print(f"  fetched id list: {len(ids)} ids ({IDS_CACHE.name})")
    return ids


async def fetch_object(client: httpx.AsyncClient, sem: asyncio.Semaphore, oid: int) -> dict | None:
    """Fail fast. Resume cache handles any missing IDs on the next run."""
    async with sem:
        try:
            r = await client.get(f"{BASE}/objects/{oid}")
        except httpx.HTTPError:
            await asyncio.sleep(PER_WORKER_DELAY)
            return None
        await asyncio.sleep(PER_WORKER_DELAY)
        if r.status_code == 404:
            return {"object_id": oid, "_missing": True}
        if r.status_code >= 400:
            return None
        return r.json()


def compact(o: dict, gnum: int) -> dict:
    return {
        "object_id": o["objectID"],
        "gallery_number": gnum,
        "title": (o.get("title") or "").strip(),
        "artist": (o.get("artistDisplayName") or "").strip(),
        "artist_bio": (o.get("artistDisplayBio") or "").strip(),
        "culture": (o.get("culture") or "").strip(),
        "period": (o.get("period") or "").strip(),
        "dynasty": (o.get("dynasty") or "").strip(),
        "reign": (o.get("reign") or "").strip(),
        "date": (o.get("objectDate") or "").strip(),
        "date_begin": o.get("objectBeginDate"),
        "date_end": o.get("objectEndDate"),
        "medium": (o.get("medium") or "").strip(),
        "classification": (o.get("classification") or "").strip(),
        "object_name": (o.get("objectName") or "").strip(),
        "credit_line": (o.get("creditLine") or "").strip(),
        "accession_number": (o.get("accessionNumber") or "").strip(),
        "image_small": o.get("primaryImageSmall") or None,
        "image": o.get("primaryImage") or None,
        "is_highlight": bool(o.get("isHighlight")),
        "is_public_domain": bool(o.get("isPublicDomain")),
        "object_url": o.get("objectURL") or None,
    }


def parse_gallery(val) -> int | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s.isdigit():
        return None
    n = int(s)
    return n if n in GALLERY_RANGE else None


def load_cache() -> dict[int, dict]:
    """Returns object_id → cache row. Row is either a full object dict or {'object_id', '_missing': True}."""
    if not CACHE.exists():
        return {}
    out: dict[int, dict] = {}
    with CACHE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a half-written last line from a crash
            oid = row.get("objectID") or row.get("object_id")
            if oid is not None:
                out[int(oid)] = row
    return out


async def build():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=PER_REQUEST_TIMEOUT, headers=HEADERS) as client:
        print("Fetching object ID list…")
        ids = await fetch_object_ids(client)
        if not ids:
            print("No IDs returned — aborting.", file=sys.stderr)
            sys.exit(1)

        if SAMPLE_SIZE and SAMPLE_SIZE < len(ids):
            rng = random.Random(SAMPLE_SEED)
            ids = sorted(rng.sample(ids, SAMPLE_SIZE))
            print(f"  sampled {SAMPLE_SIZE} of total (seed={SAMPLE_SEED})")

        cache = load_cache()
        todo = [oid for oid in ids if oid not in cache]
        print(f"  cached={len(cache)}  todo={len(todo)}  total={len(ids)}")

        if todo:
            print(f"Fetching {len(todo)} object records (concurrency={CONCURRENCY})…")
            sem = asyncio.Semaphore(CONCURRENCY)
            cache_f = CACHE.open("a")
            done = 0
            failed = 0
            lock = asyncio.Lock()

            async def worker(oid: int):
                nonlocal done, failed
                row = await fetch_object(client, sem, oid)
                async with lock:
                    done += 1
                    if row is None:
                        failed += 1
                    else:
                        cache_f.write(json.dumps(row) + "\n")
                        cache_f.flush()
                    if done % 50 == 0 or done == len(todo):
                        print(f"  {done}/{len(todo)}  failed_this_run={failed}", flush=True)

            try:
                await asyncio.gather(*(worker(oid) for oid in todo))
            finally:
                cache_f.close()

            if failed:
                print(f"\n{failed} fetches failed transiently — rerun to pick them up.", file=sys.stderr)

    # Finalization: iterate the full cache, not just this run's ids list.
    # Keeps accumulated hits across runs; also guards against cross-run ID overlap.
    cache = load_cache()
    kept: list[dict] = []
    seen_galleries: Counter[int] = Counter()
    for oid, row in cache.items():
        if not row or row.get("_missing"):
            continue
        # Filter by real department (guards against pollution from the earlier
        # run that used /search — which silently ignores departmentIds).
        if row.get("department") != "Asian Art":
            continue
        gnum = parse_gallery(row.get("GalleryNumber"))
        if gnum is None:
            continue
        kept.append(compact(row, gnum))
        seen_galleries[gnum] += 1

    kept.sort(key=lambda x: (x["gallery_number"], not x["is_highlight"], x["object_id"]))

    highlights = sum(1 for x in kept if x["is_highlight"])
    public_domain = sum(1 for x in kept if x["is_public_domain"])

    out = {
        "source": "Met Open Access API (collectionapi.metmuseum.org)",
        "department_id": DEPARTMENT_ID,
        "gallery_range": [GALLERY_RANGE.start, GALLERY_RANGE.stop - 1],
        "counts": {
            "objects": len(kept),
            "highlights": highlights,
            "public_domain": public_domain,
            "galleries_with_objects": len(seen_galleries),
        },
        "objects": kept,
    }

    OUT.write_text(json.dumps(out, indent=2))
    size_mb = OUT.stat().st_size / (1024 * 1024)

    print("\nDone.")
    print(f"  file:       {OUT}  ({size_mb:.1f} MB)")
    print(f"  objects:    {len(kept)}")
    print(f"  highlights: {highlights}")
    print(f"  galleries:  {len(seen_galleries)} / {len(GALLERY_RANGE)}")
    top = seen_galleries.most_common(5)
    if top:
        print(f"  top galleries: {top}")

    # Only delete the cache when the full ID list was processed cleanly.
    missing_from_cache = [oid for oid in ids if oid not in cache]
    if not missing_from_cache:
        CACHE.unlink(missing_ok=True)
        print(f"  cache cleared: {CACHE.name}")
    else:
        print(f"  cache retained ({len(missing_from_cache)} ids still unfetched) — rerun to finish")


if __name__ == "__main__":
    asyncio.run(build())
