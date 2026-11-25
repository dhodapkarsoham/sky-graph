"""Step 2: clean, cross-match and normalize the raw data into data/processed/.

Outputs (JSON lists of plain dicts, ready for UNWIND):
  stars.json, planets.json, constellations.json, missions.json, methods.json,
  line_to.json, near.json, feature_norm.json, prepare_report.json
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord, get_constellation
import astropy.units as u
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

LY_PER_PC = 3.261563777
NAKED_EYE_MAG = 6.5
NEIGHBORHOOD_LY = 100.0   # NEAR is built among stars within this distance of the Sun
NEAR_MAX_LY = 10.0        # ...linking pairs at most this far apart
HYG_UNKNOWN_DIST_PC = 100000  # HYG sentinel for "no distance"

# Positional cross-match of NASA hosts to HYG (when no HIP/HD id matches).
XMATCH_ARCSEC = 30.0
XMATCH_MAX_DMAG = 1.0
XMATCH_MAX_DDIST = 0.25   # relative
# Nearby stars can move arcminutes between HYG's J2000 epoch and the archive's
# Gaia-epoch positions, so hosts within the neighborhood get a wider radius
# with a tighter distance check.
XMATCH_NEARBY_ARCSEC = 300.0
XMATCH_NEARBY_MAX_DDIST = 0.10

# Stick-figure vertex -> star match radius.
LINE_MATCH_DEG = 0.25

FEATURES = ["radius_earth", "mass_earth", "period_days", "eq_temp_k", "insolation"]
LOG_FEATURES = {"radius_earth", "mass_earth", "period_days", "insolation"}
# Earth reference, using the same zero-albedo T_eq convention the imputation uses.
EARTH = {"radius_earth": 1.0, "mass_earth": 1.0, "period_days": 365.256,
         "eq_temp_k": 278.6, "insolation": 1.0}


def clean(v):
    """NaN/inf/numpy scalars -> JSON-friendly Python values."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if not math.isfinite(float(v)) else float(v)
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def record(d: dict) -> dict:
    return {k: c for k, v in d.items() if (c := clean(v)) is not None}


def unit_vectors(ra_deg, dec_deg):
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.c_[np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)]


def catalog_number(s, prefix):
    if not isinstance(s, str):
        return None
    m = re.match(rf"^{prefix}\s+(\d+)", s.strip())
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------- stars
def load_hyg() -> pd.DataFrame:
    h = pd.read_csv(RAW / "hyg_v44.csv.gz", low_memory=False)
    h["ra_deg"] = h["ra"] * 15.0
    h["dist_ly"] = np.where(h["dist"] < HYG_UNKNOWN_DIST_PC, h["dist"] * LY_PER_PC, np.nan)
    for c in ("x", "y", "z"):
        h[f"{c}_ly"] = np.where(h["dist"] < HYG_UNKNOWN_DIST_PC, h[c] * LY_PER_PC, np.nan)
    return h


def hyg_display_name(r) -> str:
    if isinstance(r.proper, str) and r.proper.strip():
        return r.proper.strip()
    con = r.con if isinstance(r.con, str) else ""
    if isinstance(r.bayer, str) and r.bayer.strip():
        return f"{r.bayer.strip()} {con}".strip()
    if pd.notna(r.flam):
        return f"{int(r.flam)} {con}".strip()
    if pd.notna(r.hip):
        return f"HIP {int(r.hip)}"
    if pd.notna(r.hd):
        return f"HD {int(r.hd)}"
    if isinstance(r.gl, str) and r.gl.strip():
        return r.gl.strip()
    return f"HYG {int(r.id)}"


