"""Step 4: run the three verification queries against sky.rl.

Writes data/verify_results.json and prints a readable summary.

Query 1 note (Relate 1.3.0): `SHORTEST 1 (a)-[r:NEAR WHERE r.ly <= $max]-+(b)`
is correct Cypher, and it runs instantly when no route exists (the reachable
set is tiny). But the per-hop predicate is evaluated by enumerating paths, not
by pruning the breadth-first search, so its cost grows exponentially with hop
depth (0.25 s at 6 hops, ~7 s at 8, >40 s at 10). For the route that does
exist we therefore pull the qualifying NEAR edges with one Cypher query, run
Dijkstra/BFS in Python, and confirm the returned route hop-by-hop in Cypher.
"""
from __future__ import annotations

import heapq
import json
import math
import time
from collections import defaultdict, deque
from pathlib import Path

import relate

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sky.rl"
NORM = ROOT / "data" / "processed" / "feature_norm.json"
OUT = ROOT / "data" / "verify_results.json"

SUN, TARGET = "Sun", "TRAPPIST-1"
STRICT_MAX_LY = 6.0

Q1_SHORTEST = """
MATCH (sun:Star {name: $from})
MATCH (t:Star {name: $to})
MATCH p = SHORTEST 1 (sun)-[r:NEAR WHERE r.ly <= $max]-+(t)
RETURN [n IN nodes(p) | n.name] AS route,
       [x IN relationships(p) | x.ly] AS jumps_ly,
       length(p) AS jumps,
       reduce(total = 0.0, x IN relationships(p) | total + x.ly) AS total_ly
"""

Q1_REACHABLE = """
MATCH (sun:Star {name: $from})
MATCH (sun)(()-[r:NEAR]-() WHERE r.ly <= $max){1,8}(x:Star)
WHERE x <> sun
RETURN count(DISTINCT x) AS reachable, max(x.dist_ly) AS farthest_from_sun_ly,
       collect(DISTINCT x.name) AS stars
"""

Q1_EDGES = """
MATCH (a:Star)-[r:NEAR]->(b:Star)
WHERE r.ly <= $max
RETURN a.id AS a, b.id AS b, r.ly AS ly
"""

Q1_CONFIRM = """
UNWIND range(0, size($ids) - 2) AS i
MATCH (a:Star {id: $ids[i]})-[r:NEAR]-(b:Star {id: $ids[i + 1]})
OPTIONAL MATCH (b)-[:IN]->(c:Constellation)
RETURN i + 1 AS jump, a.name AS from_star, b.name AS to_star, r.ly AS ly,
       round(b.dist_ly, 2) AS to_dist_from_sun_ly, b.spect AS to_spectral_type,
       c.name AS to_constellation
ORDER BY jump
"""

Q2_EARTHLIKE = """
MATCH (p:Planet)-[:ORBITS]->(s:Star {naked_eye: true})-[:IN]->(c:Constellation)
WHERE p.features IS NOT NULL
WITH p, s, c, vector.similarity.euclidean(p.features, $earth) AS similarity
RETURN p.name AS planet, s.name AS host_star, s.mag AS host_mag,
       round(s.dist_ly, 1) AS host_dist_ly, c.name AS constellation,
       round(similarity, 4) AS similarity,
       p.radius_earth AS radius_earth, p.mass_earth AS mass_earth,
       p.period_days AS period_days, p.eq_temp_k AS eq_temp_k,
       p.insolation AS insolation, p.features_imputed AS imputed
ORDER BY similarity DESC, planet
LIMIT 10
"""

# Same ranking through the vector index (exact top-k scan), as a cross-check.
Q2_INDEX = """
CALL db.index.vector.queryNodes('planet_features', $k, $earth) YIELD node, score
MATCH (node)-[:ORBITS]->(:Star {naked_eye: true})-[:IN]->(:Constellation)
RETURN node.name AS planet, round(score, 4) AS score
ORDER BY score DESC, planet
LIMIT 10
"""

