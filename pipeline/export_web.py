"""Step 5: export a compact copy of the graph for the browser.

The Relate WASM build (@relate/relate-wasm) opens only in-memory or OPFS
databases; it cannot read a native .rl bundle, and sky.rl is ~49 MB anyway.
So the page fetches this columnar JSON (~3 MB, ~1 MB gzipped on GitHub Pages),
draws the sky map from it straight away, and loads the same model into an
in-memory Relate database in a Web Worker for the live queries.

Everything is read back out of sky.rl (not data/processed) so the browser
graph is exactly the verified one.

Output: web/sky-graph.json
"""
from __future__ import annotations

import json
from pathlib import Path

import relate

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sky.rl"
NORM = ROOT / "data" / "processed" / "feature_norm.json"
OUT = ROOT / "web" / "sky-graph.json"


def r(x, nd):
    return None if x is None else round(float(x), nd)


def main():
    norm = json.loads(NORM.read_text())
    db = relate.connect(str(DB_PATH))
    try:
        cons = [x.data() for x in db.execute(
            "MATCH (c:Constellation) RETURN c.abbr AS abbr, c.name AS name ORDER BY abbr")]
        methods = [x["name"] for x in db.execute("MATCH (m:Method) RETURN m.name AS name ORDER BY name")]
        missions = [x.data() for x in db.execute(
            "MATCH (m:Mission) RETURN m.name AS name, m.locale AS locale ORDER BY name")]
        stars = [x.data() for x in db.execute("""
            MATCH (s:Star)
            RETURN s.id AS id, s.name AS name, s.ra_deg AS ra, s.dec_deg AS dec, s.mag AS mag,
                   s.dist_ly AS dist, s.x_ly AS x, s.y_ly AS y, s.z_ly AS z, s.con AS con,
                   s.spect AS spect, s.naked_eye AS eye, s.neighborhood AS hood, s.is_host AS host
            ORDER BY s.naked_eye DESC, s.mag""")]
        idx = {s["id"]: i for i, s in enumerate(stars)}
        planets = [x.data() for x in db.execute("""
            MATCH (p:Planet)-[:ORBITS]->(s:Star)
            OPTIONAL MATCH (p)-[:DETECTED_VIA]->(m:Method)
            OPTIONAL MATCH (p)-[:FOUND_BY]->(f:Mission)
            RETURN p.name AS name, s.id AS star, p.radius_earth AS r, p.mass_earth AS m,
                   p.period_days AS P, p.eq_temp_k AS teq, p.insolation AS S,
                   p.disc_year AS year, m.name AS method, f.name AS facility, p.features AS f
            ORDER BY name""")]
        lines = [x.data() for x in db.execute(
            "MATCH (a:Star)-[l:LINE_TO]->(b:Star) RETURN a.id AS a, b.id AS b, l.con AS con")]
        near = [x.data() for x in db.execute(
            "MATCH (a:Star)-[n:NEAR]->(b:Star) RETURN a.id AS a, b.id AS b, n.ly AS ly")]
    finally:
        db.close()

    mi = {m: i for i, m in enumerate(methods)}
    fi = {m["name"]: i for i, m in enumerate(missions)}
    out = {
        "version": 1,
        "about": "Night-sky graph export. Star data: HYG v4.4 (CC BY-SA 4.0). Planets: NASA Exoplanet "
                 "Archive PSCompPars (doi:10.26133/NEA13). Constellation lines: d3-celestial (BSD-3-Clause).",
        "earth": norm["_earth_features"],
        "featureOrder": norm["_order"],
        "constellations": [[c["abbr"], c["name"]] for c in cons],
        "methods": methods,
        "missions": [[m["name"], m.get("locale")] for m in missions],
        "starColumns": ["name", "ra", "dec", "mag", "dist", "x", "y", "z", "con", "spect", "flags"],
        "stars": [[
            s["name"], r(s["ra"], 3), r(s["dec"], 3), r(s["mag"], 2), r(s["dist"], 2),
            *((r(s["x"], 2), r(s["y"], 2), r(s["z"], 2)) if s["hood"] else (None, None, None)),
            s["con"], s["spect"],
            (1 if s["eye"] else 0) | (2 if s["hood"] else 0) | (4 if s["host"] else 0),
        ] for s in stars],
        "planetColumns": ["name", "star", "radius", "mass", "period", "teq", "insol", "year",
                          "method", "facility", "features"],
        "planets": [[
            p["name"], idx[p["star"]], r(p["r"], 3), r(p["m"], 3), r(p["P"], 4), r(p["teq"], 1),
            r(p["S"], 4), p["year"], mi.get(p["method"]), fi.get(p["facility"]),
            [round(v, 4) for v in p["f"]] if p["f"] else None,
        ] for p in planets],
        "lines": [[idx[l["a"]], idx[l["b"]], l["con"]] for l in lines],
        "near": [[idx[n["a"]], idx[n["b"]], n["ly"]] for n in near],
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB): {len(stars)} stars, {len(planets)} planets, "
          f"{len(lines)} lines, {len(near)} near")


if __name__ == "__main__":
    main()