def match_hosts(hosts: pd.DataFrame, h: pd.DataFrame) -> dict:
    """hostname -> HYG row index. One HYG star per host (first claim wins)."""
    by_hip = {int(v): i for i, v in h["hip"].dropna().items()}
    by_hd = {}
    for i, v in h["hd"].dropna().items():
        by_hd.setdefault(int(v), i)  # keep the first (primary) component
    tree = cKDTree(unit_vectors(h["ra_deg"].values, h["dec"].values))
    def chord(arcsec):
        return 2 * math.sin(math.radians(arcsec / 3600) / 2)

    matches, claimed, how = {}, set(), {"hip": 0, "hd": 0, "position": 0, "none": 0}
    # Hosts without a component suffix claim first, so "HD 1 A"/"HD 1 B" don't steal "HD 1".
    order = hosts.assign(_has_comp=hosts.hostname.str.match(r".*\s[A-Z]$")).sort_values("_has_comp")
    for _, r in order.iterrows():
        idx, kind = None, None
        hip = catalog_number(r.hip_name, "HIP")
        hd = catalog_number(r.hd_name, "HD")
        if hip is not None and hip in by_hip:
            idx, kind = by_hip[hip], "hip"
        elif hd is not None and hd in by_hd:
            idx, kind = by_hd[hd], "hd"
        elif pd.notna(r.ra) and pd.notna(r.dec):
            nearby = pd.notna(r.sy_dist) and r.sy_dist * LY_PER_PC <= NEIGHBORHOOD_LY
            radius = XMATCH_NEARBY_ARCSEC if nearby else XMATCH_ARCSEC
            max_dd = XMATCH_NEARBY_MAX_DDIST if nearby else XMATCH_MAX_DDIST
            here = unit_vectors([r.ra], [r.dec])[0]
            cands = tree.query_ball_point(here, chord(radius))
            cands.sort(key=lambda j: np.linalg.norm(tree.data[j] - here))  # closest first
            for j in cands:
                c = h.loc[j]
                wide = np.linalg.norm(tree.data[j] - here) > chord(XMATCH_ARCSEC)
                if pd.notna(r.sy_vmag) and pd.notna(c.mag) and abs(r.sy_vmag - c.mag) > XMATCH_MAX_DMAG:
                    continue
                if pd.isna(r.sy_dist) or pd.isna(c.dist_ly):
                    if wide:
                        continue  # the widened ring needs a distance check
                elif abs(c.dist_ly / (r.sy_dist * LY_PER_PC) - 1) > (max_dd if wide else XMATCH_MAX_DDIST):
                    continue
                idx, kind = j, "position"
                break
        if idx is not None and idx not in claimed:
            matches[r.hostname] = idx
            claimed.add(idx)
            how[kind] += 1
        else:
            how["none"] += 1
    return matches, how


def build_stars(h: pd.DataFrame, planets: pd.DataFrame):
    hosts = planets.groupby("hostname", as_index=False).first()
    matches, how = match_hosts(hosts, h)
    host_of_hyg = {idx: host for host, idx in matches.items()}

    naked = h["mag"] <= NAKED_EYE_MAG
    near = h["dist_ly"] <= NEIGHBORHOOD_LY
    keep = naked | near | h.index.isin(list(matches.values()))
    sel = h[keep]

    stars = []
    for i, r in sel.iterrows():
        host = host_of_hyg.get(i)
        name = r.proper if isinstance(r.proper, str) and r.proper.strip() else (host or hyg_display_name(r))
        stars.append(record({
            "id": f"hyg-{int(r.id)}",
            "name": "Sun" if r.id == 0 else name,
            "host_name": host,
            "source": "HYG v4.4",
            "hyg_id": int(r.id), "hip": r.hip, "hd": r.hd, "hr": r.hr, "gl": r.gl,
            "proper": r.proper, "bayer": r.bayer, "flam": r.flam,
            "ra_deg": r.ra_deg, "dec_deg": r.dec,
            "dist_ly": 0.0 if r.id == 0 else r.dist_ly,
            "mag": r.mag, "absmag": r.absmag, "spect": r.spect, "ci": r.ci, "lum": r.lum,
            "x_ly": r.x_ly, "y_ly": r.y_ly, "z_ly": r.z_ly,
            "con": r.con,
            "naked_eye": bool(r.mag <= NAKED_EYE_MAG) if r.id != 0 else False,
            "neighborhood": bool(r.dist_ly <= NEIGHBORHOOD_LY) or r.id == 0,
            "is_host": host is not None,
        }))

    # Hosts not in HYG become stars from the archive's system parameters.
    unmatched = hosts[~hosts.hostname.isin(matches.keys())].copy()
    cons = get_constellation(SkyCoord(unmatched.ra.values * u.deg, unmatched.dec.values * u.deg), short_name=True)
    unmatched["con"] = cons
    for _, r in unmatched.iterrows():
        d = r.sy_dist * LY_PER_PC if pd.notna(r.sy_dist) else np.nan
        xyz = unit_vectors([r.ra], [r.dec])[0] * d if pd.notna(d) else [np.nan] * 3
        stars.append(record({
            "id": "nasa-" + re.sub(r"[^A-Za-z0-9]+", "-", r.hostname).strip("-").lower(),
            "name": r.hostname, "host_name": r.hostname,
            "source": "NASA Exoplanet Archive",
            "hip": catalog_number(r.hip_name, "HIP"), "hd": catalog_number(r.hd_name, "HD"),
            "gaia_dr3": r.gaia_dr3_id,
            "ra_deg": r.ra, "dec_deg": r.dec, "dist_ly": d,
            "mag": r.sy_vmag, "spect": r.st_spectype,
            "x_ly": xyz[0], "y_ly": xyz[1], "z_ly": xyz[2],
            "con": r.con,
            "naked_eye": bool(pd.notna(r.sy_vmag) and r.sy_vmag <= NAKED_EYE_MAG),
            "neighborhood": bool(pd.notna(d) and d <= NEIGHBORHOOD_LY),
            "is_host": True,
        }))
    # Host-star physical parameters used for imputation.
    host_phys = hosts.set_index("hostname")[["st_teff", "st_rad", "st_mass"]]
    return stars, how, host_phys


