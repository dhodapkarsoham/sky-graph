"""Step 1: download the three source datasets into data/raw/.

Each download is recorded in data/raw/MANIFEST.json (URL, retrieval time,
sha256, size) so the build is reproducible and attributable.
Licenses and citations live in ATTRIBUTION.md at the project root.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

# HYG v4.4 (Codeberg is the canonical home since 2024; the GitHub repo is frozen).
HYG_URL = "https://codeberg.org/astronexus/hyg/media/branch/main/data/hyg/CURRENT/hyg_v44.csv.gz"
HYG_SHA256 = "00b349893b9a53106dd488d8371e8d2fa586043e500bb3cdb8bff3931682197d"  # from the LFS pointer

# NASA Exoplanet Archive, Planetary Systems Composite Parameters (one row per
# confirmed planet, best available value per column).
NASA_COLUMNS = [
    "pl_name", "hostname", "hd_name", "hip_name", "gaia_dr3_id",
    "sy_snum", "sy_pnum", "ra", "dec", "sy_dist", "sy_vmag",
    "st_spectype", "st_teff", "st_rad", "st_mass",
    "pl_rade", "pl_bmasse", "pl_orbper", "pl_orbsmax", "pl_eqt", "pl_insol",
    "discoverymethod", "disc_facility", "disc_locale", "disc_year",
]
NASA_QUERY = f"select {','.join(NASA_COLUMNS)} from pscomppars"
NASA_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync?" + urllib.parse.urlencode(
    {"query": NASA_QUERY, "format": "csv"}
)

# d3-celestial (Olaf Frohn), pinned through jsDelivr to a fixed commit tag.
D3C = "https://cdn.jsdelivr.net/gh/ofrohn/d3-celestial@v0.7.32/data/"
D3C_FILES = ["constellations.lines.json", "constellations.json"]

SOURCES = [
    ("hyg_v44.csv.gz", HYG_URL, HYG_SHA256),
    ("nasa_pscomppars.csv", NASA_URL, None),
    *[(f"d3celestial_{f}", D3C + f, None) for f in D3C_FILES],
    ("d3celestial_LICENSE", "https://cdn.jsdelivr.net/gh/ofrohn/d3-celestial@v0.7.32/LICENSE", None),
]


def download(url: str, dest: Path) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "sky-graph-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        if r.status != 200:
            raise RuntimeError(f"{url}: HTTP {r.status}")
        data = r.read()
    dest.write_bytes(data)
    return data


def main(force: bool = False) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for name, url, expected in SOURCES:
        dest = RAW / name
        if dest.exists() and not force:
            print(f"skip   {name} (exists; --force to refresh)")
            continue
        print(f"fetch  {name}")
        data = download(url, dest)
        digest = hashlib.sha256(data).hexdigest()
        if expected and digest != expected:
            dest.unlink()
            raise SystemExit(f"{name}: sha256 mismatch ({digest} != {expected})")
        manifest[name] = {
            "url": url,
            "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "sha256": digest,
            "bytes": len(data),
        }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
