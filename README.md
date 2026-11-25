# sky-graph: the night sky as a graph

Data pipeline behind ["The night sky as a graph"](https://dhodapkarsoham.github.io/2026-11-25-night-sky-graph/). It turns a star
catalog, the confirmed-exoplanet list and constellation stick figures into a
single-file [Relate](https://relatedb.ai) graph database, `sky.rl`.

```bash
./run.sh            # fetch (cached) -> prepare -> build -> verify
./run.sh --refresh  # re-download the source data first
relate query "MATCH (s:Star {name: 'Vega'})-[:IN]->(c) RETURN c.name" --database sky.rl
```

Needs Python 3.9+ and the Relate CLI v1.3.0 (`curl -sSfL https://relatedb.ai/install.sh | bash`),
which is used to checkpoint the bundle. Data licenses: see [ATTRIBUTION.md](ATTRIBUTION.md).

## Pipeline

| Step | Script | Output |
|---|---|---|
| 1 fetch | `pipeline/fetch.py` | `data/raw/` + `MANIFEST.json` (URL, time, sha256). HYG is checked against its published hash. |
| 2 prepare | `pipeline/prepare.py` | `data/processed/*.json`, plus `prepare_report.json` with counts and match statistics |
| 3 build | `pipeline/build.py` | `sky.rl`. Built to a temp file, every node/relationship count checked against the processed data, then swapped in. |
| 4 verify | `pipeline/verify.py` | `data/verify_results.json` + a printed summary |
| 5 export | `pipeline/export_web.py` | `web/sky-graph.json`: a compact copy of the graph for the blog's in-browser sky map |

The browser runs [`@relate/relate-wasm`](https://www.npmjs.com/package/@relate/relate-wasm), which opens only
in-memory or OPFS databases and cannot read a native `.rl` bundle, so the page loads
`web/sky-graph.json` (2 MB, ~0.7 MB gzipped) into an in-memory Relate database instead
of shipping the 49 MB `sky.rl`. Copy it to the blog's `assets/sky-graph/` after a rebuild.

## Model

```
(:Star)-[:IN]->(:Constellation)
(:Star)-[:LINE_TO {con}]->(:Star)        stick figures (743 segments)
(:Star)-[:NEAR {ly}]->(:Star)            stored once per pair; query undirected
(:Planet)-[:ORBITS]->(:Star)
(:Planet)-[:FOUND_BY]->(:Mission)        discovery facility
(:Planet)-[:DETECTED_VIA]->(:Method)     discovery method
```

| | Count | Key | Notes |
|---|---:|---|---|
| Star | 16,846 | `id` (`hyg-<id>` or `nasa-<host>`) | flags `naked_eye`, `neighborhood`, `is_host`; `x_ly/y_ly/z_ly` equatorial, Sun at origin |
| Planet | 6,473 | `name` | archive values as reported (null if missing) + `features` |
| Constellation | 88 | `abbr` | |
| Mission | 73 | `name` | `locale`: Space / Ground / Multiple Locales |
| Method | 11 | `name` | |
| NEAR | 11,688 | | both stars ≤ 100 ly from the Sun, pair ≤ 10 ly apart |

**Which stars are in the graph:** naked-eye stars (HYG V ≤ 6.5), every
exoplanet host, and every HYG star within 100 ly of the Sun. The last group
was added beyond the original spec. With only naked-eye stars and hosts, the
Sun's 10 ly neighborhood is too sparse for any route to TRAPPIST-1.

**Host cross-match:** NASA hosts are matched to HYG by HIP number (803), then
by HD number (53), then by position (51: within 30″ with magnitude and
distance checks, widened to 5′ for stars within 100 ly because of proper
motion). The other 3,958 hosts, mostly faint Kepler/TESS/microlensing stars,
become `nasa-*` stars. Their constellation comes from astropy's IAU
boundaries.

**`Planet.features`** is a 5-d vector `[radius, mass, period, eq_temp,
insolation]`. Radius, mass, period and insolation are log10. Each feature is
min–max scaled to its 1st–99th percentile and clipped to [0, 1].
Normalization parameters and Earth's vector are in
`data/processed/feature_norm.json`. Gaps are filled physically before
scaling, and the filled fields are listed in `features_imputed` (401
planets):
- period ↔ semi-major axis via Kepler's third law
- insolation from T_eq or from stellar L/a²
- T_eq = 278.6 K · S^¼
- radius ↔ mass via Chen & Kipping (2017)

328 planets still lack inputs and have no vector. The vector index
`planet_features` uses euclidean similarity.

## Verification results (2026-11-25 data)

**1. Sun → TRAPPIST-1, jumps ≤ 6 ly: no route.** Only 5 stars are reachable
from the Sun with 6 ly hops: the three α Centauri stars, Barnard's Star and
Ross 154 (9.7 ly). The smallest possible longest jump is **8.21 ly**.
At ≤ 8.5 ly, the shortest route is 19 jumps and 124.4 ly:
Sun → Barnard's Star → Ross 154 → Lacaille 8760 → GJ 876 → … → TRAPPIST-1.
It detours out to ~56 ly through Aquarius and Pisces before coming back in
to 40.5 ly. HYG is incomplete for faint M dwarfs, so a complete census
(e.g. Gaia DR3) would likely give a shorter route with smaller jumps.

> Relate 1.3.0 note: `SHORTEST 1 (a)-[r:NEAR WHERE r.ly <= $max]-+(b)` is used
> for the 6 ly case. With a per-hop predicate, though, the search time grows
> exponentially with depth (~7 s at 8 hops, timeout past 10). The 19-jump
> route is therefore found by pulling the qualifying `NEAR` edges with Cypher
> and running Dijkstra in Python. Every hop is then confirmed against the
> graph. Unfiltered `SHORTEST` is instant.

**2. Top Earth-like planets with naked-eye hosts** (euclidean similarity to
Earth's feature vector; the vector index returns the same ranking):
1. τ Cet h (Cetus, 0.939)
2. τ Cet f (0.938)
3. HD 20794 e (82 G. Eri, Eridanus, 0.920)
4. HD 20794 d
5. HD 20794 f
6. HD 219134 f (Cassiopeia)
7. HD 219134 d
8. τ Cet g
9. HD 136352 d (Lupus)
10. HD 20794 b

The vector weights all five features equally, period included. That
favors temperate, roughly Earth-sized planets around Sun-like stars, but
it is not a habitability index.

**3. Planet counts:**
- By constellation: Cygnus 1,683 and Lyra 1,062 (the Kepler field), then
  Draco 319, Sagittarius 239 (microlensing toward the bulge) and Virgo 201.
  All 88 constellations have at least one planet.
- By method per decade: Transit dominates the 2010s (3,078); in the 2020s
  Transit has 1,662, RV 430 and microlensing 206.

The full tables, including a constellation × method × decade breakdown, are
in `data/verify_results.json`.