# ----------------------------------------------------------------------------- planets
def radius_from_mass(m):
    """Chen & Kipping (2017) mean relation, in Earth units."""
    if m < 2.04:
        return m ** 0.279
    if m < 132:
        return 0.808 * m ** 0.589
    return 17.74 * m ** -0.044


def mass_from_radius(r):
    if r < 1.23:
        return r ** (1 / 0.279)
    if r < 14.26:
        return (r / 0.808) ** (1 / 0.589)
    return 317.8  # Jupiter-size: radius is degenerate in mass; use one Jupiter mass


def impute(row, phys) -> tuple[dict, list]:
    v = {
        "radius_earth": row.pl_rade, "mass_earth": row.pl_bmasse, "period_days": row.pl_orbper,
        "eq_temp_k": row.pl_eqt, "insolation": row.pl_insol,
    }
    v = {k: (float(x) if pd.notna(x) and x > 0 else None) for k, x in v.items()}
    sma = row.pl_orbsmax if pd.notna(row.pl_orbsmax) and row.pl_orbsmax > 0 else None
    st_mass = phys.st_mass if pd.notna(phys.st_mass) and phys.st_mass > 0 else None
    imputed = []

    if v["period_days"] is None and sma and st_mass:
        v["period_days"] = 365.256 * math.sqrt(sma ** 3 / st_mass); imputed.append("period_days")
    if sma is None and v["period_days"] and st_mass:
        sma = (st_mass * (v["period_days"] / 365.256) ** 2) ** (1 / 3)
    if v["insolation"] is None and v["eq_temp_k"]:
        v["insolation"] = (v["eq_temp_k"] / 278.6) ** 4; imputed.append("insolation")
    if v["insolation"] is None and sma and pd.notna(phys.st_teff) and pd.notna(phys.st_rad):
        v["insolation"] = phys.st_rad ** 2 * (phys.st_teff / 5772) ** 4 / sma ** 2; imputed.append("insolation")
    if v["eq_temp_k"] is None and v["insolation"]:
        v["eq_temp_k"] = 278.6 * v["insolation"] ** 0.25; imputed.append("eq_temp_k")
    if v["radius_earth"] is None and v["mass_earth"]:
        v["radius_earth"] = radius_from_mass(v["mass_earth"]); imputed.append("radius_earth")
    if v["mass_earth"] is None and v["radius_earth"]:
        v["mass_earth"] = mass_from_radius(v["radius_earth"]); imputed.append("mass_earth")
    return v, imputed


def transform(name, x):
    return math.log10(x) if name in LOG_FEATURES else x


