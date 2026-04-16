"""Seed data/transitions.jsonl with plausible fake heat-map data.

Popularity weights are grounded in the Met's actual Asian Art highlights,
scanned from the real gallery descriptions in data/asian_art.json:

  - Astor Court / Ming Courtyard (217, 218) — the signature space
  - Astor Forecourt (209) — the moon gate
  - Chinese Buddhist Art (206, 208) — monumental Buddhist sculpture
  - Chinese Treasury (219) and the Jade Bishop collection (222) on floor 3
  - Arts of Tibet and Nepal (252, 253) on floor 3
  - Southeast Asian sculpture (Khmer, 244-250)

Each simulated session represents one anonymous visitor: enters from a
Great-Hall-Balcony gallery, walks between galleries weighted by popularity
and proximity, occasionally switches floors. Writes JSONL records in the
same shape as POST /track/transition, so /heatmap/edges and /heatmap/galleries
will aggregate them immediately.

Usage:
    python3 scripts/seed_fake_transitions.py                 # 200 sessions, seed 42
    python3 scripts/seed_fake_transitions.py --sessions 500  # more data
    python3 scripts/seed_fake_transitions.py --append        # add to existing file
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DATA_PATH = REPO_ROOT / "data" / "asian_art.json"
ADJACENCY_PATH = REPO_ROOT / "data" / "adjacency.json"
OUT_PATH = REPO_ROOT / "data" / "transitions.jsonl"

# Keyword → multiplicative popularity boost. Matched case-insensitively against
# each gallery's name + description. Multiple matches compound.
POPULARITY_KEYWORDS: dict[str, float] = {
    # Astor Court — the Met's most iconic Asian Art space.
    "astor court": 6.0,
    "ming dynasty": 3.0,
    "courtyard": 3.0,
    "moon gate": 3.0,
    "forecourt": 2.0,
    # Monumental Buddhist art — the Sackler Gallery anchor.
    "buddhist": 3.0,
    "monumental": 2.5,
    # Floor 3 highlights.
    "treasury": 2.5,
    "jade": 4.0,
    "bishop": 2.0,
    # Tibetan / Nepalese religious art.
    "tibet": 2.5,
    "nepal": 2.0,
    "thangka": 2.0,
    # Southeast Asian sculpture (Khmer / Angkor).
    "southeast asian": 1.8,
    "khmer": 2.5,
    "cambodia": 2.0,
    # South Asian sculpture.
    "hindu": 1.6,
    "jain": 1.4,
    # Korean — under-visited IRL but has a devoted niche.
    "korean": 1.4,
    # Japanese ceramics (many galleries — per-gallery weight stays low).
    "japanese ceramics": 1.2,
    # Chinese painting strip (210-216) — moderate per-gallery.
    "chinese painting": 1.3,
    "calligraphy": 1.2,
    # Special exhibitions always draw traffic.
    "celebrating": 1.8,
}

# Visitor archetypes → (session-length range, popularity exponent).
# Higher popularity exponent = more highlight-seeking.
ARCHETYPES = [
    ("highlight", (3, 7),   1.8, 0.50),  # 50% — short visits, chase famous spots
    ("explorer",  (6, 12),  1.0, 0.35),  # 35% — medium walk, moderate bias
    ("methodical", (10, 18), 0.4, 0.15), # 15% — thorough, low popularity bias
]

# Floor-3 is genuinely under-visited in the real Asian Art wing (upstairs from
# the main floor, easy to miss). Model it as a per-session decision: most
# visitors never go up; those who do usually head back down soon after.
PROB_SESSION_VISITS_FLOOR_3 = 0.08   # chance a session ever goes upstairs (real wing is easy to miss)
PROB_GO_UP_PER_STEP = 0.08           # per-step chance of climbing, once decided
PROB_COME_BACK_DOWN = 0.55           # per-step chance of returning to floor 2


def haversine_m(a: dict, b: dict) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(a["lat"]), math.radians(b["lat"])
    dphi = math.radians(b["lat"] - a["lat"])
    dlam = math.radians(b["lon"] - a["lon"])
    x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def popularity_score(g: dict) -> float:
    text = f"{g.get('name', '')} {g.get('description', '')}".lower()
    score = 1.0
    for kw, mult in POPULARITY_KEYWORDS.items():
        if kw in text:
            score *= mult
    return score


def pick_archetype(rng: random.Random) -> tuple[str, tuple[int, int], float]:
    r = rng.random()
    acc = 0.0
    for name, length_range, pop_exp, prob in ARCHETYPES:
        acc += prob
        if r < acc:
            return name, length_range, pop_exp
    name, length_range, pop_exp, _ = ARCHETYPES[-1]
    return name, length_range, pop_exp


def simulate_session(
    rng: random.Random,
    galleries: list[dict],
    pop: dict[int, float],
    adjacency: dict[int, list[int]],
    now_ms: int,
    session_i: int,
) -> list[dict]:
    archetype, length_range, pop_exp = pick_archetype(rng)
    path_length = rng.randint(*length_range)
    will_visit_floor_3 = rng.random() < PROB_SESSION_VISITS_FLOOR_3

    # Visit was "some time in the last 30 days", to spread timestamps out.
    start_offset_ms = rng.randint(0, 30 * 24 * 3600 * 1000)
    t = now_ms - start_offset_ms

    # Entry: Great Hall Balcony (200-205) most of the time, Chinese Buddhist
    # Art (206, 208, 209) sometimes, rarely deep inside the wing.
    entry_weights = []
    entry_pool = [g for g in galleries if g["floor"] == "2"]
    for g in entry_pool:
        n = g["number"]
        if 200 <= n <= 205:
            entry_weights.append(6.0)
        elif n in (206, 208, 209):
            entry_weights.append(3.0)
        elif 210 <= n <= 218:
            entry_weights.append(1.2)
        else:
            entry_weights.append(0.4)
    current = rng.choices(entry_pool, weights=entry_weights, k=1)[0]

    records: list[dict] = []
    visited = {current["number"]}

    by_num = {g["number"]: g for g in galleries}
    for _ in range(path_length):
        if current["floor"] == "2":
            # Only consider going up if this session was pre-picked to.
            want_switch = will_visit_floor_3 and rng.random() < PROB_GO_UP_PER_STEP
            target_floor = "3" if want_switch else "2"
        else:
            # On floor 3, bias toward heading back down fairly quickly.
            want_switch = rng.random() < PROB_COME_BACK_DOWN
            target_floor = "2" if want_switch else "3"

        if target_floor == current["floor"]:
            # Same-floor walking: only jump to a physically adjacent gallery.
            # Anything else would imply teleporting across other rooms.
            neighbor_nums = adjacency.get(current["number"], [])
            pool = [by_num[n] for n in neighbor_nums
                    if n in by_num and n not in visited]
        else:
            # Floor change: lifts/stairs connect the two floors at fixed spots,
            # so the visitor can realistically arrive at any open gallery on
            # the new floor. We let the proximity weight shape where they go.
            pool = [g for g in galleries
                    if g["number"] not in visited and g["floor"] == target_floor]
        # If adjacency dead-ends (all neighbors visited), end the session here
        # rather than teleporting — matches how a real visitor would bail.
        if not pool:
            break

        weights = []
        for g in pool:
            d = haversine_m(current, g)
            # Proximity: 1/(1+d/20)^2 — strong locality preference. At museum
            # scale most gallery centroids are within 5-50m of their neighbors.
            prox = 1.0 / (1.0 + d / 20.0) ** 2
            # Cross-floor penalty (lifts are real friction).
            if g["floor"] != current["floor"]:
                prox *= 0.4
            weights.append((pop[g["number"]] ** pop_exp) * prox)

        nxt = rng.choices(pool, weights=weights, k=1)[0]

        # Gap between transitions: 45s to 6min, with archetype affecting dwell.
        # TODO: once we track dwell time explicitly, read it from here.
        gap_ms = rng.randint(45_000, 360_000)
        t += gap_ms

        records.append({
            "session_id": f"fake-{session_i:04d}-{rng.randrange(10 ** 10):010d}",
            "from_gallery": current["number"],
            "to_gallery": nxt["number"],
            "floor_from": current["floor"],
            "floor_to": nxt["floor"],
            "client_ts": t,
            "server_ts": t + rng.randint(30, 400),
            # Mostly polygon hits; some corridor fallbacks — matches what the
            # real /locate endpoint returns in practice.
            "locate_method": "polygon" if rng.random() < 0.78 else "nearest-centroid",
        })
        current = nxt
        visited.add(current["number"])

    # A session_id should be stable within a session; re-stamp all records with
    # the same id (the loop above varies per-record for convenience).
    session_id = f"fake-{session_i:04d}-{rng.randrange(10 ** 10):010d}"
    for r in records:
        r["session_id"] = session_id
    return records


def main(sessions: int, seed: int, out: Path, append: bool) -> None:
    rng = random.Random(seed)
    with DATA_PATH.open() as f:
        data = json.load(f)
    galleries = [g for g in data["galleries"] if not g.get("is_closed")]
    pop = {g["number"]: popularity_score(g) for g in galleries}
    # Load adjacency cache: maps gallery number → list of neighbor numbers.
    # Built by scripts/compute_adjacency.py; fail loudly if missing so we
    # don't silently fall back to generating physically impossible paths.
    if not ADJACENCY_PATH.exists():
        raise SystemExit(
            f"Missing adjacency cache at {ADJACENCY_PATH}. "
            f"Run scripts/compute_adjacency.py first."
        )
    adj_raw = json.load(ADJACENCY_PATH.open())
    adjacency: dict[int, list[int]] = {
        int(k): v for k, v in adj_raw.get("pairs", {}).items()
    }
    print(f"Loaded adjacency: {len(adjacency)} galleries with neighbor lists.")

    # Print the popularity ranking so the bias is auditable.
    ranked = sorted(galleries, key=lambda g: -pop[g["number"]])
    print("Top 10 galleries by popularity weight:")
    for g in ranked[:10]:
        print(f"  {g['number']:>3} f{g['floor']}  weight={pop[g['number']]:>6.2f}  {g['name']}")
    print()

    now_ms = int(time.time() * 1000)
    all_records: list[dict] = []
    for i in range(sessions):
        all_records.extend(simulate_session(rng, galleries, pop, adjacency, now_ms, i))
    # Interleave by server_ts so the JSONL looks like it was written live.
    all_records.sort(key=lambda r: r["server_ts"])

    out.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with out.open(mode) as f:
        for r in all_records:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")

    print(f"Wrote {len(all_records)} transitions from {sessions} sessions to {out} ({'append' if append else 'overwrite'})")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sessions", type=int, default=200)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=OUT_PATH)
    p.add_argument("--append", action="store_true", help="Append to existing JSONL instead of overwriting")
    args = p.parse_args()
    main(sessions=args.sessions, seed=args.seed, out=args.out, append=args.append)
