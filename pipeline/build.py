"""Step 3: load data/processed/*.json into the Relate database sky.rl.

Model
  (:Star)-[:IN]->(:Constellation)
  (:Star)-[:LINE_TO {con}]->(:Star)          constellation stick figures
  (:Star)-[:NEAR {ly}]->(:Star)              both stars <= 100 ly from the Sun, pair <= 10 ly apart
                                              (one direction stored; query it undirected)
  (:Planet)-[:ORBITS]->(:Star)
  (:Planet)-[:FOUND_BY]->(:Mission)          discovery facility
  (:Planet)-[:DETECTED_VIA]->(:Method)       discovery method
  Planet.features                              normalized 5-d vector, vector index `planet_features`

The build writes to a temporary file and swaps it into place, so a failed
build never leaves a half-loaded sky.rl behind.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import relate

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
DB_PATH = ROOT / "sky.rl"
BATCH = 2000

SCHEMA = [
    "CREATE CONSTRAINT star_id FOR (s:Star) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT planet_name FOR (p:Planet) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT constellation_abbr FOR (c:Constellation) REQUIRE c.abbr IS UNIQUE",
    "CREATE CONSTRAINT mission_name FOR (m:Mission) REQUIRE m.name IS UNIQUE",
    "CREATE CONSTRAINT method_name FOR (m:Method) REQUIRE m.name IS UNIQUE",
    "CREATE INDEX star_name FOR (s:Star) ON (s.name)",
    "CREATE INDEX star_naked_eye FOR (s:Star) ON (s.naked_eye)",
    "CREATE INDEX planet_decade FOR (p:Planet) ON (p.disc_decade)",
    "CREATE VECTOR INDEX planet_features FOR (p:Planet) ON (p.features) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 5, `vector.similarity_function`: 'euclidean'}}",
]

LOADS = [
    ("constellations", "UNWIND $rows AS r CREATE (c:Constellation) SET c = r"),
    ("methods", "UNWIND $rows AS r CREATE (m:Method) SET m = r"),
    ("missions", "UNWIND $rows AS r CREATE (m:Mission) SET m = r"),
    ("stars", "UNWIND $rows AS r CREATE (s:Star) SET s = r"),
    ("stars", """
        UNWIND $rows AS r
        WITH r WHERE r.con IS NOT NULL
        MATCH (s:Star {id: r.id}), (c:Constellation {abbr: r.con})
        CREATE (s)-[:IN]->(c)"""),
    ("planets", """
        UNWIND $rows AS r
        MATCH (s:Star {id: r.star_id})
        CREATE (p:Planet)
        SET p = r
        REMOVE p.star_id
        CREATE (p)-[:ORBITS]->(s)"""),
    ("planets", """
        UNWIND $rows AS r
        MATCH (p:Planet {name: r.name}), (m:Method {name: r.method})
        CREATE (p)-[:DETECTED_VIA]->(m)"""),
    ("planets", """
        UNWIND $rows AS r
        WITH r WHERE r.facility IS NOT NULL
        MATCH (p:Planet {name: r.name}), (m:Mission {name: r.facility})
        CREATE (p)-[:FOUND_BY]->(m)"""),
    ("line_to", """
        UNWIND $rows AS r
        MATCH (a:Star {id: r.from}), (b:Star {id: r.to})
        CREATE (a)-[:LINE_TO {con: r.con}]->(b)"""),
    ("near", """
        UNWIND $rows AS r
        MATCH (a:Star {id: r.from}), (b:Star {id: r.to})
        CREATE (a)-[:NEAR {ly: r.ly}]->(b)"""),
    # Convenience counts for the page.
    (None, """
        MATCH (s:Star)
        SET s.planet_count = COUNT { (s)<-[:ORBITS]-(:Planet) }"""),
]

CHECKS = {
    "Star": "MATCH (n:Star) RETURN count(n) AS n",
    "Planet": "MATCH (n:Planet) RETURN count(n) AS n",
    "Constellation": "MATCH (n:Constellation) RETURN count(n) AS n",
    "Mission": "MATCH (n:Mission) RETURN count(n) AS n",
    "Method": "MATCH (n:Method) RETURN count(n) AS n",
    "ORBITS": "MATCH ()-[r:ORBITS]->() RETURN count(r) AS n",
    "IN": "MATCH ()-[r:IN]->() RETURN count(r) AS n",
    "LINE_TO": "MATCH ()-[r:LINE_TO]->() RETURN count(r) AS n",
    "FOUND_BY": "MATCH ()-[r:FOUND_BY]->() RETURN count(r) AS n",
    "DETECTED_VIA": "MATCH ()-[r:DETECTED_VIA]->() RETURN count(r) AS n",
    "NEAR": "MATCH ()-[r:NEAR]->() RETURN count(r) AS n",
}


def load(name):
    return json.loads((PROC / f"{name}.json").read_text())


def main():
    tmp = ROOT / "sky.building.rl"  # must end in .rl to be a single-file bundle
    if tmp.exists():
        shutil.rmtree(tmp) if tmp.is_dir() else tmp.unlink()
    db = relate.connect(str(tmp))
    try:
        for stmt in SCHEMA:
            db.execute(stmt)
        cache = {}
        for name, cypher in LOADS:
            t = time.time()
            if name is None:
                db.execute(cypher)
                print(f"  derived         {time.time() - t:5.1f}s")
                continue
            rows = cache.setdefault(name, load(name))
            for i in range(0, len(rows), BATCH):
                db.execute(cypher, {"rows": rows[i:i + BATCH]})
            print(f"  {name:<15} {len(rows):>6} rows  {time.time() - t:5.1f}s")

        counts = {k: db.execute(q).single()["n"] for k, q in CHECKS.items()}
        expected = {
            "Star": len(cache["stars"]), "Planet": len(cache["planets"]),
            "Constellation": len(cache["constellations"]), "Mission": len(cache["missions"]),
            "Method": len(cache["methods"]), "ORBITS": len(cache["planets"]),
            "IN": sum(1 for s in cache["stars"] if "con" in s),
            "LINE_TO": len(cache["line_to"]), "NEAR": len(cache["near"]),
            "DETECTED_VIA": sum(1 for p in cache["planets"] if "method" in p),
            "FOUND_BY": sum(1 for p in cache["planets"] if "facility" in p),
        }
        bad = {k: (counts[k], expected[k]) for k in expected if counts[k] != expected[k]}
        print(json.dumps(counts))
        if bad:
            raise SystemExit(f"count mismatch (got, expected): {bad}")
        db.flush()
    finally:
        db.close()
    # Fold the WAL into the snapshot so the bundle is compact and self-contained.
    relate_cli = shutil.which("relate") or str(Path.home() / ".local/bin/relate")
    subprocess.run([relate_cli, "query", ".checkpoint", "--database", str(tmp)], check=True)

    if DB_PATH.exists():
        shutil.rmtree(DB_PATH) if DB_PATH.is_dir() else DB_PATH.unlink()
    os.replace(tmp, DB_PATH)
    print(f"wrote {DB_PATH}")


if __name__ == "__main__":
    main()