def build_planets(planets: pd.DataFrame, host_phys: pd.DataFrame, host_to_star: dict):
    rows, filled = [], []
    for _, r in planets.iterrows():
        v, imputed = impute(r, host_phys.loc[r.hostname])
        filled.append(v)
        rows.append((r, v, imputed))

    # Min-max on the 1st..99th percentile of each (log-)feature, clipped to [0, 1].
    norm = {}
    for f in FEATURES:
        xs = np.array([transform(f, v[f]) for v in filled if v[f] is not None])
        lo, hi = np.percentile(xs, [1, 99])
        norm[f] = {"transform": "log10" if f in LOG_FEATURES else "linear", "lo": float(lo), "hi": float(hi)}

    def vec(values):
        if any(values[f] is None for f in FEATURES):
            return None
        out = []
        for f in FEATURES:
            n = norm[f]
            out.append(round(min(1.0, max(0.0, (transform(f, values[f]) - n["lo"]) / (n["hi"] - n["lo"]))), 6))
        return out

    out = []
    for r, v, imputed in rows:
        decade = int(r.disc_year // 10 * 10) if pd.notna(r.disc_year) else None
        locale = r.disc_locale.title() if isinstance(r.disc_locale, str) else None
        out.append(record({
            "name": r.pl_name, "host_name": r.hostname, "star_id": host_to_star[r.hostname],
            # As reported by the archive (null when missing):
            "radius_earth": r.pl_rade, "mass_earth": r.pl_bmasse, "period_days": r.pl_orbper,
            "sma_au": r.pl_orbsmax, "eq_temp_k": r.pl_eqt, "insolation": r.pl_insol,
            "disc_year": int(r.disc_year) if pd.notna(r.disc_year) else None,
            "disc_decade": decade,
            "method": r.discoverymethod, "facility": r.disc_facility, "facility_locale": locale,
            # Feature vector (normalized, with physically-imputed gaps listed):
            "features": vec(v),
            "features_imputed": imputed or None,
        }))
    norm["_order"] = FEATURES
    norm["_earth_features"] = vec(EARTH)
    norm["_earth_reference"] = EARTH
    return out, norm


# ----------------------------------------------------------------------------- constellations
def build_constellations(stars: list[dict]):
    meta = json.loads((RAW / "d3celestial_constellations.json").read_text())["features"]
    cons = {}
    for f in meta:  # d3-celestial splits Serpens into Caput/Cauda; the IAU counts one.
        p = f["properties"]
        name = "Serpens" if f["id"] == "Ser" else p["name"]
        cons[f["id"]] = record({"abbr": f["id"], "name": name, "genitive": p.get("gen"),
                                "rank": int(p["rank"]) if p.get("rank") else None})
    cons = list(cons.values())

    # Stick figures: snap each vertex to the nearest naked-eye HYG star.
    eye = [s for s in stars if s.get("naked_eye") and s["source"] == "HYG v4.4"]
    tree = cKDTree(unit_vectors([s["ra_deg"] for s in eye], [s["dec_deg"] for s in eye]))
    chord = 2 * math.sin(math.radians(LINE_MATCH_DEG) / 2)
    lines = json.loads((RAW / "d3celestial_constellations.lines.json").read_text())["features"]
    edges, seen, misses, vertices = [], set(), 0, 0
    for f in lines:
        for strip in f["geometry"]["coordinates"]:
            ids = []
            for lon, lat in strip:
                vertices += 1
                d, j = tree.query(unit_vectors([lon % 360], [lat])[0])
                if d <= chord:
                    ids.append(eye[j]["id"])
                else:
                    ids.append(None); misses += 1
            for a, b in zip(ids, ids[1:]):
                if a and b and a != b and (key := (f["id"], *sorted((a, b)))) not in seen:
                    seen.add(key)
                    edges.append({"from": a, "to": b, "con": f["id"]})
    return cons, edges, {"vertices": vertices, "unmatched_vertices": misses, "edges": len(edges)}


# ----------------------------------------------------------------------------- NEAR
def build_near(stars: list[dict]):
    pts = [s for s in stars if s.get("neighborhood") and "x_ly" in s]
    xyz = np.array([[s["x_ly"], s["y_ly"], s["z_ly"]] for s in pts])
    tree = cKDTree(xyz)
    pairs = tree.query_pairs(NEAR_MAX_LY, output_type="ndarray")
    d = np.linalg.norm(xyz[pairs[:, 0]] - xyz[pairs[:, 1]], axis=1)
    edges = [{"from": pts[a]["id"], "to": pts[b]["id"], "ly": round(float(x), 3)}
             for (a, b), x in zip(pairs, d) if x > 0]  # x == 0 means duplicate positions
    return edges, len(pts)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    h = load_hyg()
    planets = pd.read_csv(RAW / "nasa_pscomppars.csv")
    stars, how, host_phys = build_stars(h, planets)
    host_to_star = {s["host_name"]: s["id"] for s in stars if s.get("host_name")}
    planet_rows, norm = build_planets(planets, host_phys, host_to_star)
    cons, line_to, line_report = build_constellations(stars)
    near, near_n = build_near(stars)

    # A facility's locale can vary per planet ("Multiple Locales"); keep the most common.
    locales = {}
    for p in planet_rows:
        if p.get("facility"):
            locales.setdefault(p["facility"], []).append(p.get("facility_locale"))
    mission_rows = [record({"name": n, "locale": max(set(ls), key=lambda x: (ls.count(x), x or ""))})
                    for n, ls in sorted(locales.items())]
    method_rows = [{"name": m} for m in sorted({p["method"] for p in planet_rows if p.get("method")})]

    report = {
        "stars": len(stars),
        "stars_naked_eye": sum(s["naked_eye"] for s in stars),
        "stars_neighborhood": sum(s["neighborhood"] for s in stars),
        "stars_hosts": sum(s["is_host"] for s in stars),
        "host_match": how,
        "planets": len(planet_rows),
        "planets_with_features": sum("features" in p for p in planet_rows),
        "planets_with_imputed_features": sum("features_imputed" in p and "features" in p for p in planet_rows),
        "constellations": len(cons),
        "line_to": line_report,
        "near": {"stars": near_n, "edges": len(near), "max_ly": NEAR_MAX_LY},
        "missions": len(mission_rows), "methods": len(method_rows),
    }
    for name, data in [("stars", stars), ("planets", planet_rows), ("constellations", cons),
                       ("missions", mission_rows), ("methods", method_rows), ("line_to", line_to),
                       ("near", near), ("feature_norm", norm), ("prepare_report", report)]:
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=None if isinstance(data, list) else 2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
