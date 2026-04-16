# Met Asian Art Map API

Read-only API over Living Map's Met data, filtered to the Asian Art wing
(galleries 200–253). Designed for a PWA consumer with GPS.

## Run locally

```sh
pip install -r requirements.txt
python scrape.py          # refresh data/asian_art.json from upstream
uvicorn app.main:app --reload
```

Swagger UI at http://127.0.0.1:8000/docs

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Meta + counts |
| GET | `/floors` | All floors |
| GET | `/galleries` | List galleries (`?floor=2`, `?include_closed=false`) |
| GET | `/galleries/{n}` | One gallery by number |
| GET | `/search?q=` | Substring match over name + description |
| GET | `/nearby?lat=&lon=&floor=&radius_m=` | Galleries within radius |
| GET | `/locate?lat=&lon=&floor=` | Nearest gallery — "which gallery am I in?" |
| GET | `/nearest-amenity?type=&lat=&lon=&floor=` | Nearest toilet/lift/cafe/etc. |
| POST | `/route` | `{from_gallery, to_gallery}` → steps + distance |
| GET | `/amenity-types` | Counts by amenity type |

## PWA integration notes

- **Floor cannot be inferred from GPS.** The PWA must let the user pick a floor
  and pass it as `?floor=` to `/locate` and `/nearby`.
- **Indoor GPS is noisy** (20–50 m error inside a stone building). Treat
  `/locate` as a best-guess, not ground truth.
- `/route` runs a local fallback (straight-line + floor change step) and also
  attempts to proxy Living Map's upstream `/route` endpoint. If `upstream` is
  non-null in the response, use those polyline coords for a smoother path.
- CORS is `*` — lock it down to your PWA origin before real users hit it.

## Deploy to Fly.io

```sh
brew install flyctl       # if not already
flyctl auth login
flyctl launch --no-deploy --copy-config   # keep the included fly.toml
flyctl deploy
```

Your API will be live at `https://met-asian-art-api.fly.dev` (or whatever name
you chose).

## Future work

See `TODO.md` — biggest item is decoding Living Map's vector tiles to do real
point-in-polygon containment instead of nearest-centroid for `/locate`.