Q3_BY_CONSTELLATION = """
MATCH (p:Planet)-[:ORBITS]->(:Star)-[:IN]->(c:Constellation)
RETURN c.abbr AS abbr, c.name AS constellation, count(p) AS planets
ORDER BY planets DESC, constellation
"""

Q3_METHOD_BY_DECADE = """
MATCH (p:Planet)-[:DETECTED_VIA]->(m:Method)
RETURN p.disc_decade AS decade, m.name AS method, count(p) AS planets
ORDER BY decade, planets DESC, method
"""

# The same counts combined, for the page's small-multiples (JSON only).
Q3_COMBINED = """
MATCH (c:Constellation)<-[:IN]-(:Star)<-[:ORBITS]-(p:Planet)-[:DETECTED_VIA]->(m:Method)
RETURN c.abbr AS abbr, p.disc_decade AS decade, m.name AS method, count(p) AS planets
ORDER BY abbr, decade, method
"""


def rows(db, q, params=None):
    return [r.data() for r in db.execute(q, params or {})]


def graph_from_edges(edges):
    adj = defaultdict(list)
    for e in edges:
        adj[e["a"]].append((e["b"], e["ly"]))
        adj[e["b"]].append((e["a"], e["ly"]))
    return adj


def dijkstra(adj, src, dst):
    dist, prev, pq = {src: 0.0}, {}, [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            break
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            if d + w < dist.get(v, math.inf):
                dist[v], prev[v] = d + w, u
                heapq.heappush(pq, (d + w, v))
    return unwind(prev, src, dst) if dst in dist else None


def bfs(adj, src, dst):
    prev, q, seen = {}, deque([src]), {src}
    while q:
        u = q.popleft()
        if u == dst:
            return unwind(prev, src, dst)
        for v, w in sorted(adj[u], key=lambda x: x[1]):  # ties -> shorter jumps
            if v not in seen:
                seen.add(v); prev[v] = u; q.append(v)
    return None


def unwind(prev, src, dst):
    path = [dst]
    while path[-1] != src:
        path.append(prev[path[-1]])
    return path[::-1]


def minimax_limit(edges, src, dst):
    """Smallest possible 'longest jump' on any route (bottleneck path)."""
    adj = graph_from_edges(edges)
    best, pq = {src: 0.0}, [(0.0, src)]
    while pq:
        b, u = heapq.heappop(pq)
        if u == dst:
            return b
        if b > best[u]:
            continue
        for v, w in adj[u]:
            nb = max(b, w)
            if nb < best.get(v, math.inf):
                best[v] = nb
                heapq.heappush(pq, (nb, v))
    return None


def query1(db):
    ids = {r["name"]: r["id"] for r in rows(db, "MATCH (s:Star) WHERE s.name IN [$a, $b] RETURN s.name AS name, s.id AS id",
                                           {"a": SUN, "b": TARGET})}
    out = {}
    t = time.time()
    strict = rows(db, Q1_SHORTEST, {"from": SUN, "to": TARGET, "max": STRICT_MAX_LY})
    reach = rows(db, Q1_REACHABLE, {"from": SUN, "max": STRICT_MAX_LY})[0]
    out["strict"] = {"max_jump_ly": STRICT_MAX_LY, "route": strict[0] if strict else None,
                     "reachable_from_sun": reach, "seconds": round(time.time() - t, 2)}

    all_edges = rows(db, Q1_EDGES, {"max": 10.0})
    bottleneck = minimax_limit(all_edges, ids[SUN], ids[TARGET])
    limit = math.ceil(bottleneck * 2) / 2  # next half light-year
    edges = rows(db, Q1_EDGES, {"max": limit})
    adj = graph_from_edges(edges)
    relaxed = {"bottleneck_ly": round(bottleneck, 3), "max_jump_ly": limit}
    for label, fn in [("least_distance", dijkstra), ("fewest_jumps", bfs)]:
        path = fn(adj, ids[SUN], ids[TARGET])
        hops = rows(db, Q1_CONFIRM, {"ids": path})
        assert len(hops) == len(path) - 1 and all(h["ly"] <= limit for h in hops), "route not confirmed in graph"
        relaxed[label] = {"jumps": len(hops), "total_ly": round(sum(h["ly"] for h in hops), 2), "hops": hops}
    out["relaxed"] = relaxed
    return out


def main():
    norm = json.loads(NORM.read_text())
    earth = norm["_earth_features"]
    db = relate.connect(str(DB_PATH))
    try:
        results = {"query1_route": query1(db)}
        q2 = rows(db, Q2_EARTHLIKE, {"earth": earth})
        q2_index = rows(db, Q2_INDEX, {"earth": earth, "k": 10000})
        assert [r["planet"] for r in q2] == [r["planet"] for r in q2_index], "vector index disagrees with brute force"
        results["query2_earthlike"] = {"earth_features": earth, "feature_order": norm["_order"],
                                       "similarity": "vector.similarity.euclidean = 1/(1+d^2)",
                                       "index_agrees": True, "rows": q2}
        results["query3_counts"] = {
            "by_constellation": rows(db, Q3_BY_CONSTELLATION),
            "by_method_and_decade": rows(db, Q3_METHOD_BY_DECADE),
            "by_constellation_method_decade": rows(db, Q3_COMBINED),
        }
        total = rows(db, "MATCH (p:Planet) RETURN count(p) AS n")[0]["n"]
        for key in ("by_constellation", "by_method_and_decade", "by_constellation_method_decade"):
            assert sum(x["planets"] for x in results["query3_counts"][key]) == total, key
    finally:
        db.close()
    OUT.write_text(json.dumps(results, indent=2))
    report(results)


def report(r):
    q1 = r["query1_route"]
    s = q1["strict"]
    print(f"\n[1] Sun -> TRAPPIST-1, jumps <= {s['max_jump_ly']} ly")
    if s["route"]:
        print("    route:", " -> ".join(s["route"]["route"]))
    else:
        rs = s["reachable_from_sun"]
        print(f"    no route. Only {rs['reachable']} stars are reachable from the Sun "
              f"(farthest {rs['farthest_from_sun_ly']:.1f} ly out): {', '.join(rs['stars'])}")
    rl = q1["relaxed"]
    print(f"    smallest workable max jump: {rl['bottleneck_ly']} ly -> rerun at <= {rl['max_jump_ly']} ly")
    for label in ("least_distance", "fewest_jumps"):
        x = rl[label]
        print(f"    {label}: {x['jumps']} jumps, {x['total_ly']} ly")
        for h in x["hops"]:
            print(f"      {h['jump']:>2}. {h['from_star']:<22} -> {h['to_star']:<22} {h['ly']:>6.2f} ly  "
                  f"({h['to_dist_from_sun_ly']} ly from Sun, {h['to_constellation']})")

    print("\n[2] Top 10 Earth-like planets with naked-eye hosts (vector index agrees)")
    for i, p in enumerate(r["query2_earthlike"]["rows"], 1):
        print(f"    {i:>2}. {p['planet']:<14} {p['host_star']:<12} V={p['host_mag']:<5} {p['constellation']:<11} "
              f"sim={p['similarity']}  R={p['radius_earth']} M={p['mass_earth']} P={p['period_days']}d "
              f"Teq={p['eq_temp_k']}K S={p['insolation']}")

    q3 = r["query3_counts"]
    print(f"\n[3a] Planets by constellation (top 10; {len(q3['by_constellation'])} constellations have planets)")
    for c in q3["by_constellation"][:10]:
        print(f"    {c['constellation']:<18} {c['planets']}")
    print("\n[3b] Planets by discovery method per decade")
    methods = sorted({x["method"] for x in q3["by_method_and_decade"]})
    decades = sorted({x["decade"] for x in q3["by_method_and_decade"]})
    grid = {(x["method"], x["decade"]): x["planets"] for x in q3["by_method_and_decade"]}
    print("    " + f"{'method':<30}" + "".join(f"{str(d) + 's':>7}" for d in decades))
    for m in methods:
        print("    " + f"{m:<30}" + "".join(f"{grid.get((m, d), 0):>7}" for d in decades))
    print(f"\nresults -> {OUT}")


if __name__ == "__main__":
    main()
